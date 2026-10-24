"""Candidate generation - the step between query understanding and ACOR scoring.

Two separate paths, both returning list[Candidate] so the same scorer/ranker
pipeline consumes either output unchanged:

  generate_root_cause_candidates()
      Used for:  ROOT_CAUSE_ANALYSIS, BUG_EXPLANATION, GENERAL_QA
      Strategy:  Semantic search top-K  +  call-graph expansion from anchors
      Rationale: "Why does X fail?" - we need the faulty method AND its callers

  generate_change_impact_candidates()
      Used for:  REFACTORING, FEATURE_LOCATION
      Strategy:  Graph BFS from changed symbol  +  small semantic supplement
      Rationale: "I am changing X - what else breaks?" - graph is primary signal

Both paths deduplicate by method_id and attach snippets + token counts so
scorer.py never has to touch the filesystem.
"""
from __future__ import annotations

from pathlib import Path
import sys

from app.acor.scorer import Candidate
from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.symbol_index import SymbolKind, tokenize_identifier
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.query_understanding import QueryAnalysis, analyze_query
from app.retrieval.tokenizer import count_tokens
from app.api.schemas import QueryIntentLabel


def _read_snippet_safe(
    repo_root: Path, file_path: str, start: int, end: int
) -> str | None:
    """Read source lines [start, end] inclusive, dropping unreadable candidates."""
    try:
        from app.parsing.repo_walker import read_snippet
        content = read_snippet(repo_root / file_path, start, end)
        if content:
            return content
        reason = "snippet was empty"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    print(
        f"Dropping candidate snippet for '{repo_root / file_path}': {reason}",
        file=sys.stderr,
    )
    return None


def _meta_to_candidate(meta: dict, snippet: str) -> Candidate:
    return Candidate(
        method_id=meta["method_id"],
        class_id=meta["class_id"],
        name=meta["name"],
        kind="method",
        file_path=meta["file_path"],
        start_line=meta["start_line"],
        end_line=meta["end_line"],
        snippet=snippet,
        token_count=max(count_tokens(snippet), 5),
        raw_semantic_score=meta.get("_score", 0.0),
    )


def _build_candidate_map(
    repo_index: RepoIndex,
    method_ids: set[str],
    repo_root: Path,
    base_score: float = 0.0,
) -> dict[str, Candidate]:
    """Build Candidate objects for a set of method_ids from the symbol index."""
    candidates: dict[str, Candidate] = {}
    for mid in method_ids:
        locs = repo_index.symbol_index.exact_lookup(mid.split("#")[1].split("(")[0])
        for loc in locs:
            if loc.symbol_id == mid and loc.kind == SymbolKind.METHOD:
                snippet = _read_snippet_safe(repo_root, loc.file_path, loc.start_line, loc.end_line)
                if snippet is None:
                    break
                class_id = mid.split("#")[0] if "#" in mid else ""
                candidates[mid] = Candidate(
                    method_id=mid,
                    class_id=class_id,
                    name=loc.name,
                    kind="method",
                    file_path=loc.file_path,
                    start_line=loc.start_line,
                    end_line=loc.end_line,
                    snippet=snippet,
                    token_count=count_tokens(snippet) if snippet else 1,
                    raw_semantic_score=base_score,
                )
                break
    return candidates


def _class_anchor_method_ids(
    query: QueryAnalysis,
    repo_index: RepoIndex,
) -> tuple[set[str], set[str]]:
    """Resolve class anchors to their methods and nearby class implementations."""
    anchor_class_ids: set[str] = set()
    related_class_ids: set[str] = set()

    for anchor in query.anchor_symbols:
        for location in repo_index.symbol_index.exact_lookup(anchor):
            if location.kind != SymbolKind.CLASS:
                continue
            anchor_class_ids.add(location.symbol_id)
            related_class_ids.add(location.symbol_id)

            anchor_tokens = tokenize_identifier(location.name)
            package = location.symbol_id.rpartition(".")[0]
            for class_id, attributes in repo_index.dependency_graph.graph.nodes(data=True):
                if attributes.get("package") != package:
                    continue
                class_tokens = tokenize_identifier(
                    attributes.get("name", class_id.rpartition(".")[2])
                )
                if len(anchor_tokens & class_tokens) >= 2:
                    related_class_ids.add(class_id)

    method_ids: set[str] = set()
    anchor_method_ids: set[str] = set()
    for method_id, attributes in repo_index.call_graph.graph.nodes(data=True):
        class_id = attributes.get("class_id", "")
        if any(
            class_id == related_id or class_id.startswith(related_id + ".")
            for related_id in related_class_ids
        ):
            method_ids.add(method_id)
        if any(
            class_id == anchor_id or class_id.startswith(anchor_id + ".")
            for anchor_id in anchor_class_ids
        ):
            anchor_method_ids.add(method_id)

    return method_ids, anchor_method_ids


# ---------------------------------------------------------------------------
# Path 1: Root-cause (semantic-first + call-graph expansion)
# ---------------------------------------------------------------------------

def generate_root_cause_candidates(
    query:           QueryAnalysis,
    repo_index:      RepoIndex,
    embedding_model: EmbeddingModel,
    settings:        Settings,
) -> list[Candidate]:
    """Semantic search top-K + BFS expansion from named anchor methods.

    1. Embed query -> vector store top-K (primary semantic candidates)
    2. For each anchor symbol found in query: resolve to method IDs via
       symbol_index, then expand via call_graph.neighbors_within_hops()
       (pulls in direct callers + callees up to hop_limit=2)
    3. Deduplicate by method_id; return all
    """
    repo_root = repo_index.repo_root
    candidates: dict[str, Candidate] = {}
    failed_snippets = 0

    # Step 1: semantic search
    q_vec   = embedding_model.encode_one(query.raw_query)
    class_method_ids, anchor_class_method_ids = _class_anchor_method_ids(
        query, repo_index
    )
    semantic_top_k = settings.top_k_candidates * (2 if class_method_ids else 1)
    results = repo_index.vector_store.search(q_vec, top_k=semantic_top_k)
    semantic_candidate_ids: set[str] = set()

    for rank, sr in enumerate(results, start=1):
        meta    = sr.metadata
        if (
            rank > settings.top_k_candidates
            and meta["method_id"] not in class_method_ids
        ):
            continue
        snippet = _read_snippet_safe(repo_root, meta["file_path"], meta["start_line"], meta["end_line"])
        if snippet is None:
            failed_snippets += 1
            continue
        meta["_score"] = float(sr.score)
        candidates[meta["method_id"]] = _meta_to_candidate(meta, snippet)
        semantic_candidate_ids.add(meta["method_id"])

    # Step 2: expand named classes to their own and similarly named,
    # same-package implementation classes. This catches nested helpers such as
    # FastDatePrinter.TimeZoneNameRule when a query names FastDateFormat.
    candidates.update(
        {
            method_id: candidate
            for method_id, candidate in _build_candidate_map(
                repo_index, class_method_ids, repo_root
            ).items()
            if method_id not in candidates
        }
    )

    # Step 3: expand method anchors and methods declared by named classes via
    # the call graph. Class methods use a bounded one-hop expansion; explicit
    # method anchors retain the existing two-hop behavior.
    class_expansion_seeds = set(anchor_class_method_ids)
    if len(class_expansion_seeds) > 5:
        class_expansion_seeds.clear()
        semantic_class_seeds = sorted(
            (
                method_id
                for method_id in anchor_class_method_ids & semantic_candidate_ids
            ),
            key=lambda method_id: candidates[method_id].raw_semantic_score,
            reverse=True,
        )
        class_expansion_seeds.update(semantic_class_seeds[:2])
    method_expansion_seeds: set[str] = set()
    for anchor in query.anchor_symbols:
        locs = repo_index.symbol_index.exact_lookup(anchor)
        for loc in locs:
            if loc.kind != SymbolKind.METHOD:
                continue
            method_expansion_seeds.add(loc.symbol_id)

    graph_method_ids: set[str] = set()
    for method_id in class_expansion_seeds:
        graph_method_ids.update(
            repo_index.call_graph.neighbors_within_hops(method_id, max_hops=1)
        )
    for method_id in method_expansion_seeds:
        graph_method_ids.update(
            repo_index.call_graph.neighbors_within_hops(method_id, max_hops=2)
        )
    candidates.update(
        {
            method_id: candidate
            for method_id, candidate in _build_candidate_map(
                repo_index, graph_method_ids, repo_root
            ).items()
            if method_id not in candidates
        }
    )

    if failed_snippets and not candidates:
        raise RuntimeError("All retrieved candidates were dropped because their snippets could not be read.")
    return list(candidates.values())


# ---------------------------------------------------------------------------
# Path 2: Change-impact (graph-first + small semantic supplement)
# ---------------------------------------------------------------------------

def generate_change_impact_candidates(
    changed_symbol:  str,
    repo_index:      RepoIndex,
    embedding_model: EmbeddingModel,
    settings:        Settings,
    hop_limit:       int = 3,
) -> list[Candidate]:
    """Directed BFS (callers) from changed symbol + small semantic supplement.

    1. Resolve changed_symbol -> method IDs via symbol_index
    2. Directed reverse BFS (callers) up to hop_limit via call_graph
    3. Supplement with semantic search top-20 (lower than root-cause)
    4. Deduplicate and return
    """
    repo_root = repo_index.repo_root
    candidates: dict[str, Candidate] = {}
    failed_snippets = 0

    # Step 1+2: resolve symbol -> directed reverse BFS (callers)
    locs = repo_index.symbol_index.exact_lookup(changed_symbol)
    for loc in locs:
        if loc.kind != SymbolKind.METHOD:
            continue
        callers = repo_index.call_graph.callers_within_hops(loc.symbol_id, hops=hop_limit)
        for mid in callers | {loc.symbol_id}:
            if mid in candidates:
                continue
            name = mid.split("#")[1].split("(")[0] if "#" in mid else mid
            nlocs = repo_index.symbol_index.exact_lookup(name)
            for nloc in nlocs:
                if nloc.symbol_id == mid:
                    snippet = _read_snippet_safe(repo_root, nloc.file_path, nloc.start_line, nloc.end_line)
                    if snippet is None:
                        failed_snippets += 1
                        break
                    class_id = mid.split("#")[0] if "#" in mid else ""
                    candidates[mid] = Candidate(
                        method_id=mid,
                        class_id=class_id,
                        name=nloc.name,
                        kind="method",
                        file_path=nloc.file_path,
                        start_line=nloc.start_line,
                        end_line=nloc.end_line,
                        snippet=snippet,
                        token_count=count_tokens(snippet),
                        raw_semantic_score=0.0,
                    )
                    break

    # Step 3: semantic supplement (top-20, lower than root-cause's top-K)
    synth_query = analyze_query(changed_symbol)
    q_vec       = embedding_model.encode_one(changed_symbol)
    results     = repo_index.vector_store.search(q_vec, top_k=min(20, settings.top_k_candidates))
    for sr in results:
        meta = sr.metadata
        mid  = meta["method_id"]
        if mid in candidates:
            candidates[mid].raw_semantic_score = max(
                candidates[mid].raw_semantic_score, float(sr.score)
            )
            continue
        snippet = _read_snippet_safe(repo_root, meta["file_path"], meta["start_line"], meta["end_line"])
        if snippet is None:
            failed_snippets += 1
            continue
        meta["_score"] = float(sr.score)
        candidates[mid] = _meta_to_candidate(meta, snippet)

    if failed_snippets and not candidates:
        raise RuntimeError("All retrieved candidates were dropped because their snippets could not be read.")
    return list(candidates.values())


# ---------------------------------------------------------------------------
# Router: pick the right path based on intent
# ---------------------------------------------------------------------------

def get_candidates(
    query:           QueryAnalysis,
    repo_index:      RepoIndex,
    embedding_model: EmbeddingModel,
    settings:        Settings,
    changed_symbol:  str | None = None,
    hop_limit:       int = 3,
) -> list[Candidate]:
    """Single entry point - routes to root-cause or change-impact path."""
    if changed_symbol:
        return generate_change_impact_candidates(
            changed_symbol, repo_index, embedding_model, settings, hop_limit
        )
    return generate_root_cause_candidates(query, repo_index, embedding_model, settings)