"""
Vector store abstraction over the two backends named in the synopsis
(FAISS / ChromaDB), picked at runtime via VECTOR_DB_BACKEND.

Why offer both instead of just picking one: they sit at different points on
the "simplicity vs. features" tradeoff the team should be able to discuss
in the evaluation chapter. FAISS (IndexFlatIP here) is an in-process
library -- no server, exact search, trivial to reason about, and plenty
fast at single-repository scale (thousands of methods, not millions).
ChromaDB adds persistence and metadata filtering out of the box, which
matters once the project needs to keep several repositories'
indices around simultaneously without re-embedding on every server
restart. Both are exposed behind the same three methods so the ACOR layer
above never needs to know which one is active.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from app.config import Settings


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    score: float  # cosine similarity, since vectors are pre-normalized
    metadata: dict


class VectorStore(ABC):
    @abstractmethod
    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None: ...

    @abstractmethod
    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]: ...

    @abstractmethod
    def count(self) -> int: ...


class FaissVectorStore(VectorStore):
    """Exact inner-product search. Vectors must already be L2-normalized
    (embeddings.py guarantees this) so inner product == cosine similarity."""

    def __init__(self, dim: int) -> None:
        import faiss  # local import: keeps `faiss` an optional dependency

        self._faiss = faiss
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self._id_by_row: list[str] = []
        self._metadata_by_id: dict[str, dict] = {}

    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None:
        if len(ids) != vectors.shape[0] or len(ids) != len(metadatas):
            raise ValueError("ids, vectors, and metadatas must have matching lengths")
        self.index.add(vectors)
        self._id_by_row.extend(ids)
        for chunk_id, meta in zip(ids, metadatas):
            self._metadata_by_id[chunk_id] = meta

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        if self.index.ntotal == 0:
            return []
        top_k = min(top_k, self.index.ntotal)
        query = query_vector.reshape(1, -1).astype(np.float32)
        scores, row_indices = self.index.search(query, top_k)
        results: list[SearchResult] = []
        for score, row in zip(scores[0], row_indices[0]):
            if row == -1:
                continue
            chunk_id = self._id_by_row[row]
            results.append(
                SearchResult(chunk_id=chunk_id, score=float(score), metadata=self._metadata_by_id[chunk_id])
            )
        return results

    def count(self) -> int:
        return self.index.ntotal


class ChromaVectorStore(VectorStore):
    """Persistent, metadata-filterable store -- useful once multiple
    repositories' indices need to coexist across server restarts without
    re-embedding everything on boot."""

    def __init__(self, persist_dir: str, collection_name: str) -> None:
        import chromadb  # local import: keeps `chromadb` an optional dependency

        client = chromadb.PersistentClient(path=persist_dir)
        self.collection = client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None:
        self.collection.add(ids=ids, embeddings=vectors.tolist(), metadatas=metadatas)

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        response = self.collection.query(query_embeddings=[query_vector.tolist()], n_results=top_k)
        results: list[SearchResult] = []
        ids = response.get("ids", [[]])[0]
        distances = response.get("distances", [[]])[0]
        metadatas = response.get("metadatas", [[]])[0]
        for chunk_id, distance, meta in zip(ids, distances, metadatas):
            # Chroma returns cosine *distance*; convert back to similarity
            # so callers never need to know which backend is active.
            results.append(SearchResult(chunk_id=chunk_id, score=1.0 - distance, metadata=meta or {}))
        return results

    def count(self) -> int:
        return self.collection.count()


class QdrantVectorStore(VectorStore):
    """Qdrant-backed vector store with support for local embedded or remote cloud instance."""

    def __init__(self, settings: Settings, collection_name: str) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as rest_models

        # Sanitize collection name for Qdrant (letters, numbers, underscores, dashes)
        sanitized_name = collection_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        self.collection_name = f"codelens_{sanitized_name}"
        self.dim = settings.embedding_dim

        if settings.qdrant_url and settings.qdrant_api_key:
            self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        elif settings.qdrant_url:
            self.client = QdrantClient(url=settings.qdrant_url)
        else:
            settings.qdrant_path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(settings.qdrant_path))

        # Ensure collection exists
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in collections:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=rest_models.VectorParams(
                    size=self.dim,
                    distance=rest_models.Distance.COSINE,
                ),
            )

    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None:
        from qdrant_client.http import models as rest_models
        import uuid

        if len(ids) != vectors.shape[0] or len(ids) != len(metadatas):
            raise ValueError("ids, vectors, and metadatas must have matching lengths")

        points = []
        for chunk_id, vec, meta in zip(ids, vectors, metadatas):
            # Qdrant point IDs need to be int or UUID
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
            payload = dict(meta)
            payload["_chunk_id"] = chunk_id
            points.append(
                rest_models.PointStruct(
                    id=point_id,
                    vector=vec.tolist(),
                    payload=payload,
                )
            )

        BATCH = 100
        for i in range(0, len(points), BATCH):
            self.client.upsert(
                collection_name=self.collection_name,
                points=points[i : i + BATCH],
            )

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        if self.count() == 0:
            return []
        
        vec_list = query_vector.tolist() if isinstance(query_vector, np.ndarray) else query_vector
        # qdrant-client >= 1.10 uses query_points
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=vec_list,
                limit=top_k,
            )
            hits = response.points
        else:
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=vec_list,
                limit=top_k,
            )
        results: list[SearchResult] = []
        for hit in hits:
            payload = hit.payload or {}
            chunk_id = payload.get("_chunk_id", str(hit.id))
            clean_meta = {k: v for k, v in payload.items() if k != "_chunk_id"}
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    score=float(hit.score),
                    metadata=clean_meta,
                )
            )
        return results

    def count(self) -> int:
        info = self.client.get_collection(collection_name=self.collection_name)
        return info.points_count or 0


def get_vector_store(settings: Settings, collection_name: str) -> VectorStore:
    """Factory selecting the backend from config. `collection_name` is
    typically the repo_id, so each indexed repository gets an isolated
    collection/index rather than one giant mixed index."""
    if settings.vector_db_backend == "qdrant":
        return QdrantVectorStore(settings=settings, collection_name=collection_name)
    if settings.vector_db_backend == "faiss":
        return FaissVectorStore(dim=settings.embedding_dim)
    if settings.vector_db_backend == "chroma":
        return ChromaVectorStore(
            persist_dir=str(settings.chroma_persist_dir), collection_name=collection_name
        )
    raise ValueError(f"Unknown vector_db_backend: {settings.vector_db_backend!r}")