#!/usr/bin/env python3
"""Create isolated Apache Commons Lang buggy-revision worktrees for evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        required=True,
        type=Path,
        help="Dedicated directory for the Git clone and checkouts; will not be deleted",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "commons-lang-defects4j.json",
    )
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    repository = workdir / "source"
    checkouts = workdir / "checkouts"
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Task file must contain a non-empty JSON array")
    workdir.mkdir(parents=True, exist_ok=True)
    checkouts.mkdir(parents=True, exist_ok=True)

    if not (repository / ".git").exists():
        if repository.exists() and any(repository.iterdir()):
            raise FileExistsError(
                f"Refusing to clone into non-empty path: {repository}"
            )
        _git(
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "https://github.com/apache/commons-lang.git",
            str(repository),
        )

    for task in tasks:
        repo_id = task.get("repo_id")
        revision = task.get("source", {}).get("buggy_revision")
        if not isinstance(repo_id, str) or not repo_id:
            raise ValueError("Each task needs repo_id")
        if not isinstance(revision, str) or len(revision) != 40:
            raise ValueError(f"Task {repo_id!r} needs a full buggy_revision SHA")

        checkout = checkouts / repo_id
        _git("fetch", "--no-tags", "origin", revision, cwd=repository)
        if checkout.exists():
            current = _git("rev-parse", "HEAD", cwd=checkout)
            if current != revision:
                raise FileExistsError(
                    f"Existing checkout {checkout} is at {current}, expected {revision}; "
                    "move it aside manually before retrying"
                )
        else:
            _git(
                "worktree",
                "add",
                "--detach",
                str(checkout),
                revision,
                cwd=repository,
            )
        print(f"{repo_id}={checkout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
