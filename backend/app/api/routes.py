"""FastAPI router for repository indexing, queries, status, and graph inspection.

In-memory registry (_repo_registry) is backed by disk: on startup any
repo whose .pkl exists in INDEX_STORAGE_DIR is available immediately
without re-parsing.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query as FastAPIQuery

from app.acor.context_builder import build_context
from app.acor.scorer import score_candidates
from app.api.schemas import (
    GraphInspectionResponse,
    IndexRepositoryRequest,
    IndexRepositoryResponse,
    QueryRequest,
    QueryResponse,
    RepositoryStatusResponse,
)
from app.config import Settings, get_settings
from app.core import candidate_pipeline
from app.core.query_service import (
    execute_repository_query,
    inspect_repository_graphs,
)
from app.core.repo_index import RepoIndex, RepoRootMissing
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.providers import get_llm_client
from app.parsing.java_bridge import parse_repository
from app.parsing.repo_walker import compute_repo_fingerprint, read_snippet
from app.parsing.source_filter import filter_test_sources
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.tokenizer import count_tokens
from app.retrieval.vector_store import get_vector_store
from app.watcher.live_sync import start_repo_watcher, stop_repo_watcher

router = APIRouter()

# Process-level registry: repo_id -> RepoIndex
_repo_registry: dict[str, RepoIndex] = {}

def _get_repo_index(repo_id: str, settings: Settings) -> RepoIndex:
    """Return from memory or disk, distinguishing missing indexes from missing roots."""
    if repo_id in _repo_registry:
        idx = _repo_registry[repo_id]
        if idx.num_methods > 0 and idx.vector_store.count() > 0:
            if not idx.repo_root.is_dir():
                _repo_registry.pop(repo_id, None)
                raise HTTPException(
                    status_code=410,
                    detail={
                        "code": "repository_source_unavailable",
                        "message": "The index exists, but its source checkout is unavailable.",
                    },
                )
            return idx
    try:
        idx = RepoIndex.load(repo_id, settings.index_storage_dir, settings)
        _repo_registry[repo_id] = idx
        start_repo_watcher(idx, settings)
        return idx
    except RepoRootMissing as exc:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "repository_source_unavailable",
                "message": "The index exists, but its source checkout is unavailable.",
            },
        ) from exc
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "repository_not_indexed",
                "message": f"Repository '{repo_id}' is not indexed in this server's configured index directory.",
            },
        )


# ---------------------------------------------------------------------------
# POST /repositories/index
# ---------------------------------------------------------------------------

@router.post("/repositories/index", response_model=IndexRepositoryResponse)
async def index_repository(
    request:  IndexRepositoryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> IndexRepositoryResponse:
    t0 = time.perf_counter()

    repo_id = request.repo_id or (Path(request.repo_path).name if request.repo_path else "default")

    # If force_reindex is False and saved index exists, return it immediately
    pkl_file = settings.index_storage_dir / f"{repo_id}.pkl"
    if not request.force_reindex and pkl_file.is_file():
        try:
            import pickle
            with open(pkl_file, "rb") as f:
                payload = pickle.load(f)
            unparsed_files = payload.get("unparsed_files", [])
            if unparsed_files:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Stored index for '{repo_id}' is incomplete: "
                        f"{len(unparsed_files)} Java source files failed to parse."
                    ),
                )
            return IndexRepositoryResponse(
                repo_id=payload.get("repo_id", repo_id),
                num_files=payload.get("num_files", 0),
                num_classes=payload.get("num_classes", 0),
                num_methods=payload.get("num_methods", 0),
                num_call_edges=payload.get("num_call_edges", 0),
                num_import_edges=payload.get("num_import_edges", 0),
                unparsed_files=unparsed_files,
                index_time_seconds=0.0,
            )
        except HTTPException:
            raise
        except Exception:
            pass

    repo_path = Path(request.repo_path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {repo_path}")

    fingerprint = compute_repo_fingerprint(repo_path)

    # Parse
    parsed = parse_repository(repo_path, settings)
    if settings.exclude_test_sources:
        parsed = filter_test_sources(parsed)
    if parsed.unparsed_files:
        preview = ", ".join(parsed.unparsed_files[:10])
        remaining = len(parsed.unparsed_files) - min(len(parsed.unparsed_files), 10)
        suffix = f" (and {remaining} more)" if remaining else ""
        raise HTTPException(
            status_code=422,
            detail=(
                f"Indexing '{repo_id}' aborted: {len(parsed.unparsed_files)} Java "
                f"source files failed to parse: {preview}{suffix}"
            ),
        )

    if len(parsed.methods) == 0:
        # If directory has no java files, check if a valid saved index already exists
        try:
            idx = RepoIndex.load(repo_id, settings.index_storage_dir, settings)
            if idx.num_methods > 0:
                _repo_registry[repo_id] = idx
                return IndexRepositoryResponse(
                    repo_id=repo_id,
                    num_files=idx.num_files,
                    num_classes=idx.num_classes,
                    num_methods=idx.num_methods,
                    num_call_edges=idx.num_call_edges,
                    num_import_edges=idx.num_import_edges,
                    unparsed_files=idx.unparsed_files,
                    index_time_seconds=0.0,
                )
        except Exception:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"No Java files found in '{repo_path}'. Please provide a directory containing .java source files.",
        )

    # Build graphs
    call_graph       = CallGraph.build(parsed.methods, parsed.call_edges)
    dependency_graph = DependencyGraph.build(parsed.classes, parsed.import_edges)
    symbol_index     = SymbolIndex.build(parsed.classes, parsed.methods)

    # Embed methods and populate vector store
    embedding_model = get_embedding_model(settings)
    vector_store    = get_vector_store(settings, collection_name=repo_id)
    if request.force_reindex:
        vector_store.clear()

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

    # Compute whole-file token counts for touched files to enable naive baseline calculations
    file_token_counts: dict[str, int] = {}
    for m in parsed.methods:
        if m.file_path not in file_token_counts:
            try:
                full_file_path = repo_path / m.file_path
                if full_file_path.is_file():
                    content = full_file_path.read_text(encoding="utf-8", errors="replace")
                    file_token_counts[m.file_path] = count_tokens(content)
                else:
                    file_token_counts[m.file_path] = 0
            except Exception:
                file_token_counts[m.file_path] = 0

    num_files = len({m.file_path for m in parsed.methods})
    repo_index = RepoIndex(
        repo_id=repo_id,
        repo_root=repo_path.resolve(),
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
        file_token_counts=file_token_counts,
    )
    repo_index.save(settings.index_storage_dir)
    stop_repo_watcher(repo_id)
    _repo_registry[repo_id] = repo_index
    start_repo_watcher(repo_index, settings)

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
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return RepositoryStatusResponse(
            repo_id=repo_id,
            indexed=False,
            error_code=detail.get("code", "repository_unavailable"),
            message=detail.get("message", "Repository index is unavailable."),
        )


# ---------------------------------------------------------------------------
# POST /repositories/{repo_id}/query
# ---------------------------------------------------------------------------

@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
async def query_repository(
    repo_id:  str,
    request:  QueryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryResponse:
    repo_index   = _get_repo_index(repo_id, settings)
    return execute_repository_query(
        repo_id,
        request,
        settings,
        repo_index,
        embedding_model_factory=get_embedding_model,
        candidate_factory=candidate_pipeline.get_candidates,
        scorer=score_candidates,
        context_builder=build_context,
        llm_client_factory=get_llm_client,
    )


@router.get(
    "/repositories/{repo_id}/graph",
    response_model=GraphInspectionResponse,
)
async def repository_graph(
    repo_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    symbol: str | None = None,
    max_hops: int = FastAPIQuery(default=2, ge=0, le=5),
    limit: int = FastAPIQuery(default=20, ge=1, le=100),
) -> GraphInspectionResponse:
    repo_index = _get_repo_index(repo_id, settings)
    return inspect_repository_graphs(repo_id, repo_index, symbol, max_hops, limit)