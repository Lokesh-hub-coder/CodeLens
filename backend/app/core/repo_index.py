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

    def save(self, storage_dir: Path) -> None:
        """Persist graphs + symbol index.  VectorStore persists itself."""
        storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo_id":          self.repo_id,
            "repo_root":        self.repo_root,
            "fingerprint":      self.fingerprint,
            "call_graph":       self.call_graph,
            "dependency_graph": self.dependency_graph,
            "symbol_index":     self.symbol_index,
            "num_files":        self.num_files,
            "num_classes":      self.num_classes,
            "num_methods":      self.num_methods,
            "num_call_edges":   self.num_call_edges,
            "num_import_edges": self.num_import_edges,
            "unparsed_files":   self.unparsed_files,
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
        vector_store = get_vector_store(settings, collection_name=repo_id)
        return cls(
            vector_store=vector_store,
            **{k: v for k, v in payload.items()},
        )