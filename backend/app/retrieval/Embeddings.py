"""
Embedding generation for both repository chunks (at index time) and
developer queries (at query time).

Model choice matters more than it might look. A general-purpose sentence
embedding model (e.g. all-MiniLM-L6-v2) is trained on natural-language
similarity and treats code largely as unusual-looking text -- it will
happily rank a comment-heavy, semantically-irrelevant method above a
dense, correct one just because the comments share vocabulary with the
query. The default here is a *code-aware* bi-encoder instead
(flax-sentence-embeddings/st-codesearch-distilroberta-base, trained
specifically for code search); swap EMBEDDING_MODEL in .env for any other
sentence-transformers-compatible code model (e.g. a CodeBERT/UniXCoder/
jina-embeddings-v2-base-code variant) without touching pipeline code
elsewhere -- see docs/ACOR_algorithm.md, "Embedding model choice".
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.config import Settings


@dataclass(frozen=True)
class EmbeddingModel:
    model: object  # sentence_transformers.SentenceTransformer, typed loosely to avoid a hard import at module load
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Returns an (N, dim) float32 array, L2-normalized so that a plain
        dot product equals cosine similarity (both vector_store.py backends
        rely on this)."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vectors.astype(np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


@lru_cache
def get_embedding_model(settings: Settings) -> EmbeddingModel:
    """Cached loader -- sentence-transformers model loading is expensive
    (seconds, plus a first-time download), so every caller within a process
    shares one loaded model rather than re-loading per request."""
    # Imported lazily so `import app.config` alone (e.g. in tests that don't
    # touch embeddings) never pays sentence-transformers' import cost or
    # requires it to be installed.
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.embedding_model)
    return EmbeddingModel(model=model, dim=settings.embedding_dim)