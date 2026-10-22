"""RepoIndex - bundles all per-repository in-memory data structures and
handles serialisation to / deserialisation from disk.

MVP design: graphs and symbol index are pickled; vector store persists
itself (Chroma) or is rebuilt from saved embeddings (FAISS).
Data is lost on restart for FAISS - switch VECTOR_DB_BACKEND=chroma for
persistence across restarts.
"""
from __future__ import annotations
import pickle
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.retrieval.vector_store import VectorStore, get_vector_store


class RepoRootMissing(FileNotFoundError):
    """Raised when an index points to a repository root that is unavailable."""


@dataclass
class RepoIndex:
    repo_id:          str
    repo_root:        Path
    fingerprint:      str
    call_graph:       CallGraph
    dependency_graph: DependencyGraph
    symbol_index:     SymbolIndex
    vector_store:     VectorStore
    num_files:        int
    num_classes:      int
    num_methods:      int
    num_call_edges:   int
    num_import_edges: int = 0
    unparsed_files:   list[str] = field(default_factory=list)
    file_token_counts: dict[str, int] = field(default_factory=dict)

    def save(self, storage_dir: Path) -> None:
        """Persist graphs + symbol index.  VectorStore persists itself."""
        storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo_id":           self.repo_id,
            "repo_root":         self.repo_root.resolve(),
            "fingerprint":       self.fingerprint,
            "call_graph":        self.call_graph,
            "dependency_graph":  self.dependency_graph,
            "symbol_index":      self.symbol_index,
            "num_files":         self.num_files,
            "num_classes":       self.num_classes,
            "num_methods":       self.num_methods,
            "num_call_edges":    self.num_call_edges,
            "num_import_edges":  self.num_import_edges,
            "unparsed_files":    self.unparsed_files,
            "file_token_counts": self.file_token_counts,
        }
        path = storage_dir / f"{self.repo_id}.pkl"
        with open(path, "wb") as f:
            pickle.dump(payload, f)

    @classmethod
    def load(
        cls,
        repo_id:     str,
        storage_dir: Path,
        settings:    Settings,
    ) -> "RepoIndex":
        """Load from disk.  Raises FileNotFoundError if not indexed yet."""
        path = storage_dir / f"{repo_id}.pkl"
        if not path.exists():
            raise FileNotFoundError(f"No index found for repo_id='{repo_id}'")
        with open(path, "rb") as f:
            payload = pickle.load(f)
        repo_root = Path(payload["repo_root"])
        if not repo_root.is_absolute() or not repo_root.is_dir():
            raise RepoRootMissing(
                f"Stored repository root '{payload['repo_root']}' is missing or is not a directory."
            )
        payload["repo_root"] = repo_root
        vector_store = get_vector_store(settings, collection_name=repo_id)
        vec_cache = storage_dir / f"{repo_id}.vectors.pkl"
        if vec_cache.exists() and vector_store.count() == 0:
            with open(vec_cache, "rb") as vf:
                ids, all_vecs_np, metadatas = pickle.load(vf)
            vector_store.add(ids, all_vecs_np, metadatas)
        elif vector_store.count() == 0:
            from app.graphs.symbol_index import SymbolKind
            from app.retrieval.embeddings import get_embedding_model
            import re
            import numpy as np

            symbol_index = payload.get("symbol_index")
            if symbol_index:
                embedding_model = get_embedding_model(settings)
                texts, ids, metadatas = [], [], []
                seen_ids: set = set()
                for name, locs in symbol_index._exact.items():
                    for loc in locs:
                        if loc.kind == SymbolKind.METHOD and loc.symbol_id not in seen_ids:
                            seen_ids.add(loc.symbol_id)
                            class_part = loc.symbol_id.split("#")[0] if "#" in loc.symbol_id else ""
                            class_name = class_part.split(".")[-1] if class_part else ""
                            readable = re.sub(r"([a-z])([ A-Z])", r"\1 \2", loc.name).replace("_", " ").lower()
                            text_repr = f"{class_name} {loc.name} {readable}".strip()
                            texts.append(text_repr)
                            ids.append(loc.symbol_id)
                            metadatas.append({
                                "method_id":  loc.symbol_id,
                                "class_id":   class_part,
                                "name":       loc.name,
                                "file_path":  loc.file_path,
                                "start_line": loc.start_line,
                                "end_line":   loc.end_line,
                            })
                if texts:
                    BATCH = 64
                    all_vecs = []
                    for i in range(0, len(texts), BATCH):
                        batch = texts[i: i + BATCH]
                        all_vecs.append(embedding_model.encode(batch))
                    all_vecs_np = np.vstack(all_vecs)
                    vector_store.add(ids, all_vecs_np, metadatas)
                    try:
                        with open(vec_cache, "wb") as vf:
                            pickle.dump((ids, all_vecs_np, metadatas), vf)
                    except Exception:
                        pass

        return cls(
            vector_store=vector_store,
            **{k: v for k, v in payload.items()},
        )