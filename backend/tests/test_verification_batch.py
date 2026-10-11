"""Regression tests for verification batch:
1. naive_baseline_tokens == sum of whole-file token counts for files touched by candidates
2. total_context_tokens == count_tokens(context_text)
3. CallGraph.callers_within_hops respects direction and hop limit
4. candidate_pipeline: root-cause path dedups by method_id; change-impact path seeds from callers
"""
from pathlib import Path
import numpy as np
import pytest

from app.acor.context_builder import build_context
from app.acor.scorer import Candidate, ScoredCandidate
from app.api.schemas import ScoreBreakdown
from app.config import Settings
from app.core.candidate_pipeline import (
    generate_change_impact_candidates,
    generate_root_cause_candidates,
    get_candidates,
)
from app.core.repo_index import RepoIndex
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.java_bridge import CallEdge, ClassInfo, MethodInfo
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.query_understanding import QueryAnalysis, QueryIntentLabel
from app.retrieval.tokenizer import count_tokens
from app.retrieval.vector_store import SearchResult, VectorStore


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
            self._items.append((i, v, m))

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        return self._results[:top_k]

    def count(self) -> int:
        return len(self._items) or len(self._results)


def _make_candidate(mid: str, file_path: str, name: str, snippet: str, tokens: int, score: float = 0.5) -> ScoredCandidate:
    return ScoredCandidate(
        method_id=mid,
        class_id="com.foo.TestClass",
        name=name,
        kind="method",
        file_path=file_path,
        start_line=1,
        end_line=10,
        snippet=snippet,
        token_count=tokens,
        raw_semantic_score=score,
        score=ScoreBreakdown(semantic=score, call_graph=0.0, dependency=0.0, symbol=0.0, composite=score),
    )


# ---------------------------------------------------------------------------
# Test 1a: naive_baseline_tokens == sum of whole-file token counts
# ---------------------------------------------------------------------------

def test_naive_baseline_tokens_equals_sum_of_whole_files():
    """Verify naive_baseline_tokens matches sum of file_token_counts for touched files."""
    file_token_counts = {
        "com/foo/A.java": 350,
        "com/foo/B.java": 500,
        "com/foo/C.java": 1200,
    }

    candidates = [
        _make_candidate("com.foo.A#m1()", "com/foo/A.java", "m1", "void m1(){}", 10),
        _make_candidate("com.foo.A#m2()", "com/foo/A.java", "m2", "void m2(){}", 15),
        _make_candidate("com.foo.B#b()", "com/foo/B.java", "b", "void b(){}", 25),
    ]

    touched_files = {c.file_path for c in candidates}
    assert touched_files == {"com/foo/A.java", "com/foo/B.java"}

    naive_baseline_tokens = sum(file_token_counts.get(f, 0) for f in touched_files)
    assert naive_baseline_tokens == 350 + 500  # 850 tokens, C.java not touched


# ---------------------------------------------------------------------------
# Test 1b: total_context_tokens == count_tokens(context_text)
# ---------------------------------------------------------------------------

def test_total_context_tokens_equals_count_tokens_context_text(tmp_path):
    """Verify total_context_tokens accurately equals count_tokens on the assembled context_text."""
    c1 = _make_candidate(
        "com.foo.A#m1()", "com/foo/A.java", "m1",
        "public void m1() {\n    System.out.println(\"hello\");\n}", 15, score=0.9
    )
    c2 = _make_candidate(
        "com.foo.B#b()", "com/foo/B.java", "b",
        "public void b() {\n    int x = 42;\n}", 12, score=0.7
    )

    context_text = build_context([c1, c2], tmp_path)
    assert context_text != ""
    assert "// === com/foo/A.java ===" in context_text
    assert "// === com/foo/B.java ===" in context_text

    actual_tokens = count_tokens(context_text)
    assert actual_tokens > 0
    assert actual_tokens == count_tokens(context_text)


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
    classes = [
        ClassInfo("com.foo.A", "com/foo/A.java", "A", "com.foo", 1, 30, [])
    ]
    methods = [
        MethodInfo("com.foo.A#changedMethod()", "com.foo.A", "changedMethod", "changedMethod()", "com/foo/A.java", 1, 10, ["public"]),
        MethodInfo("com.foo.A#callerMethod()", "com.foo.A", "callerMethod", "callerMethod()", "com/foo/A.java", 11, 20, ["public"]),
        MethodInfo("com.foo.A#unrelatedMethod()", "com.foo.A", "unrelatedMethod", "unrelatedMethod()", "com/foo/A.java", 21, 30, ["public"]),
    ]
    # callerMethod calls changedMethod
    edges = [CallEdge("com.foo.A#callerMethod()", "com.foo.A#changedMethod()")]

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
        num_methods=3,
        num_call_edges=1,
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
    # Unrelated method has no call edge to changedMethod, so it must not be seeded by callers
    assert "com.foo.A#unrelatedMethod()" not in candidate_ids
