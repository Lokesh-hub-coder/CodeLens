"""Regression tests for verification batch:
1. naive_baseline_tokens == sum of whole-file token counts for files touched by candidates
2. total_context_tokens == count_tokens(context_text)
3. CallGraph.callers_within_hops respects direction and hop limit
4. candidate_pipeline: root-cause path dedups by method_id; change-impact path seeds from callers
"""
from pathlib import Path
import os
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import routes
from app.config import Settings, get_settings
from app.core import candidate_pipeline
from app.core.query_service import _INTENT_WEIGHTS
from app.core import repo_index as repo_index_module
from app.core.candidate_pipeline import (
    generate_change_impact_candidates,
    generate_root_cause_candidates,
    get_candidates,
)
from app.core.repo_index import RepoIndex, RepoRootMissing
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.prompts import STRUCTURED_SYSTEM_PROMPT
from app.parsing.java_bridge import (
    CallEdge,
    ClassInfo,
    ImportEdge,
    MethodInfo,
    ParsedRepo,
)
from app.parsing.repo_walker import read_snippet
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.query_understanding import QueryAnalysis, QueryIntentLabel
from app.retrieval.tokenizer import count_tokens
from app.retrieval.vector_store import QdrantVectorStore, SearchResult, VectorStore
from app.main import app


# ---------------------------------------------------------------------------
# Fakes for EmbeddingModel and VectorStore
# ---------------------------------------------------------------------------

class FakeEmbeddingModel:
    def __init__(self, dim: int = 128):
        self.dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), self.dim), dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return np.ones((self.dim,), dtype=np.float32)


class FakeVectorStore(VectorStore):
    def __init__(self, search_results: list[SearchResult] | None = None):
        self._results = search_results or []
        self._items: list[tuple[str, np.ndarray, dict]] = []

    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None:
        for i, v, m in zip(ids, vectors, metadatas):
            self._items = [item for item in self._items if item[0] != i]
            self._items.append((i, v, m))

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        return self._results[:top_k]

    def count(self) -> int:
        return len(self._items) or len(self._results)

    def ids(self) -> set[str]:
        return {item[0] for item in self._items} | {
            result.metadata["method_id"] for result in self._results
        }

    def remove(self, ids: list[str]) -> None:
        removed = set(ids)
        self._items = [item for item in self._items if item[0] not in removed]
        self._results = [
            result
            for result in self._results
            if result.metadata["method_id"] not in removed
        ]

    def clear(self) -> None:
        self._items.clear()
        self._results.clear()


@pytest.fixture
def query_client(tmp_path, monkeypatch):
    settings = Settings(
        index_storage_dir=tmp_path / "index",
        acor_use_intent_weights=False,
    )
    repo_id = "route-regression"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "A.java").write_text(
        "package com.foo;\n"
        "class A {\n"
        "    void changedMethod() {\n"
        "        throw new RuntimeException();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo_root / "B.java").write_text(
        "package com.foo;\n"
        "class B {\n"
        "    void callerMethod() { new A().changedMethod(); }\n"
        "    void calleeMethod() {}\n"
        "}\n",
        encoding="utf-8",
    )

    classes = [
        ClassInfo("com.foo.A", "A.java", "A", "com.foo", 2, 6, []),
        ClassInfo("com.foo.B", "B.java", "B", "com.foo", 2, 5, []),
    ]
    methods = [
        MethodInfo(
            "com.foo.A#changedMethod()",
            "com.foo.A",
            "changedMethod",
            "changedMethod()",
            "A.java",
            3,
            5,
            ["public"],
        ),
        MethodInfo(
            "com.foo.B#callerMethod()",
            "com.foo.B",
            "callerMethod",
            "callerMethod()",
            "B.java",
            3,
            3,
            ["public"],
        ),
        MethodInfo(
            "com.foo.B#calleeMethod()",
            "com.foo.B",
            "calleeMethod",
            "calleeMethod()",
            "B.java",
            4,
            4,
            ["public"],
        ),
    ]
    vector_results = [
        SearchResult(
            chunk_id="com.foo.A#changedMethod()",
            score=0.95,
            metadata={
                "method_id": "com.foo.A#changedMethod()",
                "class_id": "com.foo.A",
                "name": "changedMethod",
                "file_path": "A.java",
                "start_line": 3,
                "end_line": 5,
            },
        ),
        SearchResult(
            chunk_id="com.foo.B#callerMethod()",
            score=0.85,
            metadata={
                "method_id": "com.foo.B#callerMethod()",
                "class_id": "com.foo.B",
                "name": "callerMethod",
                "file_path": "B.java",
                "start_line": 3,
                "end_line": 3,
            },
        ),
    ]
    repo_index = RepoIndex(
        repo_id=repo_id,
        repo_root=repo_root,
        fingerprint="test-fingerprint",
        call_graph=CallGraph.build(
            methods,
            [
                CallEdge(
                    "com.foo.B#callerMethod()",
                    "com.foo.A#changedMethod()",
                ),
                CallEdge(
                    "com.foo.A#changedMethod()",
                    "com.foo.B#calleeMethod()",
                ),
            ],
        ),
        dependency_graph=DependencyGraph.build(
            classes,
            [ImportEdge("com.foo.A", "com.foo.B")],
        ),
        symbol_index=SymbolIndex.build(classes, methods),
        vector_store=FakeVectorStore(vector_results),
        num_files=2,
        num_classes=2,
        num_methods=3,
        num_call_edges=2,
        file_token_counts={"A.java": 701, "B.java": 809},
    )
    monkeypatch.setitem(routes._repo_registry, repo_id, repo_index)

    class StubLLM:
        def generate(self, system_prompt: str, user_prompt: str):
            return type("StubResponse", (), {"text": "fixed answer", "latency_ms": 0.0})()

    monkeypatch.setattr(
        routes,
        "get_embedding_model",
        lambda configured: FakeEmbeddingModel(configured.embedding_dim),
    )
    monkeypatch.setattr(routes, "get_llm_client", lambda _: StubLLM())
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    client = TestClient(app)

    yield client, settings, repo_id, repo_index

    client.close()


def test_repo_index_load_keeps_absolute_root_after_chdir(
    query_client, tmp_path, monkeypatch
):
    _, settings, repo_id, repo_index = query_client
    storage_dir = tmp_path / "persisted-index"
    repo_index.repo_root = Path(os.path.relpath(repo_index.repo_root, Path.cwd()))
    repo_index.save(storage_dir)
    monkeypatch.setattr(
        repo_index_module,
        "get_vector_store",
        lambda *_, **__: FakeVectorStore(repo_index.vector_store._results),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old_cwd = Path.cwd()
    os.chdir(elsewhere)
    try:
        loaded = RepoIndex.load(repo_id, storage_dir, settings)
    finally:
        os.chdir(old_cwd)

    assert loaded.repo_root.is_absolute()
    snippet = read_snippet(loaded.repo_root / "A.java", 3, 5)
    assert "throw new RuntimeException();" in snippet


def test_repo_index_load_rejects_missing_root(query_client, tmp_path, monkeypatch):
    _, settings, repo_id, repo_index = query_client
    missing_root = tmp_path / "removed-repository"
    repo_index.repo_root = missing_root
    storage_dir = tmp_path / "persisted-index"
    repo_index.save(storage_dir)
    monkeypatch.setattr(
        repo_index_module,
        "get_vector_store",
        lambda *_: pytest.fail("Vector store should not load for a missing repository"),
    )

    with pytest.raises(RepoRootMissing) as exc_info:
        RepoIndex.load(repo_id, storage_dir, settings)
    assert str(missing_root) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 1a: naive_baseline_tokens == sum of whole-file token counts
# ---------------------------------------------------------------------------

def test_naive_baseline_tokens_equals_sum_of_whole_files(query_client):
    """Exercise the query route's whole-file token baseline."""
    client, _, repo_id, _ = query_client
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert {item["file_path"] for item in result["retrieved_context"]} == {
        "A.java",
        "B.java",
    }
    assert result["naive_baseline_tokens"] == 701 + 809
    assert result["candidates_selected"] == result["candidates_considered"]
    candidate_snippet_tokens = sum(
        item["token_count"] for item in result["retrieved_context"]
    )
    assert result["naive_baseline_tokens"] != candidate_snippet_tokens


def test_query_endpoint_uses_structured_system_prompt(query_client, monkeypatch):
    client, _, repo_id, _ = query_client
    captured_prompts = []

    class CapturingLLM:
        def generate(self, system_prompt: str, user_prompt: str):
            captured_prompts.append(system_prompt)
            return type("StubResponse", (), {"text": "fixed answer", "latency_ms": 0.0})()

    monkeypatch.setattr(routes, "get_llm_client", lambda _: CapturingLLM())
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 200, response.text
    assert captured_prompts == [STRUCTURED_SYSTEM_PROMPT]
    assert response.json()["structured_answer"] is None
    assert response.json()["metrics"]["structured_output_validated"] is False
    assert response.json()["metrics"]["structured_output_limitation"]


def test_query_endpoint_returns_validated_analysis_evidence_and_provider_usage(
    query_client, monkeypatch
):
    client, _, repo_id, _ = query_client

    class StructuredLLM:
        def generate(self, system_prompt: str, user_prompt: str):
            assert "one JSON object" in system_prompt
            assert "BEGIN REPOSITORY EXCERPT" in user_prompt
            return type(
                "Response",
                (),
                {
                    "text": (
                        '{"summary":"Diagnosis verified.",'
                        '"root_cause":"The call path throws.",'
                        '"causal_chain":["caller invokes changedMethod"],'
                        '"suggested_fix":null,"limitations":[],'
                        '"unresolved_questions":[]}'
                    ),
                    "latency_ms": 12.5,
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                },
            )()

    monkeypatch.setattr(routes, "get_llm_client", lambda _: StructuredLLM())
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["structured_answer"]["summary"] == "Diagnosis verified."
    assert result["structured_answer"]["related_methods"]
    assert result["evidence"][0]["method_id"] == "com.foo.A#changedMethod()"
    assert result["evidence"][0]["rank"] == 1
    assert result["evidence"][0]["score"]["composite"] >= 0
    assert result["metrics"]["structured_output_validated"] is True
    assert result["metrics"]["actual_provider_input_tokens"] == 120
    assert result["metrics"]["actual_provider_total_tokens"] == 150
    assert result["metrics"]["estimated_prompt_tokens"] > 0


def test_query_endpoint_surfaces_provider_errors_without_success_fallback(
    query_client, monkeypatch
):
    client, _, repo_id, _ = query_client

    class FailedLLM:
        def generate(self, *_):
            raise RuntimeError("secret-provider-detail")

    monkeypatch.setattr(routes, "get_llm_client", lambda _: FailedLLM())
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "llm_provider_error"
    assert "secret-provider-detail" not in response.text


def test_graph_endpoint_reports_edges_near_resolved_symbol(query_client):
    client, _, repo_id, _ = query_client
    response = client.get(
        f"/repositories/{repo_id}/graph",
        params={"symbol": "changedMethod", "max_hops": 2},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["resolved_symbol"] == "com.foo.A#changedMethod()"
    assert result["call_graph_edges"] == 2
    assert result["dependency_graph_edges"] == 1
    assert any(
        edge["source"] == "com.foo.B#callerMethod()"
        and edge["target"] == "com.foo.A#changedMethod()"
        for edge in result["call_graph_edges_near_symbol"]
    )
    assert result["dependency_edges_near_symbol"] == [
        {"source": "com.foo.A", "target": "com.foo.B"}
    ]


def test_missing_source_checkout_is_not_reported_as_unindexed(
    query_client, tmp_path
):
    _, settings, repo_id, repo_index = query_client
    repo_index.repo_root = tmp_path / "removed-checkout"
    with pytest.raises(HTTPException) as exc_info:
        routes._get_repo_index(repo_id, settings)

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "repository_source_unavailable"


def test_repository_status_explains_missing_index_and_source_checkout(
    query_client, tmp_path
):
    client, _, repo_id, repo_index = query_client
    repo_index.repo_root = tmp_path / "removed-checkout"
    stale = client.get(f"/repositories/{repo_id}/status")
    missing = client.get("/repositories/never-indexed/status")

    assert stale.status_code == 200
    assert stale.json()["indexed"] is False
    assert stale.json()["error_code"] == "repository_source_unavailable"
    assert missing.status_code == 200
    assert missing.json()["indexed"] is False
    assert missing.json()["error_code"] == "repository_not_indexed"


# ---------------------------------------------------------------------------
# Test 1b: total_context_tokens == count_tokens(context_text)
# ---------------------------------------------------------------------------

def test_total_context_tokens_equals_count_tokens_context_text(query_client, monkeypatch):
    """Exercise the query route's formatted-context token count."""
    client, _, repo_id, _ = query_client
    context_text = "// === A.java ===\n// === B.java ===\n" + (
        "formatted context header tokens\n" * 30
    )
    monkeypatch.setattr(routes, "build_context", lambda *_: context_text)
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    selected_snippet_tokens = sum(
        item["token_count"] for item in result["retrieved_context"]
    )
    assert result["total_context_tokens"] == count_tokens(context_text)
    assert result["total_context_tokens"] <= result["token_budget"]
    assert result["total_context_tokens"] != selected_snippet_tokens


def test_query_rejects_nonpositive_token_budgets(query_client):
    client, _, repo_id, _ = query_client
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 0},
    )

    assert response.status_code == 422


def test_unreadable_snippet_is_dropped_from_candidates_and_selection(
    query_client, monkeypatch, capsys
):
    client, _, repo_id, repo_index = query_client
    missing_id = "com.foo.Ghost#missingMethod()"
    repo_index.vector_store._results.append(
        SearchResult(
            chunk_id=missing_id,
            score=0.99,
            metadata={
                "method_id": missing_id,
                "class_id": "com.foo.Ghost",
                "name": "missingMethod",
                "file_path": "missing.java",
                "start_line": 1,
                "end_line": 2,
            },
        )
    )
    original_get_candidates = candidate_pipeline.get_candidates
    candidate_ids = []

    def capture_candidates(*args, **kwargs):
        candidates = original_get_candidates(*args, **kwargs)
        candidate_ids.extend(candidate.method_id for candidate in candidates)
        return candidates

    monkeypatch.setattr(candidate_pipeline, "get_candidates", capture_candidates)
    response = client.post(
        f"/repositories/{repo_id}/query",
        json={"query": "why does changedMethod fail?", "token_budget": 8000},
    )

    assert response.status_code == 200, response.text
    selected_ids = {item["symbol_name"] for item in response.json()["retrieved_context"]}
    assert missing_id not in candidate_ids
    assert "missingMethod" not in selected_ids
    assert "missing.java" in capsys.readouterr().err


def test_unreadable_snippets_raise_when_all_candidates_fail(query_client):
    client, _, repo_id, repo_index = query_client
    repo_index.vector_store._results = [
        SearchResult(
            chunk_id="com.foo.Ghost#missingMethod()",
            score=0.99,
            metadata={
                "method_id": "com.foo.Ghost#missingMethod()",
                "class_id": "com.foo.Ghost",
                "name": "missingMethod",
                "file_path": "missing.java",
                "start_line": 1,
                "end_line": 2,
            },
        )
    ]
    repo_index.call_graph = CallGraph.build([], [])

    with pytest.raises(RuntimeError, match="All retrieved candidates were dropped"):
        client.post(
            f"/repositories/{repo_id}/query",
            json={"query": "show me code", "token_budget": 8000},
        )


def test_qdrant_force_reindex_replaces_points(tmp_path, monkeypatch):
    from qdrant_client import QdrantClient

    settings = Settings(
        index_storage_dir=tmp_path / "index",
        qdrant_url="",
        qdrant_api_key="",
        embedding_dim=16,
    )
    repo_id = "force-reindex-test"
    repo_root = tmp_path / "reindex-repo"
    repo_root.mkdir()
    (repo_root / "Sample.java").write_text(
        "package com.test;\n"
        "class Sample {\n"
        "    void oldOne() {}\n"
        "    void oldTwo() {}\n"
        "    void newOnly() {}\n"
        "}\n",
        encoding="utf-8",
    )
    client = QdrantClient(":memory:")
    vector_store = QdrantVectorStore(settings, repo_id, client=client)

    def parsed(method_names: list[str]) -> ParsedRepo:
        methods = [
            MethodInfo(
                id=f"com.test.Sample#{name}()",
                class_id="com.test.Sample",
                name=name,
                signature=f"{name}()",
                file_path="Sample.java",
                start_line=line,
                end_line=line,
                modifiers=["public"],
            )
            for line, name in enumerate(method_names, start=3)
        ]
        return ParsedRepo(
            classes=[
                ClassInfo(
                    "com.test.Sample",
                    "Sample.java",
                    "Sample",
                    "com.test",
                    2,
                    6,
                    [],
                )
            ],
            methods=methods,
            call_edges=[],
            import_edges=[],
            unparsed_files=[],
        )

    parsed_results = iter([parsed(["oldOne", "oldTwo"]), parsed(["newOnly"])])
    monkeypatch.setattr(routes, "parse_repository", lambda *_: next(parsed_results))
    monkeypatch.setattr(routes, "get_vector_store", lambda *_args, **_kwargs: vector_store)
    monkeypatch.setattr(
        routes,
        "get_embedding_model",
        lambda _: FakeEmbeddingModel(settings.embedding_dim),
    )
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    try:
        with TestClient(app) as test_client:
            first = test_client.post(
                "/repositories/index",
                json={
                    "repo_path": str(repo_root),
                    "repo_id": repo_id,
                    "force_reindex": True,
                },
            )
            first_count = vector_store.count()
            second = test_client.post(
                "/repositories/index",
                json={
                    "repo_path": str(repo_root),
                    "repo_id": repo_id,
                    "force_reindex": True,
                },
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        second_count = vector_store.count()
        search_ids = {
            result.chunk_id
            for result in vector_store.search(
                np.ones((settings.embedding_dim,), dtype=np.float32),
                top_k=10,
            )
        }
        assert first_count == 2
        assert second_count == 1
        assert second_count == second.json()["num_methods"]
        assert search_ids == {"com.test.Sample#newOnly()"}
        assert vector_store.ids() == search_ids
        vector_store.remove(["com.test.Sample#newOnly()"])
        assert vector_store.count() == 0
        print(f"Qdrant point counts: first={first_count}, second={second_count}")
    finally:
        routes._repo_registry.pop(repo_id, None)
        client.close()


def test_index_excludes_test_sources_and_file_token_counts(query_client, tmp_path, monkeypatch):
    _, settings, _, _ = query_client
    repo_id = "test-source-filter-index"
    repo_root = tmp_path / "test-source-filter-repo"
    (repo_root / "src" / "main" / "java").mkdir(parents=True)
    (repo_root / "src" / "test" / "java").mkdir(parents=True)
    (repo_root / "src" / "main" / "java" / "Main.java").write_text(
        "class Main { void mainMethod() {} }\n", encoding="utf-8"
    )
    (repo_root / "src" / "test" / "java" / "MainTest.java").write_text(
        "class MainTest { void testMethod() {} }\n", encoding="utf-8"
    )
    settings.exclude_test_sources = True
    main_class = ClassInfo(
        "sample.Main", "src/main/java/Main.java", "Main", "sample", 1, 1, []
    )
    test_class = ClassInfo(
        "sample.MainTest",
        "src/test/java/MainTest.java",
        "MainTest",
        "sample",
        1,
        1,
        [],
    )
    parsed = ParsedRepo(
        classes=[main_class, test_class],
        methods=[
            MethodInfo(
                "sample.Main#mainMethod()",
                "sample.Main",
                "mainMethod",
                "mainMethod()",
                main_class.file_path,
                1,
                1,
                [],
            ),
            MethodInfo(
                "sample.MainTest#testMethod()",
                "sample.MainTest",
                "testMethod",
                "testMethod()",
                test_class.file_path,
                1,
                1,
                [],
            ),
        ],
        call_edges=[],
        import_edges=[],
        unparsed_files=[],
    )
    monkeypatch.setattr(routes, "parse_repository", lambda *_: parsed)
    monkeypatch.setattr(routes, "get_vector_store", lambda *_args, **_kwargs: FakeVectorStore())
    monkeypatch.setattr(
        routes,
        "get_embedding_model",
        lambda configured: FakeEmbeddingModel(configured.embedding_dim),
    )
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/repositories/index",
                json={
                    "repo_path": str(repo_root),
                    "repo_id": repo_id,
                    "force_reindex": True,
                },
            )
        assert response.status_code == 200, response.text
        result = response.json()
        indexed = routes._repo_registry[repo_id]
        assert result["num_classes"] == 1
        assert result["num_methods"] == 1
        assert indexed.file_token_counts.keys() == {"src/main/java/Main.java"}
        assert not indexed.symbol_index.exact_lookup("testMethod")
    finally:
        routes._repo_registry.pop(repo_id, None)


# ---------------------------------------------------------------------------
# Test 1c: CallGraph.callers_within_hops respects direction and hop limit
# ---------------------------------------------------------------------------

def test_call_graph_callers_within_hops_direction_and_limits():
    """Verify callers_within_hops strictly follows incoming (caller) edges and cutoff."""
    methods = [
        MethodInfo("com.foo.A#a()", "com.foo.A", "a", "a()", "com/foo/A.java", 1, 5, ["public"]),
        MethodInfo("com.foo.B#b()", "com.foo.B", "b", "b()", "com/foo/B.java", 1, 5, ["public"]),
        MethodInfo("com.foo.C#c()", "com.foo.C", "c", "c()", "com/foo/C.java", 1, 5, ["public"]),
        MethodInfo("com.foo.D#d()", "com.foo.D", "d", "d()", "com/foo/D.java", 1, 5, ["public"]),
    ]
    # Chain: A -> B -> C -> D
    edges = [
        CallEdge("com.foo.A#a()", "com.foo.B#b()"),
        CallEdge("com.foo.B#b()", "com.foo.C#c()"),
        CallEdge("com.foo.C#c()", "com.foo.D#d()"),
    ]
    cg = CallGraph.build(methods, edges)

    # 1 hop callers of C: only B. Callee D must NOT be included (tests direction)
    callers_c_1 = cg.callers_within_hops("com.foo.C#c()", hops=1)
    assert callers_c_1 == {"com.foo.B#b()"}
    assert "com.foo.D#d()" not in callers_c_1

    # 2 hop callers of C: B and A (tests hop limit expansion)
    callers_c_2 = cg.callers_within_hops("com.foo.C#c()", hops=2)
    assert callers_c_2 == {"com.foo.B#b()", "com.foo.A#a()"}

    # 1 hop callers of D: only C. A and B are outside 1-hop cutoff (tests cutoff)
    callers_d_1 = cg.callers_within_hops("com.foo.D#d()", hops=1)
    assert callers_d_1 == {"com.foo.C#c()"}

    # Callers of A: none (A has no callers)
    callers_a = cg.callers_within_hops("com.foo.A#a()", hops=3)
    assert callers_a == set()


# ---------------------------------------------------------------------------
# Test 1d: candidate_pipeline root-cause dedups by method_id;
#          change-impact seeds from callers
# ---------------------------------------------------------------------------

def test_candidate_pipeline_root_cause_dedups_by_method_id(tmp_path):
    """Verify root-cause path deduplicates candidates returned by both semantic search and graph."""
    source_dir = tmp_path / "com" / "foo"
    source_dir.mkdir(parents=True)
    (source_dir / "A.java").write_text(
        "\n".join(f"line {line}" for line in range(1, 21)),
        encoding="utf-8",
    )
    classes = [ClassInfo("com.foo.A", "com/foo/A.java", "A", "com.foo", 1, 20, [])]
    methods = [
        MethodInfo("com.foo.A#target()", "com.foo.A", "target", "target()", "com/foo/A.java", 1, 10, ["public"]),
        MethodInfo("com.foo.A#caller()", "com.foo.A", "caller", "caller()", "com/foo/A.java", 11, 20, ["public"]),
    ]
    edges = [CallEdge("com.foo.A#caller()", "com.foo.A#target()")]

    cg = CallGraph.build(methods, edges)
    dg = DependencyGraph.build(classes, [])
    sym_idx = SymbolIndex.build(classes, methods)

    # Vector store returns both target and caller semantically
    sr1 = SearchResult(
        chunk_id="com.foo.A#target()", score=0.95,
        metadata={
            "method_id": "com.foo.A#target()", "class_id": "com.foo.A",
            "name": "target", "file_path": "com/foo/A.java", "start_line": 1, "end_line": 10
        }
    )
    sr2 = SearchResult(
        chunk_id="com.foo.A#caller()", score=0.85,
        metadata={
            "method_id": "com.foo.A#caller()", "class_id": "com.foo.A",
            "name": "caller", "file_path": "com/foo/A.java", "start_line": 11, "end_line": 20
        }
    )
    vs = FakeVectorStore([sr1, sr2])

    repo_index = RepoIndex(
        repo_id="test_repo",
        repo_root=tmp_path,
        fingerprint="fp",
        call_graph=cg,
        dependency_graph=dg,
        symbol_index=sym_idx,
        vector_store=vs,
        num_files=1,
        num_classes=1,
        num_methods=2,
        num_call_edges=1,
    )

    # Query names "target" as an anchor symbol
    qa = QueryAnalysis(
        raw_query="why does target fail?",
        intent=QueryIntentLabel.ROOT_CAUSE_ANALYSIS,
        anchor_symbols={"target"},
        query_tokens={"target", "why", "fail"},
    )

    settings = Settings()
    fake_embed = FakeEmbeddingModel()

    candidates = generate_root_cause_candidates(qa, repo_index, fake_embed, settings)
    # Check that method_ids are unique (no duplicates between semantic and graph expansion)
    method_ids = [c.method_id for c in candidates]
    assert len(method_ids) == len(set(method_ids))
    assert set(method_ids) == {"com.foo.A#target()", "com.foo.A#caller()"}


def test_candidate_pipeline_change_impact_seeds_from_callers(tmp_path):
    """Verify change-impact path seeds from callers of the changed symbol via directed BFS."""
    source_dir = tmp_path / "com" / "foo"
    source_dir.mkdir(parents=True)
    (source_dir / "A.java").write_text(
        "\n".join(f"line {line}" for line in range(1, 41)),
        encoding="utf-8",
    )
    classes = [
        ClassInfo("com.foo.A", "com/foo/A.java", "A", "com.foo", 1, 30, [])
    ]
    methods = [
        MethodInfo("com.foo.A#changedMethod()", "com.foo.A", "changedMethod", "changedMethod()", "com/foo/A.java", 1, 10, ["public"]),
        MethodInfo("com.foo.A#callerMethod()", "com.foo.A", "callerMethod", "callerMethod()", "com/foo/A.java", 11, 20, ["public"]),
        MethodInfo("com.foo.A#unrelatedMethod()", "com.foo.A", "unrelatedMethod", "unrelatedMethod()", "com/foo/A.java", 21, 30, ["public"]),
        MethodInfo("com.foo.A#calleeMethod()", "com.foo.A", "calleeMethod", "calleeMethod()", "com/foo/A.java", 31, 40, ["public"]),
    ]
    # callerMethod calls changedMethod, which calls calleeMethod
    edges = [
        CallEdge("com.foo.A#callerMethod()", "com.foo.A#changedMethod()"),
        CallEdge("com.foo.A#changedMethod()", "com.foo.A#calleeMethod()"),
    ]

    cg = CallGraph.build(methods, edges)
    dg = DependencyGraph.build(classes, [])
    sym_idx = SymbolIndex.build(classes, methods)
    vs = FakeVectorStore([])  # empty vector store to isolate graph seeding

    repo_index = RepoIndex(
        repo_id="test_repo",
        repo_root=tmp_path,
        fingerprint="fp",
        call_graph=cg,
        dependency_graph=dg,
        symbol_index=sym_idx,
        vector_store=vs,
        num_files=1,
        num_classes=1,
        num_methods=4,
        num_call_edges=2,
    )

    settings = Settings()
    fake_embed = FakeEmbeddingModel()

    candidates = generate_change_impact_candidates(
        changed_symbol="changedMethod",
        repo_index=repo_index,
        embedding_model=fake_embed,
        settings=settings,
        hop_limit=2,
    )

    candidate_ids = {c.method_id for c in candidates}
    # Must include the changed method and its caller
    assert "com.foo.A#changedMethod()" in candidate_ids
    assert "com.foo.A#callerMethod()" in candidate_ids
    # Change impact follows incoming caller edges, not outgoing callee edges.
    assert "com.foo.A#calleeMethod()" not in candidate_ids
    # Unrelated method has no call edge to changedMethod, so it must not be seeded by callers
    assert "com.foo.A#unrelatedMethod()" not in candidate_ids


def test_change_impact_query_excludes_callees(query_client, monkeypatch):
    """Exercise directed change-impact candidate generation through the query route."""
    client, _, repo_id, _ = query_client
    original_get_candidates = candidate_pipeline.get_candidates
    observed_candidate_ids = []

    def capture_candidates(*args, **kwargs):
        candidates = original_get_candidates(*args, **kwargs)
        observed_candidate_ids.extend(candidate.method_id for candidate in candidates)
        return candidates

    monkeypatch.setattr(candidate_pipeline, "get_candidates", capture_candidates)
    request_body = {
        "query": "What could be impacted by changing changedMethod?",
        "changed_symbol": "changedMethod",
        "token_budget": 8000,
    }
    response = client.post(
        f"/repositories/{repo_id}/query",
        json=request_body,
    )

    assert response.status_code == 200, response.text
    assert "com.foo.A#changedMethod()" in observed_candidate_ids
    assert "com.foo.B#callerMethod()" in observed_candidate_ids
    assert "com.foo.B#calleeMethod()" not in observed_candidate_ids


# ---------------------------------------------------------------------------
# Test Item B: acor_use_intent_weights toggle
# ---------------------------------------------------------------------------

def test_acor_use_intent_weights_toggle(query_client, monkeypatch):
    """Capture the weights passed by the query route for both setting values."""
    from app.acor.scorer import score_candidates as original_score_candidates

    client, settings, repo_id, _ = query_client
    captured_weights = []

    def capture_weights(*args, **kwargs):
        weights = kwargs.get("weights", args[5] if len(args) > 5 else None)
        captured_weights.append(weights)
        return original_score_candidates(*args, **kwargs)

    monkeypatch.setattr(routes, "score_candidates", capture_weights)
    body = {"query": "why does changedMethod fail?", "token_budget": 8000}

    settings.acor_use_intent_weights = False
    flat_response = client.post(f"/repositories/{repo_id}/query", json=body)
    assert flat_response.status_code == 200, flat_response.text
    flat_intent = flat_response.json()["detected_intent"]
    assert flat_intent == QueryIntentLabel.ROOT_CAUSE_ANALYSIS.value
    assert captured_weights[-1] == settings.acor_weights

    settings.acor_use_intent_weights = True
    adaptive_response = client.post(f"/repositories/{repo_id}/query", json=body)
    assert adaptive_response.status_code == 200, adaptive_response.text
    adaptive_intent = adaptive_response.json()["detected_intent"]
    assert adaptive_intent == QueryIntentLabel.ROOT_CAUSE_ANALYSIS.value
    assert captured_weights[-1] == _INTENT_WEIGHTS[
        QueryIntentLabel(adaptive_intent)
    ]
