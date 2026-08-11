"""
HTTP surface of CodeLens AI. Two endpoints map directly onto the two
things the synopsis' pipeline diagram does:

  POST /repositories/index         Developer Query is absent; this runs
                                    "Repository Parser -> Call Graph +
                                    Dependency Graph + Symbol Index" once
                                    per repository and embeds every method
                                    into the vector store.

  POST /repositories/{id}/query    Runs the rest of the diagram: Query
                                    Understanding -> ACOR -> Optimized
                                    Context -> LLM -> answer.

Repository state (graphs, symbol index, vector store, file paths) is kept
in a simple in-memory registry keyed by repo_id. That is a conscious MVP
simplification worth flagging explicitly rather than hiding: it means the
index is lost on server restart and does not work across multiple worker
processes. The natural upgrade -- persisting each RepoIndex (pickle the
graphs/symbol index, rely on Chroma's own on-disk persistence for vectors)
under INDEX_STORAGE_DIR and lazily reloading on lookup -- is flagged again
in docs/architecture.md rather than built here, since correctness of the
pipeline matters more than deployment robustness at this stage of the
project (Week 5-10 per the synopsis' timeline).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.acor.context_builder import build_context, estimate_naive_baseline_tokens
from app.acor.ranker import rank_and_select
from app.acor.scorer import prepare_candidates, score_candidates
from app.acor.tokenizer import count_tokens
from app.api.schemas import (
    IndexRepositoryRequest,
    IndexRepositoryResponse,
    DeveloperQueryRequest,
    QueryResponse,
    RepositoryStatus,
    RetrievedContextItem,
    ScoreBreakdown,
)
from app.config import Settings, get_settings
from app.evaluation.metrics import LatencyTracker
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.llm.providers import get_llm_client
from app.parsing.java_bridge import parse_repository
from app.parsing.repo_walker import list_java_files
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.query_understanding import analyze_query
from app.retrieval.vector_store import VectorStore, get_vector_store

router = APIRouter()


@dataclass
class RepoIndex:
    """Everything the query endpoint needs for one indexed repository."""

    repo_root: Path
    call_graph: CallGraph
    dependency_graph: DependencyGraph
    symbol_index: SymbolIndex
    vector_store: VectorStore
    file_token_counts: dict[str, int]  # whole-file token counts, for the naive-baseline comparison


# In-memory registry -- see module docstring for the persistence caveat.
_REPO_REGISTRY: dict[str, RepoIndex] = {}


@router.post("/repositories/index", response_model=IndexRepositoryResponse)
def index_repository(
    request: IndexRepositoryRequest, settings: Settings = Depends(get_settings)
) -> IndexRepositoryResponse:
    start = time.perf_counter()
    repo_root = Path(request.repo_path)
    if not repo_root.exists():
        raise HTTPException(status_code=404, detail=f"repo_path does not exist: {repo_root}")

    repo_id = request.repo_id or str(uuid.uuid4())
    if repo_id in _REPO_REGISTRY and not request.force_reindex:
        raise HTTPException(
            status_code=409,
            detail=f"repo_id {repo_id!r} is already indexed; pass force_reindex=true to re-parse",
        )

    try:
        parsed = parse_repository(repo_root, settings)
    except Exception as exc:  # parser-service failures become a clean 502, not a raw traceback
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    call_graph = CallGraph.build(parsed.methods, parsed.call_edges)
    dependency_graph = DependencyGraph.build(parsed.classes, parsed.import_edges)
    symbol_index = SymbolIndex.build(parsed.classes, parsed.methods)

    embedding_model = get_embedding_model(settings)
    vector_store = get_vector_store(settings, collection_name=repo_id)

    # Embed one chunk per method. Class-level/file-level chunking is a
    # reasonable extension (see docs/architecture.md) but methods are the
    # right default granularity: they're small enough to keep token cost
    # per candidate low (helping the ranker fit more *distinct* relevant
    # things under budget) and large enough to be self-contained.
    if parsed.methods:
        snippets = [
            _read_snippet_safe(repo_root / m.file_path, m.start_line, m.end_line)
            for m in parsed.methods
        ]
        vectors = embedding_model.encode(snippets)
        vector_store.add(
            ids=[m.id for m in parsed.methods],
            vectors=vectors,
            metadatas=[
                {
                    "file_path": m.file_path,
                    "start_line": m.start_line,
                    "end_line": m.end_line,
                    "class_id": m.class_id,
                    "name": m.name,
                    "kind": "method",
                }
                for m in parsed.methods
            ],
        )

    file_token_counts = {
        java_file.relative_path: count_tokens(java_file.absolute_path.read_text(errors="replace"))
        for java_file in list_java_files(repo_root)
    }

    _REPO_REGISTRY[repo_id] = RepoIndex(
        repo_root=repo_root,
        call_graph=call_graph,
        dependency_graph=dependency_graph,
        symbol_index=symbol_index,
        vector_store=vector_store,
        file_token_counts=file_token_counts,
    )

    return IndexRepositoryResponse(
        repo_id=repo_id,
        num_files=len(file_token_counts),
        num_classes=len(parsed.classes),
        num_methods=len(parsed.methods),
        num_call_edges=call_graph.num_edges,
        num_dependency_edges=dependency_graph.num_edges,
        index_time_seconds=time.perf_counter() - start,
    )


@router.get("/repositories/{repo_id}/status", response_model=RepositoryStatus)
def repository_status(repo_id: str) -> RepositoryStatus:
    repo_index = _REPO_REGISTRY.get(repo_id)
    if repo_index is None:
        return RepositoryStatus(repo_id=repo_id, status="not_found")
    return RepositoryStatus(repo_id=repo_id, status="indexed", num_files=len(repo_index.file_token_counts))


@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
def query_repository(
    repo_id: str, request: DeveloperQueryRequest, settings: Settings = Depends(get_settings)
) -> QueryResponse:
    repo_index = _REPO_REGISTRY.get(repo_id)
    if repo_index is None:
        raise HTTPException(status_code=404, detail=f"repo_id {repo_id!r} is not indexed")

    token_budget = request.token_budget or settings.default_token_budget

    retrieval_timer = LatencyTracker()
    retrieval_timer.start()

    # --- Query Understanding ---
    query_analysis = analyze_query(request.query)

    # --- Candidate retrieval (semantic pre-filter narrows the whole repo
    #     down to top_k_candidates before ACOR's finer-grained scoring) ---
    embedding_model = get_embedding_model(settings)
    query_vector = embedding_model.encode_one(request.query)
    search_results = repo_index.vector_store.search(query_vector, top_k=request.top_k_candidates)
    if not search_results:
        raise HTTPException(
            status_code=422,
            detail="No candidates retrieved -- is the repository empty, or did indexing fail silently?",
        )
    candidates = prepare_candidates(search_results, repo_index.repo_root)

    # --- ACOR: score -> rank & select under budget -> build context ---
    scored = score_candidates(
        candidates,
        query_analysis,
        repo_index.call_graph,
        repo_index.dependency_graph,
        repo_index.symbol_index,
        settings.acor_weights,
        settings.graph_max_hops,
    )
    selection = rank_and_select(scored, token_budget, settings)
    built_context = build_context(selection)

    retrieval_latency_ms = retrieval_timer.stop_ms()

    # --- LLM ---
    llm_timer = LatencyTracker()
    llm_timer.start()
    llm_client = get_llm_client(settings)
    user_prompt = build_user_prompt(request.query, query_analysis.intent, built_context.text)
    llm_response = llm_client.generate(SYSTEM_PROMPT, user_prompt)
    llm_latency_ms = llm_timer.stop_ms()

    naive_baseline_tokens = estimate_naive_baseline_tokens(
        {c.file_path for c in candidates}, repo_index.file_token_counts
    )

    return QueryResponse(
        repo_id=repo_id,
        query=request.query,
        detected_intent=query_analysis.intent,
        answer=llm_response.text,
        retrieved_context=[
            RetrievedContextItem(
                file_path=c.file_path,
                symbol_name=c.name,
                kind=c.kind,
                start_line=c.start_line,
                end_line=c.end_line,
                token_count=c.token_count,
                score=ScoreBreakdown(**c.score.__dict__),
                snippet=c.snippet,
            )
            for c in selection.selected
        ],
        candidates_considered=len(candidates),
        candidates_selected=len(selection.selected),
        total_context_tokens=built_context.total_tokens,
        token_budget=token_budget,
        naive_baseline_tokens=naive_baseline_tokens,
        retrieval_latency_ms=retrieval_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )


def _read_snippet_safe(file_path: Path, start_line: int, end_line: int) -> str:
    from app.parsing.repo_walker import read_snippet

    try:
        return read_snippet(file_path, start_line, end_line)
    except (OSError, ValueError):
        return ""