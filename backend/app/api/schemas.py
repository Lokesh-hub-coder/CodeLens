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
    token_budget:   Optional[int] = None
    changed_symbol: Optional[str] = None


class RetrievedContextItem(BaseModel):
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


class RepositoryStatusResponse(BaseModel):
    repo_id:     str
    indexed:     bool
    num_methods: int = 0
    num_classes: int = 0
    fingerprint: str = ""