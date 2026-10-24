from __future__ import annotations

import anyio
import numpy as np
import sys
from pathlib import Path
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.acor.scorer import Candidate
from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.java_bridge import ClassInfo, MethodInfo
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.vector_store import VectorStore
from app.api.schemas import ScoreBreakdown
from app.mcp import server as mcp_module


class EmptyVectorStore(VectorStore):
    def add(self, ids, vectors, metadatas):
        return None

    def search(self, query_vector, top_k):
        return []

    def count(self):
        return 1

    def ids(self):
        return set()

    def remove(self, ids):
        return None

    def clear(self):
        return None


class FakeEmbeddingModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return np.ones((4,), dtype=np.float32)


def _index(tmp_path):
    source = tmp_path / "Sample.java"
    source.write_text("class Sample { void changedMethod() {} }", encoding="utf-8")
    cls = ClassInfo("Sample", "Sample.java", "Sample", "", 1, 1, [])
    method = MethodInfo(
        "Sample#changedMethod()",
        "Sample",
        "changedMethod",
        "changedMethod()",
        "Sample.java",
        1,
        1,
        [],
    )
    return RepoIndex(
        repo_id="mcp-test",
        repo_root=tmp_path,
        fingerprint="fixture",
        call_graph=CallGraph.build([method], []),
        dependency_graph=DependencyGraph.build([cls], []),
        symbol_index=SymbolIndex.build([cls], [method]),
        vector_store=EmptyVectorStore(),
        num_files=1,
        num_classes=1,
        num_methods=1,
        num_call_edges=0,
        file_token_counts={"Sample.java": 20},
    )


async def _call_tools():
    client_to_server_send, client_to_server_receive = anyio.create_memory_object_stream(0)
    server_to_client_send, server_to_client_receive = anyio.create_memory_object_stream(0)
    lowlevel_server = mcp_module.mcp_server._lowlevel_server
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            lowlevel_server.run,
            client_to_server_receive,
            server_to_client_send,
            lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(server_to_client_receive, client_to_server_send) as client:
            await client.initialize()
            tools = await client.list_tools()
            root_cause = await client.call_tool(
                "codelens_root_cause",
                {"repo_id": "mcp-test", "query": "why does changedMethod fail?"},
            )
            change_impact = await client.call_tool(
                "codelens_change_impact",
                {
                    "repo_id": "mcp-test",
                    "query": "change changedMethod",
                    "changed_symbol": "changedMethod",
                },
            )
            invalid = await client.call_tool(
                "codelens_root_cause",
                {"repo_id": "mcp-test", "query": "why?", "token_budget": 0},
            )
            graph = await client.call_tool(
                "codelens_graph_inspect",
                {"repo_id": "mcp-test", "symbol": "changedMethod"},
            )
        tasks.cancel_scope.cancel()
    return tools, root_cause, change_impact, invalid, graph


def test_mcp_stdio_protocol_exposes_and_executes_both_tools(tmp_path, monkeypatch):
    settings = Settings(acor_use_intent_weights=False)
    repo_index = _index(tmp_path)
    candidate = Candidate(
        method_id="Sample#changedMethod()",
        class_id="Sample",
        name="changedMethod",
        kind="method",
        file_path="Sample.java",
        start_line=1,
        end_line=1,
        snippet="void changedMethod() {}",
        token_count=6,
        raw_semantic_score=0.9,
    )
    candidate.score = ScoreBreakdown(
        semantic=0.9,
        call_graph=1.0,
        dependency=1.0,
        symbol=1.0,
        composite=0.95,
    )
    observed_symbols: list[str | None] = []

    class FakeLLM:
        def generate(self, system_prompt: str, user_prompt: str):
            assert "BEGIN REPOSITORY EXCERPT" in user_prompt
            return type(
                "Response",
                (),
                {
                    "text": (
                        '{"summary":"Diagnosis verified.",'
                        '"root_cause":"Evidence-backed test diagnosis.",'
                        '"causal_chain":[],"suggested_fix":null,'
                        '"limitations":[],"unresolved_questions":[]}'
                    ),
                    "latency_ms": 1.0,
                    "prompt_tokens": 24,
                    "completion_tokens": 10,
                    "total_tokens": 34,
                },
            )()

    monkeypatch.setattr(mcp_module, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_module, "_get_repo_index", lambda *_: repo_index)
    monkeypatch.setattr(mcp_module, "get_embedding_model", lambda _: FakeEmbeddingModel())
    monkeypatch.setattr(mcp_module, "get_llm_client", lambda _: FakeLLM())

    def fake_candidates(*, changed_symbol=None, **_):
        observed_symbols.append(changed_symbol)
        return [candidate]

    monkeypatch.setattr(mcp_module, "get_candidates", fake_candidates)
    tools, root_cause, change_impact, invalid, graph = anyio.run(_call_tools)

    assert {tool.name for tool in tools.tools} == {
        "codelens_root_cause",
        "codelens_change_impact",
        "codelens_graph_inspect",
    }
    assert not root_cause.is_error
    assert not change_impact.is_error
    assert "Diagnosis verified." in root_cause.content[0].text
    assert "Diagnosis verified." in change_impact.content[0].text
    assert "changedMethod" in change_impact.content[0].text
    assert root_cause.structured_content["response"]["structured_answer"]["summary"] == (
        "Diagnosis verified."
    )
    assert root_cause.structured_content["response"]["evidence"][0]["method_id"] == (
        "Sample#changedMethod()"
    )
    assert root_cause.structured_content["response"]["metrics"]["actual_provider_total_tokens"] == 34
    assert not graph.is_error
    assert graph.structured_content["resolved_symbol"] == "Sample#changedMethod()"
    assert observed_symbols == [None, "changedMethod"]
    assert invalid.is_error
    assert "positive integer" in invalid.content[0].text


async def _call_real_stdio_server(repo_root: Path):
    backend_root = Path(__file__).resolve().parents[1]
    server_script = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script), str(repo_root)],
        cwd=str(backend_root),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            tools = await client.list_tools()
            root_cause = await client.call_tool(
                "codelens_root_cause",
                {"repo_id": "stdio-fixture", "query": "why does changedMethod fail?"},
            )
            change_impact = await client.call_tool(
                "codelens_change_impact",
                {
                    "repo_id": "stdio-fixture",
                    "query": "change changedMethod",
                    "changed_symbol": "changedMethod",
                },
            )
    return tools, root_cause, change_impact


def test_mcp_tools_work_over_spawned_stdio_client_process(tmp_path):
    tools, root_cause, change_impact = anyio.run(
        _call_real_stdio_server,
        tmp_path / "stdio-repository",
    )

    assert {tool.name for tool in tools.tools} >= {
        "codelens_root_cause",
        "codelens_change_impact",
    }
    assert not root_cause.is_error
    assert not change_impact.is_error
    assert root_cause.structured_content["response"]["structured_answer"]["summary"] == (
        "stdio analysis passed."
    )
    assert change_impact.structured_content["response"]["evidence"][0]["method_id"] == (
        "Sample#changedMethod()"
    )
