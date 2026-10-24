#!/usr/bin/env python3
"""Create a committed-source ZIP with secrets and generated content excluded."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

_SECRET_PATTERNS = (
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"://[^/\s:@]+:[^@\s]{8,}@"),
    re.compile(
        rb"""(?i)\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*"""
        rb"""["'](?!your_|change_me|changeme|placeholder|not-required|example)[^"'\r\n]{20,}["']"""
    ),
    re.compile(
        rb"""(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|PASSWORD|PASSWD|SECRET|TOKEN)"""
        rb"""\s*=\s*(?!["']?(?:your_|change_me|changeme|placeholder|not-required|example)\b)"""
        rb"""["']?([A-Za-z0-9_/-]{24,})["']?\s*(?:#.*)?$"""
    ),
)

_GENERATED_PARTS = {
    ".cache",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vite",
    "build",
    "chroma_db",
    "credentials",
    "data",
    "dist",
    "eval_results",
    "logs",
    "node_modules",
    "repos",
    "secrets",
    "storage",
    "target",
    "venv",
}
_GENERATED_SUFFIXES = {
    ".class",
    ".db",
    ".faiss",
    ".jar",
    ".pkl",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".zip",
}


def is_excluded(path: str) -> bool:
    """Fail closed for secret files and generated/runtime directories."""
    item = PurePosixPath(path)
    name = item.name.casefold()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name.endswith(".local.env") or item.suffix.lower() in (
        {".pem", ".key", ".p12", ".pfx"} | _GENERATED_SUFFIXES
    ):
        return True
    if item.suffix.lower() == ".log" or any(
        part.casefold() in _GENERATED_PARTS for part in item.parts
    ):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output ZIP path (default: <repo>/dist/CodeLens-<commit>.zip)",
    )
    args = parser.parse_args()

    repo_root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_root,
        text=True,
    ).strip()
    output = (args.output or repo_root / "dist" / f"CodeLens-{commit}.zip").resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing release archive: {output}")

    with tempfile.TemporaryDirectory(prefix="codelens-release-") as temp_dir:
        archive = Path(temp_dir) / "release.zip"
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                "--worktree-attributes",
                f"--output={archive}",
                "HEAD",
            ],
            cwd=repo_root,
            check=True,
        )
        with zipfile.ZipFile(archive) as release:
            offending_paths = [
                name
                for name in release.namelist()
                if not name.endswith("/") and is_excluded(name)
            ]
            if offending_paths:
                raise RuntimeError(
                    "Release archive contains excluded paths: "
                    + ", ".join(offending_paths)
                )
            for name in release.namelist():
                if name.endswith("/") or is_excluded(name):
                    continue
                content = release.read(name)
                if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
                    raise RuntimeError(
                        f"Probable secret detected in release file: {name}"
                    )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as release_file:
            release_file.write(archive.read_bytes())

    print(f"Created committed-source ZIP: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
