"""FastAPI router - two endpoints: index a repo, query a repo.

In-memory registry (_repo_registry) is backed by disk: on startup any
repo whose .pkl exists in INDEX_STORAGE_DIR is available immediately
without re-parsing.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.acor.context_builder import build_context
from app.acor.ranker import rank_and_select
from app.acor.scorer import Candidate, score_candidates
from app.api.schemas import (
    IndexRepositoryRequest,
    IndexRepositoryResponse,
    QueryIntentLabel,
    QueryRequest,
    QueryResponse,
    RepositoryStatusResponse,
    RetrievedContextItem,
)
from app.config import ACORWeights, Settings, get_settings
from app.core.repo_index import RepoIndex
from app.evaluation.metrics import LatencyTracker
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.llm.providers import get_llm_client
from app.parsing.java_bridge import parse_repository
from app.parsing.repo_walker import compute_repo_fingerprint, read_snippet
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.query_understanding import analyze_query
from app.retrieval.tokenizer import count_tokens
from app.retrieval.vector_store import get_vector_store

router = APIRouter()

# Process-level registry: repo_id -> RepoIndex
_repo_registry: dict[str, RepoIndex] = {}

# Intent-adaptive weight profiles
_INTENT_WEIGHTS: dict[QueryIntentLabel, ACORWeights] = {
    QueryIntentLabel.ROOT_CAUSE_ANALYSIS: ACORWeights(semantic=0.35, call_graph=0.30, dependency=0.10, symbol=0.25),
    QueryIntentLabel.BUG_EXPLANATION:     ACORWeights(semantic=0.40, call_graph=0.25, dependency=0.10, symbol=0.25),
    QueryIntentLabel.FEATURE_LOCATION:    ACORWeights(semantic=0.30, call_graph=0.15, dependency=0.15, symbol=0.40),
    QueryIntentLabel.REFACTORING:         ACORWeights(semantic=0.45, call_graph=0.15, dependency=0.25, symbol=0.15),
    QueryIntentLabel.GENERAL_QA:          ACORWeights(semantic=0.50, call_graph=0.15, dependency=0.15, symbol=0.20),
}


def _get_repo_index(repo_id: str, settings: Settings) -> RepoIndex:
    """Return from memory, or load from disk, or raise 404."""
    if repo_id in _repo_registry:
        return _repo_registry[repo_id]
    try:
        idx = RepoIndex.load(repo_id, settings.index_storage_dir, settings)
        _repo_registry[repo_id] = idx
        return idx
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not indexed.")


# ---------------------------------------------------------------------------
# POST /repositories/index
# ---------------------------------------------------------------------------

@router.post("/repositories/index", response_model=IndexRepositoryResponse)
async def index_repository(
    request:  IndexRepositoryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> IndexRepositoryResponse:
    t0 = time.perf_counter()

    repo_path = Path(request.repo_path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {repo_path}")

    repo_id = request.repo_id or repo_path.name
    fingerprint = compute_repo_fingerprint(repo_path)

    # Skip re-index if fingerprint unchanged (unless forced)
    if not request.force_reindex and repo_id in _repo_registry:
        existing = _repo_registry[repo_id]
        if existing.fingerprint == fingerprint:
            return IndexRepositoryResponse(
                repo_id=repo_id,
                num_files=existing.num_files,
                num_classes=existing.num_classes,
                num_methods=existing.num_methods,
                num_call_edges=existing.num_call_edges,
                num_import_edges=existing.num_import_edges,
                unparsed_files=existing.unparsed_files,
                index_time_seconds=0.0,
            )

    # Parse
    parsed = parse_repository(repo_path, settings)

    # Build graphs
    call_graph       = CallGraph.build(parsed.methods, parsed.call_edges)
    dependency_graph = DependencyGraph.build(parsed.classes, parsed.import_edges)
    symbol_index     = SymbolIndex.build(parsed.classes, parsed.methods)

    # Embed methods and populate vector store
    embedding_model = get_embedding_model(settings)
    vector_store    = get_vector_store(settings, collection_name=repo_id)

    snippets  = []
    ids       = []
    metadatas = []
    for m in parsed.methods:
        try:
            snippet = read_snippet(repo_path / m.file_path, m.start_line, m.end_line)
        except Exception:
            snippet = m.signature
        snippets.append(snippet)
        ids.append(m.id)
        metadatas.append({
            "method_id":  m.id,
            "class_id":   m.class_id,
            "name":       m.name,
            "file_path":  m.file_path,
            "start_line": m.start_line,
            "end_line":   m.end_line,
        })

    if snippets:
        vectors = embedding_model.encode(snippets)
        vector_store.add(ids, vectors, metadatas)

    num_files = len({m.file_path for m in parsed.methods})
    repo_index = RepoIndex(
        repo_id=repo_id,
        repo_root=repo_path,
        fingerprint=fingerprint,
        call_graph=call_graph,
        dependency_graph=dependency_graph,
        symbol_index=symbol_index,
        vector_store=vector_store,
        num_files=num_files,
        num_classes=len(parsed.classes),
        num_methods=len(parsed.methods),
        num_call_edges=len(parsed.call_edges),
        num_import_edges=len(parsed.import_edges),
        unparsed_files=parsed.unparsed_files,
    )
    repo_index.save(settings.index_storage_dir)
    _repo_registry[repo_id] = repo_index

    return IndexRepositoryResponse(
        repo_id=repo_id,
        num_files=num_files,
        num_classes=len(parsed.classes),
        num_methods=len(parsed.methods),
        num_call_edges=len(parsed.call_edges),
        num_import_edges=len(parsed.import_edges),
        unparsed_files=parsed.unparsed_files,
        index_time_seconds=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------
# GET /repositories/{repo_id}/status
# ---------------------------------------------------------------------------

@router.get("/repositories/{repo_id}/status", response_model=RepositoryStatusResponse)
async def repository_status(
    repo_id:  str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RepositoryStatusResponse:
    try:
        idx = _get_repo_index(repo_id, settings)
        return RepositoryStatusResponse(
            repo_id=repo_id,
            indexed=True,
            num_methods=idx.num_methods,
            num_classes=idx.num_classes,
            fingerprint=idx.fingerprint,
        )
    except HTTPException:
        return RepositoryStatusResponse(repo_id=repo_id, indexed=False)


# ---------------------------------------------------------------------------
# POST /repositories/{repo_id}/query
# ---------------------------------------------------------------------------

@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
async def query_repository(
    repo_id:  str,
    request:  QueryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryResponse:
    from app.core.candidate_pipeline import get_candidates

    repo_index   = _get_repo_index(repo_id, settings)
    query        = analyze_query(request.query)
    token_budget = request.token_budget or settings.default_token_budget

    t_retrieval = LatencyTracker()
    t_retrieval.start()

    embedding_model = get_embedding_model(settings)

    # Candidate generation: root-cause (semantic + graph) or change-impact (graph + supplement)
    # Routes automatically based on intent / changed_symbol param
    candidates = get_candidates(
        query=query,
        repo_index=repo_index,
        embedding_model=embedding_model,
        settings=settings,
    )

    naive_baseline_tokens = sum(c.token_count for c in candidates)

    # ACOR: score with intent-adaptive weights
    weights = _INTENT_WEIGHTS.get(query.intent, settings.acor_weights)
    scored  = score_candidates(
        candidates, query,
        repo_index.call_graph,
        repo_index.dependency_graph,
        repo_index.symbol_index,
        weights,
        max_hops=settings.graph_max_hops,
    )

    # ACOR: rank + select within token budget
    rank_result  = rank_and_select(scored, token_budget, settings)
    context_text = build_context(rank_result.selected, repo_index.repo_root)
    retrieval_ms = t_retrieval.stop_ms()

    # LLM call
    t_llm = LatencyTracker()
    t_llm.start()
    llm_response = get_llm_client(settings).generate(
        SYSTEM_PROMPT,
        build_user_prompt(request.query, query.intent, context_text),
    )
    llm_ms = t_llm.stop_ms()

    retrieved_context = [
        RetrievedContextItem(
            symbol_name=c.name,
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            snippet=c.snippet,
            token_count=c.token_count,
            score=c.score,
        )
        for c in rank_result.selected
    ]

    return QueryResponse(
        repo_id=repo_id,
        query=request.query,
        detected_intent=query.intent,
        answer=llm_response.text,
        retrieved_context=retrieved_context,
        candidates_considered=len(candidates),
        candidates_selected=len(rank_result.selected),
        total_context_tokens=rank_result.total_tokens,
        token_budget=token_budget,
        naive_baseline_tokens=naive_baseline_tokens,
        retrieval_latency_ms=retrieval_ms,
        llm_latency_ms=llm_ms,
    )