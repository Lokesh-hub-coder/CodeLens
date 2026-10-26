"""ACOR Scorer — computes the four-term composite relevance score for each
candidate code chunk.

Formula (from docs/ACOR_algorithm.md):
  S(c,q) = w_sem  * norm(semantic_sim(c,q))
          + w_call * call_graph_proximity(c,q)
          + w_dep  * dependency_proximity(c,q)
          + w_sym  * symbol_match(c,q)

All four raw scores are in [0,1] before weighting.  Semantic scores are
min-max normalised across the candidate set so one very-high-confidence
embedding match does not drown out the graph/symbol terms.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from app.api.schemas import ScoreBreakdown
from app.config import ACORWeights
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex, tokenize_identifier
from app.retrieval.query_understanding import QueryAnalysis


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """A single method retrieved from the vector store before scoring."""
    method_id:          str
    class_id:           str
    name:               str
    kind:               str          # "method" | "class"
    file_path:          str
    start_line:         int
    end_line:           int
    snippet:            str
    token_count:        int
    raw_semantic_score: float


@dataclass
class ScoredCandidate(Candidate):
    """Candidate with its full ACOR score breakdown attached."""
    score: ScoreBreakdown = field(default_factory=lambda: ScoreBreakdown(
        semantic=0.0, call_graph=0.0, dependency=0.0, symbol=0.0, composite=0.0
    ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _min_max_normalize(values: list[float]) -> list[float]:
    """Normalise *values* to [0,1] via min-max scaling.

    Special cases:
    - Empty input  -> return [].
    - All values equal -> return [1.0, ...] so every candidate gets full
      credit rather than all-zero (which would silently kill the semantic
      signal for tied embedding scores).
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    span = hi - lo
    # Round to 10 decimal places to avoid IEEE-754 noise in equality checks
    return [round((v - lo) / span, 10) for v in values]


def _lexical_match_scores(
    candidates: list[Candidate],
    query_tokens: set[str],
) -> list[float]:
    """Return candidate-set-normalized BM25 scores over method source text."""
    if not candidates or not query_tokens:
        return [0.0] * len(candidates)

    documents = [
        re.findall(r"[a-z0-9_$]+", candidate.snippet.casefold())
        for candidate in candidates
    ]
    document_sets = [set(document) for document in documents]
    document_frequency = {
        token: sum(token in document for document in document_sets)
        for token in query_tokens
    }
    average_length = sum(map(len, documents)) / len(documents)
    scores: list[float] = []

    for document in documents:
        frequencies: dict[str, int] = {}
        for token in document:
            if token in query_tokens:
                frequencies[token] = frequencies.get(token, 0) + 1

        score = 0.0
        for token, frequency in frequencies.items():
            inverse_frequency = math.log(
                1.0
                + (len(documents) - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            length_norm = 1.5 * (
                1.0 - 0.75 + 0.75 * len(document) / average_length
            )
            score += inverse_frequency * frequency * 2.5 / (frequency + length_norm)
        scores.append(score)

    if not any(scores):
        return [0.0] * len(candidates)
    return _min_max_normalize(scores)


def _resolve_anchors(
    anchor_symbols: set[str],
    symbol_index: SymbolIndex,
) -> tuple[set[str], set[str]]:
    """Map query anchor names to concrete method / class IDs in the index.

    Returns (method_anchor_ids, class_anchor_ids).  Names that do not appear
    in the index are silently dropped -- a query can name something that
    isn't in this repo (e.g. a JDK class), and that is fine.

    SymbolLocation has no class_id field; for methods the class ID is the
    part of symbol_id before the '#' separator (e.g. "com.foo.Bar" from
    "com.foo.Bar#doThing(int)").
    """
    method_anchors: set[str] = set()
    class_anchors:  set[str] = set()

    for name in anchor_symbols:
        locations = symbol_index.exact_lookup(name)
        for loc in locations:
            from app.graphs.symbol_index import SymbolKind  # local to avoid circular
            if loc.kind == SymbolKind.METHOD:
                method_anchors.add(loc.symbol_id)
                # Derive class_id: everything before the '#' in the method ID
                if "#" in loc.symbol_id:
                    class_anchors.add(loc.symbol_id.split("#")[0])
            elif loc.kind == SymbolKind.CLASS:
                class_anchors.add(loc.symbol_id)

    return method_anchors, class_anchors


def _class_name_match_score(
    candidate_class_id: str,
    anchor_class_ids: set[str],
    dependency_graph: DependencyGraph,
) -> float:
    """Match an explicit query class to a candidate's class or enclosing class."""
    candidate_class_ids: set[str] = set()
    class_id = candidate_class_id
    while class_id:
        if class_id in dependency_graph.graph:
            candidate_class_ids.add(class_id)
        class_id = class_id.rpartition(".")[0]

    best_score = 0.0
    for candidate_id in candidate_class_ids:
        candidate_name = dependency_graph.graph.nodes[candidate_id].get(
            "name", candidate_id.rpartition(".")[2]
        )
        candidate_tokens = tokenize_identifier(candidate_name)
        for anchor_id in anchor_class_ids:
            if anchor_id not in dependency_graph.graph:
                continue
            anchor_name = dependency_graph.graph.nodes[anchor_id].get(
                "name", anchor_id.rpartition(".")[2]
            )
            anchor_tokens = tokenize_identifier(anchor_name)
            intersection = candidate_tokens & anchor_tokens
            if len(intersection) < 2:
                continue
            score = len(intersection) / len(candidate_tokens | anchor_tokens)
            best_score = max(best_score, score)
    return best_score


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def score_candidates(
    candidates:       list[Candidate],
    query:            QueryAnalysis,
    call_graph:       CallGraph,
    dependency_graph: DependencyGraph,
    symbol_index:     SymbolIndex,
    weights:          ACORWeights,
    max_hops:         int = 4,
) -> list[ScoredCandidate]:
    """Score every candidate and return them sorted by composite score desc.

    Steps:
    1. Resolve anchor symbols -> method / class anchor ID sets.
    2. Min-max normalise raw_semantic_score across all candidates.
    3. For each candidate compute all four signal terms, then the composite.
    4. Return ScoredCandidate list sorted by composite descending.
    """
    if not candidates:
        return []

    # Step 1: anchors
    method_anchors, class_anchors = _resolve_anchors(
        query.anchor_symbols, symbol_index
    )

    # Step 2: normalise semantic scores
    raw_semantics = [c.raw_semantic_score for c in candidates]
    norm_semantics = _min_max_normalize(raw_semantics)
    lexical_scores = _lexical_match_scores(candidates, query.query_tokens)

    # Step 3: score each candidate
    scored: list[ScoredCandidate] = []
    for candidate, sem_norm, lexical_score in zip(
        candidates, norm_semantics, lexical_scores
    ):
        cg_score  = call_graph.proximity(
            candidate.method_id, method_anchors, max_hops=max_hops
        )
        dep_score = dependency_graph.proximity(
            candidate.class_id, class_anchors, max_hops=max_hops
        )
        sym_score = max(
            symbol_index.match_score(
                candidate.method_id, candidate.name, query.query_tokens
            ),
            lexical_score * 0.25,
            _class_name_match_score(
                candidate.class_id, class_anchors, dependency_graph
            ),
        )

        composite = (
            weights.semantic   * sem_norm
            + weights.call_graph * cg_score
            + weights.dependency * dep_score
            + weights.symbol     * sym_score
        )

        scored.append(ScoredCandidate(
            **{k: getattr(candidate, k) for k in Candidate.__dataclass_fields__},
            score=ScoreBreakdown(
                semantic=sem_norm,
                call_graph=cg_score,
                dependency=dep_score,
                symbol=sym_score,
                composite=composite,
            ),
        ))

    # Step 4: sort by composite descending
    scored.sort(key=lambda c: c.score.composite, reverse=True)
    return scored