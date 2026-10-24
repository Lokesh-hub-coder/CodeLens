"""
Embedding generation for both repository chunks (at index time) and
developer queries (at query time).

FastEmbed generates normalized method and query vectors. The configured
model defaults to BAAI/bge-small-en-v1.5; retrieval quality must be
measured on labeled repository tasks rather than inferred from the model
name.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.config import Settings


@dataclass(frozen=True)
class EmbeddingModel:
    model: object
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Returns an (N, dim) float32 array, L2-normalized so that a plain
        dot product equals cosine similarity."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        batch_size = 2
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            chunk_embeds = list(self.model.embed(chunk, batch_size=len(chunk)))
            all_embeddings.extend(chunk_embeds)
        arr = np.array(all_embeddings, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (arr / norms).astype(np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


@lru_cache
def _load_model(model_name: str, dim: int) -> EmbeddingModel:
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=model_name)
    return EmbeddingModel(model=model, dim=dim)


def get_embedding_model(settings: Settings) -> EmbeddingModel:
    return _load_model(settings.embedding_model, settings.embedding_dim)