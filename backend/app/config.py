"""Central configuration - every module imports Settings and get_settings()
from here. All values can be overridden via environment variables or a
.env file (pydantic-settings BaseSettings handles both).
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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
        total = self.semantic + self.call_graph + self.dependency + self.symbol
        if total <= 0:
            raise ValueError("At least one ACORWeight must be positive")
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
    parser_timeout_seconds: int = 120

    # Embeddings
    embedding_backend: str = "fastembed"  # "fastembed" | "sentence_transformers"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Vector store
    vector_db_backend: str = "qdrant"   # "qdrant" | "faiss" | "chroma"
    chroma_persist_dir: Path = Path("./data/chroma")
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
    ranker_strategy: str = "greedy"    # "greedy" | "exact"
    exact_knapsack_max_candidates: int = 200
    default_token_budget: int = 8000

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

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def acor_weights(self) -> ACORWeights:
        """Default weights - intent-adaptive overrides live in routes.py."""
        return ACORWeights()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton; tests can monkeypatch or construct directly."""
    return Settings()