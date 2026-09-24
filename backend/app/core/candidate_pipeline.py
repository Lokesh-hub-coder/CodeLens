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

from app.acor.scorer import Candidate
from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.symbol_index import SymbolKind
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.query_understanding import QueryAnalysis, analyze_query
from app.retrieval.tokenizer import count_tokens
from app.api.schemas import QueryIntentLabel


def _read_snippet_safe(repo_root: Path, file_path: str, start: int, end: int, name: str = "") -> str:
    """Read source lines [start, end] inclusive; return summary fallback if file is moved."""
    try:
        from app.parsing.repo_walker import read_snippet
        content = read_snippet(repo_root / file_path, start, end)
        if content:
            return content
    except Exception:
        pass
    return f"// Method: {name or file_path}\n// File: {file_path} (lines {start}-{end})"


def _meta_to_candidate(meta: dict, snippet: str) -> Candidate:
    snip = snippet or f"// Method: {meta.get('name', '')}\n// File: {meta.get('file_path', '')} (lines {meta.get('start_line', 0)}-{meta.get('end_line', 0)})"
    return Candidate(
        method_id=meta["method_id"],
        class_id=meta["class_id"],
        name=meta["name"],
        kind="method",
        file_path=meta["file_path"],
        start_line=meta["start_line"],
        end_line=meta["end_line"],
        snippet=snip,
        token_count=max(count_tokens(snip), 5),
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

    # Step 1: semantic search
    q_vec   = embedding_model.encode_one(query.raw_query)
    results = repo_index.vector_store.search(q_vec, top_k=settings.top_k_candidates)

    for sr in results:
        meta    = sr.metadata
        snippet = _read_snippet_safe(repo_root, meta["file_path"], meta["start_line"], meta["end_line"])
        meta["_score"] = float(sr.score)
        candidates[meta["method_id"]] = _meta_to_candidate(meta, snippet)

    # Step 2: call-graph expansion from anchor symbols (hop_limit=2 for root-cause)
    for anchor in query.anchor_symbols:
        locs = repo_index.symbol_index.exact_lookup(anchor)
        for loc in locs:
            if loc.kind != SymbolKind.METHOD:
                continue
            neighbors = repo_index.call_graph.neighbors_within_hops(loc.symbol_id, max_hops=2)
            for mid in neighbors:
                if mid in candidates:
                    continue
                # Look up metadata from symbol index
                name = mid.split("#")[1].split("(")[0] if "#" in mid else mid
                neighbor_locs = repo_index.symbol_index.exact_lookup(name)
                for nloc in neighbor_locs:
                    if nloc.symbol_id == mid:
                        snippet = _read_snippet_safe(repo_root, nloc.file_path, nloc.start_line, nloc.end_line)
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
                            token_count=count_tokens(snippet) if snippet else 1,
                            raw_semantic_score=0.0,  # graph-expansion candidates have no semantic score
                        )
                        break

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
    """BFS from changed symbol + small semantic supplement.

    1. Resolve changed_symbol -> method IDs via symbol_index
    2. BFS callers/callees up to hop_limit via call_graph
    3. Also expand dependency_graph for the changed symbol's class
    4. Supplement with semantic search top-20 (lower than root-cause)
    5. Deduplicate and return
    """
    repo_root = repo_index.repo_root
    candidates: dict[str, Candidate] = {}

    # Step 1+2: resolve symbol -> BFS expansion
    locs = repo_index.symbol_index.exact_lookup(changed_symbol)
    for loc in locs:
        if loc.kind != SymbolKind.METHOD:
            continue
        neighbors = repo_index.call_graph.neighbors_within_hops(loc.symbol_id, max_hops=hop_limit)
        for mid in neighbors | {loc.symbol_id}:
            if mid in candidates:
                continue
            name = mid.split("#")[1].split("(")[0] if "#" in mid else mid
            nlocs = repo_index.symbol_index.exact_lookup(name)
            for nloc in nlocs:
                if nloc.symbol_id == mid:
                    snippet = _read_snippet_safe(repo_root, nloc.file_path, nloc.start_line, nloc.end_line)
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
                        token_count=count_tokens(snippet) if snippet else 1,
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
        meta["_score"] = float(sr.score)
        candidates[mid] = _meta_to_candidate(meta, snippet)

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