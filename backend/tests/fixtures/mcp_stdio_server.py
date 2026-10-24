from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.acor.scorer import Candidate
from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.providers import LLMResponse
from app.mcp import server
from app.parsing.java_bridge import CallEdge, ClassInfo, MethodInfo
from app.parsing.repo_walker import read_snippet
from app.retrieval.vector_store import VectorStore


class FixtureVectorStore(VectorStore):
    def add(self, ids, vectors, metadatas):
        pass

    def search(self, query_vector, top_k):
        return []

    def count(self):
        return 1

    def ids(self):
        return set()

    def remove(self, ids):
        pass

    def clear(self):
        pass


class FixtureEmbeddingModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return np.ones((4,), dtype=np.float32)


class FixtureLLM:
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        return LLMResponse(
            text=(
                '{"summary":"stdio analysis passed.",'
                '"root_cause":"The fixture call is present.",'
                '"causal_chain":[],"suggested_fix":null,'
                '"limitations":[],"unresolved_questions":[]}'
            ),
            latency_ms=1.0,
            prompt_tokens=25,
            completion_tokens=12,
            total_tokens=37,
        )


def main() -> None:
    repo_root = Path(sys.argv[1])
    repo_root.mkdir(parents=True, exist_ok=True)
    source = repo_root / "Sample.java"
    source.write_text(
        "class Sample {\n"
        "  void changedMethod() { helper(); }\n"
        "  void helper() {}\n"
        "}\n",
        encoding="utf-8",
    )
    cls = ClassInfo("Sample", "Sample.java", "Sample", "", 1, 4, [])
    methods = [
        MethodInfo("Sample#changedMethod()", "Sample", "changedMethod", "changedMethod()", "Sample.java", 2, 2, []),
        MethodInfo("Sample#helper()", "Sample", "helper", "helper()", "Sample.java", 3, 3, []),
    ]
    index = RepoIndex(
        repo_id="stdio-fixture",
        repo_root=repo_root,
        fingerprint="stdio-fixture",
        call_graph=CallGraph.build(
            methods,
            [CallEdge("Sample#changedMethod()", "Sample#helper()")],
        ),
        dependency_graph=DependencyGraph.build([cls], []),
        symbol_index=SymbolIndex.build([cls], methods),
        vector_store=FixtureVectorStore(),
        num_files=1,
        num_classes=1,
        num_methods=2,
        num_call_edges=1,
        file_token_counts={"Sample.java": 20},
    )

    def candidates(*, query, repo_index, **_):
        method = methods[0]
        return [
            Candidate(
                method_id=method.id,
                class_id=method.class_id,
                name=method.name,
                kind="method",
                file_path=method.file_path,
                start_line=method.start_line,
                end_line=method.end_line,
                snippet=read_snippet(
                    repo_index.repo_root / method.file_path,
                    method.start_line,
                    method.end_line,
                ),
                token_count=8,
                raw_semantic_score=0.9,
            )
        ]

    server.get_settings = lambda: Settings(acor_use_intent_weights=False)
    server._get_repo_index = lambda *_: index
    server.get_embedding_model = lambda _: FixtureEmbeddingModel()
    server.get_candidates = candidates
    server.get_llm_client = lambda _: FixtureLLM()
    asyncio.run(server.main())


if __name__ == "__main__":
    main()
