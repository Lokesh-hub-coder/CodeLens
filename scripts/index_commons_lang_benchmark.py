#!/usr/bin/env python3
"""Index each pinned Commons Lang benchmark checkout into isolated storage."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _index_one(task: dict, checkout: Path, state_dir: Path) -> dict:
    project_root = Path(__file__).resolve().parents[1]
    backend_dir = project_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.api.routes import index_repository, _repo_registry
    from app.api.schemas import IndexRepositoryRequest
    from app.config import Settings
    from app.parsing.repo_walker import list_java_files
    from app.parsing.source_filter import is_test_path
    from app.watcher.live_sync import stop_repo_watcher

    repo_id = task["repo_id"]
    expected_revision = task["source"]["buggy_revision"]
    result: dict = {
        "repo_id": repo_id,
        "task_id": task["id"],
        "expected_revision": expected_revision,
        "path": str(checkout),
    }
    try:
        actual_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        if actual_revision != expected_revision:
            raise RuntimeError(
                f"Checkout revision mismatch: expected {expected_revision}, "
                f"got {actual_revision}"
            )
            result["parser_max_heap_mb"] = settings.parser_max_heap_mb
            result["parser_timeout_seconds"] = settings.parser_timeout_seconds
        settings = Settings(
            _env_file=backend_dir / ".env",
            qdrant_path=state_dir / "qdrant",
            qdrant_url="",
            qdrant_api_key="",
            index_storage_dir=state_dir / "index",
        )
        result["qdrant_mode"] = "isolated_local"
        java_source_files = sum(
            not (
                settings.exclude_test_sources
                and is_test_path(repo_file.relative_path)
            )
            for repo_file in list_java_files(checkout)
        )
        started = time.perf_counter()
        response = asyncio.run(
            index_repository(
                IndexRepositoryRequest(
                    repo_path=str(checkout),
                    repo_id=repo_id,
                    force_reindex=True,
                ),
                settings,
            )
        )
        result.update({
            "status": "success",
            "revision": actual_revision,
            "java_source_files": java_source_files,
            "index_time_seconds": response.index_time_seconds,
            "wall_time_seconds": time.perf_counter() - started,
            "num_files": response.num_files,
            "num_classes": response.num_classes,
            "num_methods": response.num_methods,
            "num_call_edges": response.num_call_edges,
            "num_import_edges": response.num_import_edges,
            "unparsed_files": response.unparsed_files,
        })
        if response.unparsed_files:
            result["status"] = "failed"
            result["error"] = (
                f"Index returned {len(response.unparsed_files)} unparsed files"
            )
    except Exception as exc:
        result.update({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
    finally:
        stop_repo_watcher(repo_id)
        _repo_registry.pop(repo_id, None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument(
        "--tasks",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "commons-lang-defects4j.json",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Isolated index and Qdrant storage (default: <workdir>/state)",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--worker-repo-id",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    checkouts = workdir / "checkouts"
    state_dir = (args.state_dir or workdir / "state").resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else state_dir / "indexing-results.json"
    )
    tasks = json.loads(args.tasks.resolve().read_text(encoding="utf-8"))

    if args.worker_repo_id is not None:
        task = next(
            (item for item in tasks if item["repo_id"] == args.worker_repo_id),
            None,
        )
        if task is None:
            raise ValueError(f"Unknown worker repo_id: {args.worker_repo_id}")
        result = _index_one(task, checkouts / args.worker_repo_id, state_dir)
        report = {"repositories": [result]}
        _write_report(report_path, report)
        print(
            f"{result['repo_id']}: {result['status']}"
            + (
                f" ({result['index_time_seconds']:.1f}s, "
                f"{result['num_methods']} methods, "
                f"{len(result['unparsed_files'])} unparsed)"
                if result["status"] == "success"
                else f" - {result.get('error', 'index incomplete')}"
            )
        )
        return 0 if result["status"] == "success" else 1

    repositories: list[dict] = []
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "qdrant_mode": "isolated_local",
        "repositories": repositories,
    }

    for task in tasks:
        repo_id = task["repo_id"]
        worker_report = state_dir / "worker-reports" / f"{repo_id}.json"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--workdir",
                    str(workdir),
                    "--state-dir",
                    str(state_dir),
                    "--tasks",
                    str(args.tasks.resolve()),
                    "--report",
                    str(worker_report),
                    "--worker-repo-id",
                    repo_id,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            worker_rows = (
                json.loads(worker_report.read_text(encoding="utf-8"))["repositories"]
                if worker_report.exists()
                else []
            )
            result = worker_rows[0] if worker_rows else {
                "repo_id": repo_id,
                "task_id": task["id"],
                "status": "failed",
                "error": (
                    f"Index worker exited with code {completed.returncode}; "
                    f"stdout={completed.stdout[-2000:]}; "
                    f"stderr={completed.stderr[-2000:]}"
                ),
            }
            if completed.returncode != 0:
                result["status"] = "failed"
                result["worker_exit_code"] = completed.returncode
        except Exception as exc:
            result = {
                "repo_id": repo_id,
                "task_id": task["id"],
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        repositories.append(result)
        _write_report(report_path, report)
        print(
            f"{repo_id}: {result['status']}"
            + (
                f" ({result['index_time_seconds']:.1f}s, "
                f"{result['num_methods']} methods, "
                f"{len(result['unparsed_files'])} unparsed)"
                if result["status"] == "success"
                else f" - {result.get('error', 'index incomplete')}"
            )
        )

    print(f"Index report: {report_path}")
    return 0 if all(row["status"] == "success" for row in repositories) else 1


if __name__ == "__main__":
    raise SystemExit(main())
