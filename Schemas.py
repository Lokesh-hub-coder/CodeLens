"""
Request/response contracts for the FastAPI layer.

Kept separate from the route handlers (routes.py) and from the internal
pipeline dataclasses (parsing/, graphs/, acor/) on purpose: schemas here are
the *public* API surface the frontend (and any future CLI/IDE plugin) talks
to, and are allowed to evolve independently of internal representations.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Repository indexing
# ---------------------------------------------------------------------------

class IndexRepositoryRequest(BaseModel):
    repo_path: str = Field(..., description="Absolute path or git URL of the Java repository")
    repo_id: str | None = Field(
        default=None, description="Optional caller-supplied id; generated if omitted"
    )
    force_reindex: bool = Field(
        default=False, description="Re-parse even if a cached index fingerprint matches"
    )


class IndexRepositoryResponse(BaseModel):
    repo_id: str
    num_files: int
    num_classes: int
    num_methods: int
    num_call_edges: int
    num_dependency_edges: int
    index_time_seconds: float
    status: str = "indexed"


class RepositoryStatus(BaseModel):
    repo_id: str
    status: str  # "not_found" | "indexing" | "indexed" | "failed"
    num_files: int | None = None
    last_indexed_at: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Developer query
# ---------------------------------------------------------------------------

class QueryIntentLabel(str, Enum):
    BUG_EXPLANATION = "bug_explanation"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    FEATURE_LOCATION = "feature_location"
    REFACTORING = "refactoring"
    GENERAL_QA = "general_qa"


class DeveloperQueryRequest(BaseModel):
    repo_id: str
    query: str = Field(..., min_length=1, description="Natural-language developer query")
    token_budget: int | None = Field(
        default=None, description="Override the server's default token budget for this query"
    )
    top_k_candidates: int = Field(
        default=50, ge=1, le=500, description="How many candidates ACOR scores before ranking"
    )


class ScoreBreakdown(BaseModel):
    semantic: float
    call_graph: float
    dependency: float
    symbol: float
    composite: float


class RetrievedContextItem(BaseModel):
    file_path: str
    symbol_name: str
    kind: str  # "method" | "class" | "field"
    start_line: int
    end_line: int
    token_count: int
    score: ScoreBreakdown
    snippet: str


class QueryResponse(BaseModel):
    repo_id: str
    query: str
    detected_intent: QueryIntentLabel
    answer: str
    root_cause: str | None = None
    suggested_fix: str | None = None
    retrieved_context: list[RetrievedContextItem]
    candidates_considered: int
    candidates_selected: int
    total_context_tokens: int
    token_budget: int
    naive_baseline_tokens: int | None = Field(
        default=None,
        description="Approx. tokens a whole-file/naive retrieval baseline would have used, "
        "for reporting the token-reduction ratio described in the synopsis' evaluation plan",
    )
    retrieval_latency_ms: float
    llm_latency_ms: float