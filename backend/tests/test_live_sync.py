from __future__ import annotations

from pathlib import Path
from threading import Event

import numpy as np
import pytest
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex, SymbolKind
from app.parsing.java_bridge import CallEdge, ClassInfo, ImportEdge, MethodInfo, ParsedRepo
from app.watcher import live_sync


class MemoryVectorStore:
    def __init__(self, ids: set[str] | None = None) -> None:
        self.points = {
            item_id: (np.zeros((4,), dtype=np.float32), {"method_id": item_id})
            for item_id in ids or set()
        }

    def add(self, ids: list[str], vectors: np.ndarray, metadatas: list[dict]) -> None:
        for item_id, vector, metadata in zip(ids, vectors, metadatas):
            self.points[item_id] = (vector, metadata)

    def ids(self) -> set[str]:
        return set(self.points)

    def remove(self, ids: list[str]) -> None:
        for item_id in ids:
            self.points.pop(item_id, None)


class FakeEmbeddingModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


def _method(class_id: str, name: str, file_path: str, line: int) -> MethodInfo:
    return MethodInfo(
        id=f"{class_id}#{name}()",
        class_id=class_id,
        name=name,
        signature=f"{name}()",
        file_path=file_path,
        start_line=line,
        end_line=line,
        modifiers=["public"],
    )


def _class(class_id: str, file_path: str, name: str) -> ClassInfo:
    return ClassInfo(
        id=class_id,
        file_path=file_path,
        name=name,
        package="com.example",
        start_line=1,
        end_line=5,
        imports=[],
    )


def _parsed(classes: list[ClassInfo], methods: list[MethodInfo]) -> ParsedRepo:
    edges = []
    imports = []
    if len(methods) > 1:
        edges.append(CallEdge(methods[1].id, methods[0].id))
        imports.append(ImportEdge(methods[1].class_id, methods[0].class_id))
    return ParsedRepo(classes, methods, edges, imports, [])


def _make_index(tmp_path: Path, repo_root: Path, method: MethodInfo) -> RepoIndex:
    old_class = _class(method.class_id, method.file_path, method.class_id.rsplit(".", 1)[-1])
    return RepoIndex(
        repo_id="sync-test",
        repo_root=repo_root,
        fingerprint="before",
        call_graph=CallGraph.build([method], []),
        dependency_graph=DependencyGraph.build([old_class], []),
        symbol_index=SymbolIndex.build([old_class], [method]),
        vector_store=MemoryVectorStore({method.id, "stale-record"}),
        num_files=1,
        num_classes=1,
        num_methods=1,
        num_call_edges=0,
        file_token_counts={method.file_path: 1},
    )


def _patch_parser(monkeypatch, parsed: ParsedRepo) -> None:
    monkeypatch.setattr(live_sync, "parse_repository", lambda *_: parsed)
    monkeypatch.setattr(
        live_sync,
        "get_embedding_model",
        lambda _: FakeEmbeddingModel(),
    )


def test_live_sync_rebuilds_graphs_symbols_vectors_and_token_counts(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source = repo_root / "src" / "main" / "java" / "com" / "example"
    source.mkdir(parents=True)
    (source / "A.java").write_text(
        "package com.example;\nclass A { void newMethod() {} }\n",
        encoding="utf-8",
    )
    (source / "B.java").write_text(
        "package com.example;\nclass B { void caller() { new A().newMethod(); } }\n",
        encoding="utf-8",
    )

    old_method = _method("com.example.A", "oldMethod", "src/main/java/com/example/A.java", 2)
    repo_index = _make_index(tmp_path, repo_root, old_method)
    new_method = _method("com.example.A", "newMethod", "src/main/java/com/example/A.java", 2)
    caller = _method("com.example.B", "caller", "src/main/java/com/example/B.java", 2)
    parsed = _parsed(
        [_class("com.example.A", new_method.file_path, "A"),
         _class("com.example.B", caller.file_path, "B")],
        [new_method, caller],
    )
    _patch_parser(monkeypatch, parsed)
    settings = Settings(index_storage_dir=tmp_path / "index")

    live_sync.synchronize_repo_index(repo_index, settings)

    assert set(repo_index.call_graph.graph) == {new_method.id, caller.id}
    assert repo_index.call_graph.graph.has_edge(caller.id, new_method.id)
    assert set(repo_index.dependency_graph.graph) == {"com.example.A", "com.example.B"}
    assert repo_index.dependency_graph.graph.has_edge("com.example.B", "com.example.A")
    assert repo_index.symbol_index.exact_lookup("oldMethod") == []
    assert repo_index.symbol_index.exact_lookup("newMethod")[0].kind == SymbolKind.METHOD
    assert repo_index.vector_store.ids() == {new_method.id, caller.id}
    assert repo_index.vector_store.points[new_method.id][1]["file_path"] == new_method.file_path
    assert repo_index.num_files == 2
    assert repo_index.num_methods == 2
    assert repo_index.file_token_counts[new_method.file_path] > 1
    assert (settings.index_storage_dir / "sync-test.pkl").is_file()
    assert repo_index.fingerprint != "before"


def test_live_sync_retries_transient_vector_cleanup_failure(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_file = repo_root / "A.java"
    source_file.write_text("class A { void current() {} }", encoding="utf-8")
    old_method = _method("A", "old", "A.java", 1)
    repo_index = _make_index(tmp_path, repo_root, old_method)
    current = _method("A", "current", "A.java", 1)
    _patch_parser(monkeypatch, _parsed([_class("A", "A.java", "A")], [current]))
    settings = Settings(index_storage_dir=tmp_path / "index")

    vector_store = repo_index.vector_store
    original_remove = vector_store.remove
    attempts = 0

    def fail_once(ids: list[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary Qdrant failure")
        original_remove(ids)

    monkeypatch.setattr(vector_store, "remove", fail_once)
    live_sync.synchronize_repo_index(repo_index, settings)

    assert attempts == 2
    assert vector_store.ids() == {current.id}


def test_live_sync_handles_create_modify_rename_and_delete(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    first_path = "A.java"
    (repo_root / first_path).write_text("class A { void oldMethod() {} }", encoding="utf-8")
    old = _method("A", "oldMethod", first_path, 1)
    repo_index = _make_index(tmp_path, repo_root, old)
    settings = Settings(index_storage_dir=tmp_path / "index")
    a_class = _class("A", first_path, "A")

    created_path = "B.java"
    (repo_root / created_path).write_text("class B { void added() {} }", encoding="utf-8")
    added = _method("B", "added", created_path, 1)
    _patch_parser(monkeypatch, _parsed([a_class, _class("B", created_path, "B")], [old, added]))
    live_sync.synchronize_repo_index(repo_index, settings)
    assert added.id in repo_index.call_graph.graph

    (repo_root / first_path).write_text("class A { void renamedMethod() {} }", encoding="utf-8")
    renamed = _method("A", "renamedMethod", first_path, 1)
    _patch_parser(monkeypatch, _parsed([a_class, _class("B", created_path, "B")], [renamed, added]))
    live_sync.synchronize_repo_index(repo_index, settings)
    assert old.id not in repo_index.vector_store.ids()
    assert renamed.id in repo_index.vector_store.ids()

    moved_path = "Moved.java"
    (repo_root / created_path).replace(repo_root / moved_path)
    moved = _method("B", "added", moved_path, 1)
    _patch_parser(monkeypatch, _parsed([a_class, _class("B", moved_path, "B")], [renamed, moved]))
    live_sync.synchronize_repo_index(repo_index, settings)
    assert repo_index.vector_store.points[moved.id][1]["file_path"] == moved_path

    (repo_root / first_path).unlink()
    _patch_parser(monkeypatch, _parsed([_class("B", moved_path, "B")], [moved]))
    live_sync.synchronize_repo_index(repo_index, settings)
    assert renamed.id not in repo_index.vector_store.ids()
    assert repo_index.symbol_index.exact_lookup("renamedMethod") == []

    (repo_root / moved_path).unlink()
    _patch_parser(monkeypatch, ParsedRepo([], [], [], [], []))
    live_sync.synchronize_repo_index(repo_index, settings)
    assert repo_index.num_methods == repo_index.num_classes == repo_index.num_files == 0
    assert repo_index.vector_store.ids() == set()


@pytest.mark.parametrize(
    "event_factory",
    [
        lambda path: FileCreatedEvent(path),
        lambda path: FileModifiedEvent(path),
        lambda path: FileDeletedEvent(path),
        lambda path: FileMovedEvent(path, path + ".java"),
    ],
)
def test_watchdog_events_schedule_reindex_for_java_changes(tmp_path, monkeypatch, event_factory):
    root = tmp_path / "repo"
    root.mkdir()
    method = _method("A", "method", "A.java", 1)
    index = _make_index(tmp_path, root, method)
    handler = live_sync.JavaFileChangeHandler(index, Settings(), debounce_seconds=0.02)
    synchronized = Event()
    monkeypatch.setattr(handler, "_run_synchronization", synchronized.set)

    path = str(root / "A.java")
    event = event_factory(path)
    if isinstance(event, FileMovedEvent):
        event.dest_path = path + ".java"
    handler.dispatch(event)

    assert synchronized.wait(timeout=2)
    handler.stop()


def test_live_sync_parse_failure_keeps_last_valid_index(tmp_path, monkeypatch, caplog):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "A.java"
    source.write_text("class A { void oldMethod() {} }", encoding="utf-8")
    method = _method("A", "oldMethod", "A.java", 1)
    index = _make_index(tmp_path, root, method)
    original_ids = index.vector_store.ids()
    handler = live_sync.JavaFileChangeHandler(index, Settings())
    monkeypatch.setattr(
        live_sync,
        "synchronize_repo_index",
        lambda *_: (_ for _ in ()).throw(RuntimeError("parser failed")),
    )

    handler._run_synchronization()

    assert index.vector_store.ids() == original_ids
    assert index.call_graph.contains(method.id)
    assert "Failed to synchronize repository" in caplog.text
    handler.stop()
