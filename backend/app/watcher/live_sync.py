"""Watch Java repositories and keep their persisted CodeLens index current."""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock, Timer

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import Settings
from app.core.repo_index import RepoIndex
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.java_bridge import parse_repository
from app.parsing.repo_walker import compute_repo_fingerprint, list_java_files, read_snippet
from app.parsing.source_filter import filter_test_sources
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.tokenizer import count_tokens

logger = logging.getLogger("codelens.watcher")
_watchers: dict[str, tuple[Observer, "JavaFileChangeHandler"]] = {}
_watchers_lock = Lock()


def synchronize_repo_index(repo_index: RepoIndex, settings: Settings) -> None:
    """Reparse the repository and replace every derived index structure.

    Parsing the whole repository is intentional: adding or removing a Java
    declaration can change cross-file call and import resolution.
    """
    parsed = parse_repository(repo_index.repo_root, settings)
    if settings.exclude_test_sources:
        parsed = filter_test_sources(parsed)

    methods = parsed.methods
    classes = parsed.classes
    new_method_ids = {method.id for method in methods}

    snippets: list[str] = []
    ids: list[str] = []
    metadatas: list[dict] = []
    for method in methods:
        try:
            snippet = read_snippet(
                repo_index.repo_root / method.file_path,
                method.start_line,
                method.end_line,
            )
        except OSError:
            snippet = method.signature
        snippets.append(snippet)
        ids.append(method.id)
        metadatas.append({
            "method_id": method.id,
            "class_id": method.class_id,
            "name": method.name,
            "file_path": method.file_path,
            "start_line": method.start_line,
            "end_line": method.end_line,
        })

    file_token_counts: dict[str, int] = {}
    java_files = list_java_files(repo_index.repo_root)
    if settings.exclude_test_sources:
        from app.parsing.source_filter import is_test_path
        java_files = [
            repo_file
            for repo_file in java_files
            if not is_test_path(repo_file.relative_path)
        ]
    for repo_file in java_files:
        content = repo_file.absolute_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        file_token_counts[repo_file.relative_path] = count_tokens(content)

    vectors = get_embedding_model(settings).encode(snippets)
    for attempt in range(2):
        try:
            if ids:
                repo_index.vector_store.add(ids, vectors, metadatas)
            stale_ids = sorted(repo_index.vector_store.ids() - new_method_ids)
            repo_index.vector_store.remove(stale_ids)
            break
        except Exception:
            if attempt == 1:
                raise
            logger.warning(
                "Vector refresh failed for repository %s; retrying once",
                repo_index.repo_id,
                exc_info=True,
            )

    repo_index.call_graph = CallGraph.build(methods, parsed.call_edges)
    repo_index.dependency_graph = DependencyGraph.build(classes, parsed.import_edges)
    repo_index.symbol_index = SymbolIndex.build(classes, methods)
    repo_index.num_files = len(java_files)
    repo_index.num_classes = len(classes)
    repo_index.num_methods = len(methods)
    repo_index.num_call_edges = len(parsed.call_edges)
    repo_index.num_import_edges = len(parsed.import_edges)
    repo_index.unparsed_files = parsed.unparsed_files
    repo_index.file_token_counts = file_token_counts
    repo_index.fingerprint = compute_repo_fingerprint(repo_index.repo_root)
    repo_index.save(settings.index_storage_dir)


class JavaFileChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        repo_index: RepoIndex,
        settings: Settings,
        debounce_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.repo_index = repo_index
        self.settings = settings
        self.repo_root = repo_index.repo_root.resolve()
        self.debounce_seconds = (
            settings.live_sync_debounce_seconds
            if debounce_seconds is None
            else debounce_seconds
        )
        self._lock = Lock()
        self._timer: Timer | None = None
        self._stopped = False

    def on_created(self, event: FileSystemEvent) -> None:
        self._schedule_if_java(event.src_path, event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._schedule_if_java(event.src_path, event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._schedule_if_java(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        paths = [event.src_path, getattr(event, "dest_path", "")]
        if any(Path(path).suffix.lower() == ".java" for path in paths if path):
            self._schedule_reindex()

    def _schedule_if_java(self, raw_path: str, is_directory: bool) -> None:
        if not is_directory and Path(raw_path).suffix.lower() == ".java":
            self._schedule_reindex()

    def _schedule_reindex(self) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = Timer(self.debounce_seconds, self._run_synchronization)
            self._timer.daemon = True
            self._timer.start()

    def _run_synchronization(self) -> None:
        try:
            synchronize_repo_index(self.repo_index, self.settings)
            logger.info(
                "Synchronized repository %s: %d methods, %d classes",
                self.repo_index.repo_id,
                self.repo_index.num_methods,
                self.repo_index.num_classes,
            )
        except Exception:
            logger.exception(
                "Failed to synchronize repository %s; retaining the last valid in-memory index",
                self.repo_index.repo_id,
            )

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def start_repo_watcher(repo_index: RepoIndex, settings: Settings) -> Observer:
    """Start one daemon watcher per indexed repository."""
    with _watchers_lock:
        current = _watchers.get(repo_index.repo_id)
        if current and current[0].is_alive():
            return current[0]

        handler = JavaFileChangeHandler(repo_index, settings)
        observer = Observer()
        observer.daemon = True
        observer.schedule(handler, str(repo_index.repo_root), recursive=True)
        observer.start()
        _watchers[repo_index.repo_id] = (observer, handler)
    logger.info("Started live sync for repository %s", repo_index.repo_id)
    return observer


def stop_repo_watcher(repo_id: str) -> None:
    with _watchers_lock:
        watcher = _watchers.pop(repo_id, None)
    if watcher is None:
        return
    observer, handler = watcher
    handler.stop()
    observer.stop()
    observer.join(timeout=5)
    if observer.is_alive():
        logger.warning("Watcher for repository %s did not stop within five seconds", repo_id)


def stop_all_repo_watchers() -> None:
    with _watchers_lock:
        repo_ids = list(_watchers)
    for repo_id in repo_ids:
        stop_repo_watcher(repo_id)
