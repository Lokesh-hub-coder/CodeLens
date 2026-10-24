import numpy as np

from app.retrieval.embeddings import EmbeddingModel


class RecordingEmbeddingBackend:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed(self, texts: list[str], batch_size: int):
        self.batch_sizes.append(batch_size)
        return [np.ones(4, dtype=np.float32) for _ in texts]


def test_embedding_batches_are_bounded_and_keep_order():
    backend = RecordingEmbeddingBackend()
    model = EmbeddingModel(backend, dim=4)

    vectors = model.encode([f"method {index}" for index in range(19)])

    assert vectors.shape == (19, 4)
    assert backend.batch_sizes == [2, 2, 2, 2, 2, 2, 2, 2, 2, 1]
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
