"""
ACOR step 1/3: score every retrieval candidate against the query.

    Developer Query -> Query Understanding -> Repository Parser ->
    Call Graph + Dependency Graph + Symbol Index ->
    [ACOR: scorer.py -> ranker.py -> context_builder.py] ->
    Optimized Repository Context -> LLM

This module answers "how relevant is candidate C to query Q", nothing about
*how many* candidates fit the token budget -- that separation is
deliberate. Mixing "relevance" and "fits the budget" into one number would
make it impossible to later ask (as the synopsis' Chapter 7 evaluation
plan effectively requires) "how good is ACOR's *ranking* independent of
the budget we happened to pick", since every score would already be
budget-contaminated. ranker.py consumes this module's output plus a
token_budget and decides *which* high-scoring candidates survive.

The composite score for candidate c against query q is:

    S(c, q) = w_sem   * normalized_semantic_sim(c, q)
            + w_call  * call_graph_proximity(c, q)
            + w_dep   * dependency_proximity(c, q)
            + w_sym   * symbol_match(c, q)

Weights come from Settings.acor_weights (renormalized to sum to 1) so they
can be tuned -- or ablated one at a time for the evaluation chapter --
without touching this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.acor.tokenizer import count_tokens
from app.api.schemas import ScoreBreakdown
from app.config import ACORWeights
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.repo_walker import read_snippet
from app.retrieval.query_understanding import QueryAnalysis
from app.retrieval.vector_store import SearchResult


@dataclass(frozen=True)
class Candidate:
    """A retrieval candidate before scoring -- one method (or, in principle,
    another chunk granularity) with everything the scorer and, later, the
    context builder need, gathered once up front so neither has to touch
    disk or the vector store again."""

    method_id: str
    class_id: str
    name: str
    kind: str  # "method" | "class"
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    token_count: int
    raw_semantic_score: float


@dataclass(frozen=True)
class ScoredCandidate(Candidate):
    score: ScoreBreakdown


def prepare_candidates(search_results: list[SearchResult], repo_root: Path) -> list[Candidate]:
    """Turn raw vector-search hits into fully-populated Candidates: read
    each one's source snippet exactly once and count its tokens, so every
    later stage (scoring, ranking, context assembly) works off the same
    in-memory copy instead of re-reading the file from disk repeatedly.

    search_results are expected to carry metadata written at index time:
    file_path, start_line, end_line, class_id, name, kind (see
    api/routes.py's indexing handler for where that metadata is set).
    """
    candidates: list[Candidate] = []
    for result in search_results:
        meta = result.metadata
        file_path = repo_root / meta["file_path"]
        snippet = read_snippet(file_path, meta["start_line"], meta["end_line"])
        candidates.append(
            Candidate(
                method_id=result.chunk_id,
                class_id=meta["class_id"],
                name=meta["name"],
                kind=meta.get("kind", "method"),
                file_path=meta["file_path"],
                start_line=meta["start_line"],
                end_line=meta["end_line"],
                snippet=snippet,
                token_count=count_tokens(snippet),
                raw_semantic_score=result.score,
            )
        )
    return candidates


def _resolve_anchors(
    anchor_symbols: set[str], symbol_index: SymbolIndex
) -> tuple[set[str], set[str]]:
    """Map raw identifier strings pulled out of the query (e.g.
    {"OrderService", "processOrder"}) to concrete graph anchors: method ids
    for the call graph, class ids for the dependency graph.

    Relies on the method-id convention "<class_id>#<signature>" set by
    java_bridge.py / RepoParser.java -- splitting on "#" recovers the
    owning class without a second index lookup. If that id format ever
    changes, this is the one place that needs to change with it.
    """
    method_anchor_ids: set[str] = set()
    class_anchor_ids: set[str] = set()
    for anchor in anchor_symbols:
        for location in symbol_index.exact_lookup(anchor):
            if location.kind.value == "method":
                method_anchor_ids.add(location.symbol_id)
                class_anchor_ids.add(location.symbol_id.split("#", 1)[0])
            else:
                class_anchor_ids.add(location.symbol_id)
    return method_anchor_ids, class_anchor_ids


def _min_max_normalize(values: list[float]) -> list[float]:
    """Stretch a set of raw semantic scores to fill [0, 1].

    Cosine similarities from a *single* embedding model, across the
    top-K neighbors of one query, tend to cluster in a narrow high band
    (e.g. 0.62-0.81) rather than spanning the full [-1, 1] range -- the
    candidates are, after all, already the ones the retriever considered
    closest. Combining that raw, narrow-band number with graph/symbol
    scores that easily span [0, 1] would let semantic similarity get
    drowned out by comparison. Min-max normalizing within the candidate
    set fixes the scale without changing the *order* semantic similarity
    induces, which is the property that actually matters here.
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0 for _ in values]  # all candidates equally similar -- don't manufacture a fake spread
    return [(v - lo) / (hi - lo) for v in values]


def score_candidates(
    candidates: list[Candidate],
    query_analysis: QueryAnalysis,
    call_graph: CallGraph,
    dependency_graph: DependencyGraph,
    symbol_index: SymbolIndex,
    weights: ACORWeights,
    max_hops: int,
) -> list[ScoredCandidate]:
    """Score every candidate and return them (unsorted -- ranker.py owns
    ordering plus the budget cut)."""
    w = weights.normalized()
    method_anchors, class_anchors = _resolve_anchors(query_analysis.anchor_symbols, symbol_index)

    normalized_semantic = _min_max_normalize([c.raw_semantic_score for c in candidates])

    scored: list[ScoredCandidate] = []
    for candidate, semantic in zip(candidates, normalized_semantic):
        call_score = call_graph.proximity(candidate.method_id, method_anchors, max_hops)
        dep_score = dependency_graph.proximity(candidate.class_id, class_anchors, max_hops)
        symbol_score = symbol_index.match_score(
            candidate.method_id, candidate.name, query_analysis.query_tokens
        )

        composite = (
            w["semantic"] * semantic
            + w["call_graph"] * call_score
            + w["dependency"] * dep_score
            + w["symbol"] * symbol_score
        )

        scored.append(
            ScoredCandidate(
                **candidate.__dict__,
                score=ScoreBreakdown(
                    semantic=semantic,
                    call_graph=call_score,
                    dependency=dep_score,
                    symbol=symbol_score,
                    composite=composite,
                ),
            )
        )
    return scored
