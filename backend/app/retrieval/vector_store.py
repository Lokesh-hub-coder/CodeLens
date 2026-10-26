"""Qdrant-backed vector storage for repository method embeddings."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from app.config import Settings

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


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

    @abstractmethod
    def ids(self) -> set[str]: ...

    @abstractmethod
    def remove(self, ids: list[str]) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class QdrantVectorStore(VectorStore):
    """Qdrant-backed vector store with support for local embedded or remote cloud instance."""

    def __init__(
        self,
        settings: Settings,
        collection_name: str,
        client: QdrantClient | None = None,
    ) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as rest_models

        # Sanitize collection name for Qdrant (letters, numbers, underscores, dashes)
        sanitized_name = collection_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        self.collection_name = f"codelens_{sanitized_name}"
        self.dim = settings.embedding_dim

        if client is not None:
            self.client = client
        elif settings.qdrant_url and settings.qdrant_api_key:
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

    def ids(self) -> set[str]:
        result: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=256,
                offset=offset,
                with_payload=["_chunk_id"],
                with_vectors=False,
            )
            result.update(
                point.payload["_chunk_id"]
                for point in points
                if point.payload and point.payload.get("_chunk_id")
            )
            if offset is None:
                return result

    def remove(self, ids: list[str]) -> None:
        if not ids:
            return
        from qdrant_client.http import models as rest_models
        import uuid

        point_ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
            for chunk_id in ids
        ]
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=rest_models.PointIdsList(points=point_ids),
            wait=True,
        )

    def clear(self) -> None:
        from qdrant_client.http import models as rest_models

        self.client.delete_collection(collection_name=self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=rest_models.VectorParams(
                size=self.dim,
                distance=rest_models.Distance.COSINE,
            ),
        )


def get_vector_store(
    settings: Settings,
    collection_name: str,
    client: QdrantClient | None = None,
) -> VectorStore:
    """Create the Qdrant collection isolated for this repository."""
    return QdrantVectorStore(
        settings=settings,
        collection_name=collection_name,
        client=client,
    )