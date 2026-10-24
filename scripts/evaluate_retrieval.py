#!/usr/bin/env python3
"""Run a labeled, same-model comparison of whole-file, semantic-only and ACOR."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-id",
        default=None,
        help="Fallback indexed repo id for tasks without repo_id (single-revision runs)",
    )
    parser.add_argument("--tasks", required=True, type=Path, help="JSON file with manually labeled benchmark tasks")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path (default: eval_results/<repo>-<timestamp>.json)")
    parser.add_argument("--include-ablations", action="store_true", help="Run signal and ranking-strategy ablations")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Measure retrieval against gold labels without calling an LLM",
    )
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=None,
        help="Isolated index/Qdrant directory created by index_commons_lang_benchmark.py",
    )
    parser.add_argument(
        "--indexing-results",
        type=Path,
        default=None,
        help="Index timing report to join into benchmark rows",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=None,
        help="Flat per-task CSV output (default: JSON output path with .csv suffix)",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Total input-prompt token budget for every retrieval condition",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=0,
        help="Pause between Groq requests to stay within the configured TPM quota",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    backend_dir = project_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.config import Settings
    from app.core.repo_index import RepoIndex
    from app.evaluation.benchmark import (
        load_tasks,
        run_benchmark,
        run_retrieval_evaluation,
    )
    from app.retrieval.embeddings import get_embedding_model
    from qdrant_client import QdrantClient

    settings_values = {}
    if args.storage_dir is not None:
        storage_dir = args.storage_dir.resolve()
        settings_values = {
            "qdrant_path": storage_dir / "qdrant",
            "index_storage_dir": storage_dir / "index",
        }
    if args.token_budget is not None:
        if args.token_budget <= 0:
            raise ValueError("--token-budget must be positive")
        settings_values["default_token_budget"] = args.token_budget
    settings = Settings(
        _env_file=backend_dir / ".env",
        qdrant_url="",
        qdrant_api_key="",
        **settings_values,
    )
    tasks = load_tasks(args.tasks.resolve())
    repo_ids = {
        task.repo_id or args.repo_id
        for task in tasks
    }
    if None in repo_ids:
        raise ValueError(
            "Provide --repo-id or a repo_id for every task in the task file"
        )
    qdrant_client = QdrantClient(path=str(settings.qdrant_path))
    try:
        repo_indexes = {
            repo_id: RepoIndex.load(
                repo_id,
                settings.index_storage_dir,
                settings,
                vector_client=qdrant_client,
            )
            for repo_id in repo_ids
        }
        if args.retrieval_only:
            report = run_retrieval_evaluation(
                tasks=tasks,
                repo_index=repo_indexes,
                embedding_model=get_embedding_model(settings),
                settings=settings,
                include_ablations=args.include_ablations,
            )
        else:
            from app.llm.providers import get_llm_client
            from app.llm.providers import GroqClient, MockLLMClient

            if settings.llm_provider != "groq":
                raise RuntimeError(
                    "This evaluation phase is restricted to the configured Groq provider"
                )
            llm_client = get_llm_client(settings)
            if not isinstance(llm_client, GroqClient) or isinstance(llm_client, MockLLMClient):
                raise RuntimeError("A real configured Groq client is required for this phase")
            report = run_benchmark(
                tasks=tasks,
                repo_index=repo_indexes,
                embedding_model=get_embedding_model(settings),
                settings=settings,
                llm_client=llm_client,
                include_ablations=args.include_ablations,
                request_delay_seconds=args.request_delay_seconds,
            )
    finally:
        qdrant_client.close()

    output_path = args.output or (
        project_root
        / "eval_results"
        / f"commons-lang-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indexing_by_repo: dict[str, dict] = {}
    if args.indexing_results is not None:
        indexing_report = json.loads(
            args.indexing_results.resolve().read_text(encoding="utf-8")
        )
        indexing_by_repo = {
            entry["repo_id"]: entry
            for entry in indexing_report["repositories"]
            if entry.get("status") == "success"
        }
        report["indexing"] = indexing_report
    task_by_id = {task.task_id: task for task in tasks}
    for row in report["results"]:
        task = task_by_id[row["task_id"]]
        row["gold_method_ids"] = list(
            task.bug_gold_ids
            if task.task_type == "bug_localization"
            else task.impact_gold_ids
        )
        index_result = indexing_by_repo.get(row.get("repository_id"), {})
        row["indexing_time_seconds"] = index_result.get("index_time_seconds")
        row["java_source_files"] = index_result.get("java_source_files")
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csv_path = (args.csv_output or output_path.with_suffix(".csv")).resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    flat_rows = [
        {
            **{key: value for key, value in row.items() if key != "metrics"},
            **{f"metric_{key}": value for key, value in row["metrics"].items()},
            "retrieved_method_ids": json.dumps(row.get("retrieved_method_ids", [])),
            "gold_method_ids": json.dumps(row.get("gold_method_ids", [])),
            "predicted_bug_ids": json.dumps(row.get("predicted_bug_ids", [])),
            "predicted_impact_ids": json.dumps(row.get("predicted_impact_ids", [])),
        }
        for row in report["results"]
    ]
    if flat_rows:
        fields = list(dict.fromkeys(key for row in flat_rows for key in row))
        with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(flat_rows)

    print(f"Repositories: {', '.join(report['repository_ids'])}")
    print(f"Model: {report['provider']} / {report['model']}")
    print(f"Tasks: {report['task_count']}; conditions: {len(report['conditions'])}")
    if report.get("execution_mode") == "retrieval_only_no_model_requests":
        print("Mode: retrieval-only; no LLM calls were made")
    print(f"Results: {output_path}")
    print(f"CSV: {csv_path}")
    token_header = (
        "Estimated input tokens"
        if args.retrieval_only
        else "Actual input tokens"
    )
    print(f"{'Condition':<25} {token_header:>22}  Token reduction  Latency ms  Success")
    for condition, summary in report["summary"].items():
        actual = summary.get("mean_actual_input_tokens")
        if actual is None and args.retrieval_only:
            actual = summary.get("mean_estimated_input_tokens")
        reduction = summary.get("mean_actual_token_reduction_vs_whole_file")
        if reduction is None and args.retrieval_only:
            reduction = summary.get("mean_estimated_token_reduction_vs_whole_file")
        latency = summary["mean_total_latency_ms"]
        print(
            f"{condition:<25} "
            f"{actual if actual is not None else 'unreported':>19}  "
            f"{f'{100 * reduction:.1f}%' if reduction is not None else 'unavailable':>15}  "
            f"{latency:>10.1f}  "
            f"{summary['successful_runs']}/{summary['tasks']}"
        )

    failures = sum(result["error_type"] is not None for result in report["results"])
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
