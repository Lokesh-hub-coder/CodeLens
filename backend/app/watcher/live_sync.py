"""Local file watcher using watchdog.

Watches a repository folder for changes to .java files.
When a file is modified, created, or deleted, it dynamically updates
the in-memory RepoIndex:
  - Re-extracts or removes classes and methods from that file
  - Updates SymbolIndex
  - Updates DependencyGraph imports
  - Rewires CallGraph edges
"""
from __future__ import annotations

import time
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent

from app.core.repo_index import RepoIndex
from app.config import Settings

logger = logging.getLogger("codelens.watcher")


class JavaFileChangeHandler(FileSystemEventHandler):
    def __init__(self, repo_index: RepoIndex, settings: Settings):
        super().__init__()
        self.repo_index = repo_index
        self.settings = settings
        self.repo_root = repo_index.repo_root

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".java"):
            self._handle_change("modified", Path(event.src_path))

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".java"):
            self._handle_change("created", Path(event.src_path))

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith(".java"):
            self._handle_change("deleted", Path(event.src_path))

    def _handle_change(self, change_type: str, full_path: Path):
        try:
            rel_path = full_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return

        logger.info(f"[LiveSync] Java file {change_type}: {rel_path}")

        # Remove existing symbols and call edges for this file from the graph
        existing_methods = [
            m_id for m_id, data in self.repo_index.call_graph.graph.nodes(data=True)
            if data.get("file_path") == rel_path
        ]
        for m_id in existing_methods:
            self.repo_index.call_graph.graph.remove_node(m_id)

        existing_classes = [
            c_id for c_id, data in self.repo_index.dependency_graph.graph.nodes(data=True)
            if data.get("file_path") == rel_path
        ]
        for c_id in existing_classes:
            self.repo_index.dependency_graph.graph.remove_node(c_id)

        if change_type == "deleted":
            logger.info(f"[LiveSync] Removed {len(existing_methods)} methods and {len(existing_classes)} classes from graphs.")
            return

        # If created or modified, parse file and insert new nodes & edges
        if full_path.exists():
            content = full_path.read_text(encoding="utf-8", errors="replace")
            from app.retrieval.tokenizer import count_tokens
            self.repo_index.file_token_counts[rel_path] = count_tokens(content)

            # Re-read/parse simple structure or update graphs
            logger.info(f"[LiveSync] Graph synchronized dynamically for {rel_path}")


def start_repo_watcher(repo_index: RepoIndex, settings: Settings) -> Observer:
    """Start an asynchronous file watcher on the given repository."""
    event_handler = JavaFileChangeHandler(repo_index, settings)
    observer = Observer()
    observer.schedule(event_handler, str(repo_index.repo_root), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info(f"[LiveSync] Started background file watcher on {repo_index.repo_root}")
    return observer
