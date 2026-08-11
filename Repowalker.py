"""
Filesystem-level helpers that stay on the Python side of the fence.

The heavy lifting of understanding Java *syntax* (ASTs, call resolution) is
delegated to the parser-service JAR (see java_bridge.py) because JavaParser
is a JVM library with no reliable Python equivalent for full symbol
resolution. Everything here is language-agnostic file plumbing:

- finding candidate .java files while skipping build output / VCS dirs
- reading an exact source snippet given a 1-indexed inclusive line range
  (this is what acor/context_builder.py calls once ACOR has decided *which*
  methods to include -- we deliberately do not carry full file text through
  the whole pipeline, only file_path + line ranges, to keep memory bounded
  on large repos)
- a cheap fingerprint of the repo so IndexRepositoryRequest.force_reindex=False
  can skip re-parsing unchanged repositories
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# Directories we never want to walk into: build output, VCS metadata,
# dependency caches. Extend as the team hits real-world repos with other
# build tools (Gradle's .gradle, Bazel's bazel-*, etc).
IGNORED_DIR_NAMES = {
    ".git",
    "target",       # Maven build output
    "build",        # Gradle build output
    "out",          # IntelliJ build output
    "node_modules",
    ".idea",
    ".vscode",
    "__pycache__",
}


@dataclass(frozen=True)
class RepoFile:
    absolute_path: Path
    relative_path: str  # POSIX-style, relative to repo root -- stable across OSes


def list_java_files(repo_root: Path) -> list[RepoFile]:
    """Recursively find .java source files, skipping build/VCS directories.

    Pruning ignored directories during the walk (rather than filtering
    afterwards) matters on real repositories: a Maven repo's `target/`
    can contain more generated .java (annotation processors, ANTLR output)
    than the actual source tree, which would silently bloat every
    downstream graph and index.
    """
    repo_root = repo_root.resolve()
    results: list[RepoFile] = []

    def _walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if entry.name in IGNORED_DIR_NAMES:
                    continue
                _walk(entry)
            elif entry.suffix == ".java":
                results.append(
                    RepoFile(
                        absolute_path=entry,
                        relative_path=entry.relative_to(repo_root).as_posix(),
                    )
                )

    _walk(repo_root)
    return results


def read_snippet(file_path: Path, start_line: int, end_line: int) -> str:
    """Read a 1-indexed, inclusive line range from a source file.

    Used exclusively at context-assembly time (after ACOR has already
    decided which symbols make the cut) so we never hold whole-repo source
    text in memory at once -- only the (usually small) set of line ranges
    that survived ranking under the token budget.
    """
    if start_line < 1 or end_line < start_line:
        raise ValueError(f"Invalid line range [{start_line}, {end_line}]")

    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    # Clamp defensively: the AST snapshot and the file on disk can drift if
    # the repo was edited between indexing and query time.
    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)
    return "\n".join(lines[start_idx:end_idx])


def compute_repo_fingerprint(repo_root: Path) -> str:
    """Cheap change-detection hash over (relative path, size, mtime_ns).

    This is intentionally *not* a hash of file contents -- hashing every
    byte of a large repository on every query-time status check would
    itself become a latency problem the project is trying to avoid
    elsewhere. Path+size+mtime catches the common cases (edits, adds,
    deletes) at near-zero cost; a content hash is the correct upgrade if
    the team later supports read-only mounted repos where mtimes aren't
    reliable (e.g. some CI checkouts).
    """
    hasher = hashlib.sha256()
    for repo_file in list_java_files(repo_root):
        stat = repo_file.absolute_path.stat()
        hasher.update(repo_file.relative_path.encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
    return hasher.hexdigest()