"""
Centralized configuration for CodeLens AI.

Every tunable knob mentioned in the synopsis (token budget, ACOR weights,
retrieval backend, LLM provider) lives here so that experiments -- and the
project's Chapter 7 evaluation ablations -- only ever touch one file or one
`.env`, never scattered magic numbers in the pipeline code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ACORWeights(BaseSettings):
    """Weights for the composite relevance score. See acor/scorer.py."""

    semantic: float = 0.40
    call_graph: float = 0.20
    dependency: float = 0.15
    symbol: float = 0.25

    def normalized(self) -> dict[str, float]:
        total = self.semantic + self.call_graph + self.dependency + self.symbol
        if total <= 0:
            raise ValueError("ACOR weights must sum to a positive number")
        return {
            "semantic": self.semantic / total,
            "call_graph": self.call_graph / total,
            "dependency": self.dependency / total,
            "symbol": self.symbol / total,
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Storage ---
    repo_storage_dir: Path = Path("./data/repos")
    index_storage_dir: Path = Path("./data/index")

    # --- Java parser ---
    java_parser_jar_path: Path = Path("./parser-service/target/parser-service-1.0.0.jar")
    java_bin: str = "java"
    parser_timeout_seconds: int = 120

    # --- Embeddings / vector store ---
    embedding_model: str = "flax-sentence-embeddings/st-codesearch-distilroberta-base"
    embedding_dim: int = 768
    vector_db_backend: str = "faiss"  # "faiss" | "chroma"
    chroma_persist_dir: Path = Path("./data/chroma")

    # --- ACOR ---
    default_token_budget: int = 8000
    acor_weight_semantic: float = 0.40
    acor_weight_call_graph: float = 0.20
    acor_weight_dependency: float = 0.15
    acor_weight_symbol: float = 0.25
    graph_max_hops: int = 4
    ranker_strategy: str = "greedy"  # "greedy" | "exact_knapsack"
    exact_knapsack_max_candidates: int = 200

    # --- LLM ---
    llm_provider: str = "openai"  # "openai" | "llama" | "qwen"
    llm_model_name: str = "gpt-4o-mini"
    llm_max_output_tokens: int = 1024
    llm_temperature: float = 0.2

    openai_api_key: str = Field(default="")
    openai_base_url: str = "https://api.openai.com/v1"

    llama_base_url: str = "http://localhost:11434"
    llama_model_name: str = "llama3.1:8b"

    qwen_api_key: str = Field(default="")
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model_name: str = "qwen2.5-coder-32b-instruct"

    # --- API server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_allow_origins: str = "http://localhost:5173"

    @field_validator("vector_db_backend")
    @classmethod
    def _validate_backend(cls, v: str) -> str:
        allowed = {"faiss", "chroma"}
        if v not in allowed:
            raise ValueError(f"vector_db_backend must be one of {allowed}, got {v!r}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        allowed = {"openai", "llama", "qwen"}
        if v not in allowed:
            raise ValueError(f"llm_provider must be one of {allowed}, got {v!r}")
        return v

    @property
    def acor_weights(self) -> ACORWeights:
        return ACORWeights(
            semantic=self.acor_weight_semantic,
            call_graph=self.acor_weight_call_graph,
            dependency=self.acor_weight_dependency,
            symbol=self.acor_weight_symbol,
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Settings are cached -- construct once, reuse everywhere (incl. FastAPI Depends)."""
    return Settings()
