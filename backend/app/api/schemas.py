"""Pydantic request/response schemas for the HTTP endpoints."""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class QueryIntentLabel(str, Enum):
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    BUG_EXPLANATION     = "bug_explanation"
    REFACTORING         = "refactoring"
    FEATURE_LOCATION    = "feature_location"
    GENERAL_QA          = "general_qa"


class ScoreBreakdown(BaseModel):
    semantic:   float
    call_graph: float
    dependency: float
    symbol:     float
    composite:  float


class IndexRepositoryRequest(BaseModel):
    repo_path:     str
    repo_id:       Optional[str] = None
    force_reindex: bool = False


class IndexRepositoryResponse(BaseModel):
    repo_id:            str
    num_files:          int
    num_classes:        int
    num_methods:        int
    num_call_edges:     int
    num_import_edges:   int
    unparsed_files:     list[str] = Field(default_factory=list)
    index_time_seconds: float


class QueryRequest(BaseModel):
    query:          str
    token_budget:   Optional[int] = Field(default=None, gt=0)
    changed_symbol: Optional[str] = None


class StructuredAnalysisAnswer(BaseModel):
    summary: str
    root_cause: str | None = None
    causal_chain: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    related_methods: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class EvidenceReference(BaseModel):
    rank: int
    file_path: str
    method_id: str
    symbol_name: str
    start_line: int
    end_line: int
    snippet: str
    score: ScoreBreakdown


class QueryMetrics(BaseModel):
    retrieval_latency_ms: float
    provider_latency_ms: float
    estimated_context_tokens: int
    estimated_prompt_tokens: int
    token_budget: int
    actual_provider_input_tokens: int | None = None
    actual_provider_output_tokens: int | None = None
    actual_provider_total_tokens: int | None = None
    structured_output_validated: bool
    structured_output_limitation: str | None = None


class RetrievedContextItem(BaseModel):
    method_id: str | None = None
    symbol_name: str
    file_path:   str
    start_line:  int
    end_line:    int
    snippet:     str
    token_count: int
    score:       ScoreBreakdown


class QueryResponse(BaseModel):
    repo_id:               str
    query:                 str
    detected_intent:       QueryIntentLabel
    answer:                str
    retrieved_context:     list[RetrievedContextItem] = Field(default_factory=list)
    candidates_considered: int
    candidates_selected:   int
    total_context_tokens:  int
    token_budget:          int
    naive_baseline_tokens: int
    retrieval_latency_ms:  float
    llm_latency_ms:        float
    # LLM token cost breakdown (Groq usage API fields)
    llm_prompt_tokens:     Optional[int] = None   # input tokens billed
    llm_completion_tokens: Optional[int] = None   # output tokens billed
    llm_total_tokens:      Optional[int] = None   # prompt + completion = cost basis
    structured_answer: StructuredAnalysisAnswer | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    metrics: QueryMetrics | None = None


class MCPQueryResult(BaseModel):
    summary: str
    response: QueryResponse


class RepositoryStatusResponse(BaseModel):
    repo_id:     str
    indexed:     bool
    num_methods: int = 0
    num_classes: int = 0
    fingerprint: str = ""
    error_code: str | None = None
    message: str | None = None


class GraphEdgeReference(BaseModel):
    source: str
    target: str


class GraphInspectionResponse(BaseModel):
    repo_id: str
    call_graph_nodes: int
    call_graph_edges: int
    dependency_graph_nodes: int
    dependency_graph_edges: int
    symbol_index_entries: int
    resolved_symbol: str | None = None
    call_graph_edges_near_symbol: list[GraphEdgeReference] = Field(default_factory=list)
    dependency_edges_near_symbol: list[GraphEdgeReference] = Field(default_factory=list)
