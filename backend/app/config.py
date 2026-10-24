"""Central configuration - every module imports Settings and get_settings()
from here. All values can be overridden via environment variables or a
.env file (pydantic-settings BaseSettings handles both).
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = 3 levels up from backend/app/config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_JAR  = _PROJECT_ROOT / "parser-service" / "target" / "parser-service-1.0.0.jar"


@dataclass
class ACORWeights:
    """Relative importance of each scoring signal. Auto-normalised to sum 1.0."""
    semantic:   float = 0.40
    call_graph: float = 0.20
    dependency: float = 0.15
    symbol:     float = 0.25

    def __post_init__(self) -> None:
        weights = (self.semantic, self.call_graph, self.dependency, self.symbol)
        if any(not isfinite(weight) or weight < 0 for weight in weights):
            raise ValueError("ACOR weights must be finite and non-negative")
        total = self.semantic + self.call_graph + self.dependency + self.symbol
        if total <= 0:
            raise ValueError("At least one ACOR weight must be positive")
        self.semantic   /= total
        self.call_graph /= total
        self.dependency /= total
        self.symbol     /= total


class Settings(BaseSettings):
    """All tuneable knobs for the CodeLens backend.
    Env-var names mirror field names in UPPER_SNAKE_CASE.
    """
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Parser
    java_parser_jar_path: Path = _DEFAULT_JAR
    java_bin: str = "java"
    parser_classpath: str = ""
    parser_timeout_seconds: int = 120
    parser_max_heap_mb: int = 512
    parser_active_processor_count: int = 2
    exclude_test_sources: bool = True

    # Embeddings
    embedding_backend: Literal["fastembed"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Vector store
    vector_db_backend: Literal["qdrant"] = "qdrant"
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_path: Path = Path("./data/qdrant")

    # Index persistence
    index_storage_dir: Path = Path("./data/index")

    # Retrieval
    top_k_candidates: int = 50
    graph_max_hops: int = 4
    acor_use_intent_weights: bool = False

    # Ranking
    ranker_strategy: Literal["greedy", "exact"] = "greedy"
    exact_knapsack_max_candidates: int = 200
    default_token_budget: int = 8000
    acor_weight_semantic: float = 0.40
    acor_weight_call_graph: float = 0.20
    acor_weight_dependency: float = 0.15
    acor_weight_symbol: float = 0.25
    live_sync_debounce_seconds: float = 0.4

    # LLM
    llm_provider: str = "groq"         # "groq" | "openai" | "llama" | "qwen"
    llm_model_name: str = "openai/gpt-oss-120b"
    groq_api_key: str = ""
    groq_model_name: str = "openai/gpt-oss-120b"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llama_base_url: str = "http://localhost:11434/v1"
    llama_model_name: str = "llama3"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    qwen_model_name: str = "qwen-max"
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.2

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    @model_validator(mode="after")
    def validate_runtime_limits(self) -> "Settings":
        self.acor_weights
        if self.default_token_budget <= 0:
            raise ValueError("DEFAULT_TOKEN_BUDGET must be positive")
        if self.parser_max_heap_mb <= 0:
            raise ValueError("PARSER_MAX_HEAP_MB must be positive")
        if self.parser_active_processor_count <= 0:
            raise ValueError("PARSER_ACTIVE_PROCESSOR_COUNT must be positive")
        if self.top_k_candidates <= 0:
            raise ValueError("TOP_K_CANDIDATES must be positive")
        if self.graph_max_hops <= 0:
            raise ValueError("GRAPH_MAX_HOPS must be positive")
        if self.exact_knapsack_max_candidates <= 0:
            raise ValueError("EXACT_KNAPSACK_MAX_CANDIDATES must be positive")
        if self.live_sync_debounce_seconds < 0:
            raise ValueError("LIVE_SYNC_DEBOUNCE_SECONDS cannot be negative")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def acor_weights(self) -> ACORWeights:
        """Configured weights - intent-adaptive overrides live in routes.py."""
        return ACORWeights(
            semantic=self.acor_weight_semantic,
            call_graph=self.acor_weight_call_graph,
            dependency=self.acor_weight_dependency,
            symbol=self.acor_weight_symbol,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton; tests can monkeypatch or construct directly."""
    return Settings()