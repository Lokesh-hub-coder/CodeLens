#!/usr/bin/env python3
"""Resume only failed/missing Groq benchmark rows and save after every call."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


def _write_results(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(output)

    csv_path = output.with_suffix(".csv")
    rows = []
    for result in report["results"]:
        rows.append({
            **{key: value for key, value in result.items() if key != "metrics"},
            **{f"metric_{key}": value for key, value in result.get("metrics", {}).items()},
            "gold_method_ids": json.dumps(result.get("gold_method_ids", [])),
            "retrieved_method_ids": json.dumps(result.get("retrieved_method_ids", [])),
            "predicted_bug_ids": json.dumps(result.get("predicted_bug_ids", [])),
            "predicted_impact_ids": json.dumps(result.get("predicted_impact_ids", [])),
        })
    if rows:
        fields = list(dict.fromkeys(key for row in rows for key in row))
        csv_temp = csv_path.with_suffix(csv_path.suffix + ".tmp")
        with csv_temp.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        csv_temp.replace(csv_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--storage-dir", required=True, type=Path)
    parser.add_argument("--indexing-results", required=True, type=Path)
    parser.add_argument("--token-budget", type=int, required=True)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=60,
        help="Wait before the single next provider call to respect TPM quotas",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Groq model to use for this pending row; must be available on this account",
    )
    args = parser.parse_args()
    if args.token_budget <= 0 or args.request_delay_seconds < 0:
        raise ValueError("Token budget must be positive and delay non-negative")

    project_root = Path(__file__).resolve().parents[1]
    backend_dir = project_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from qdrant_client import QdrantClient

    from app.config import Settings
    from app.core.repo_index import RepoIndex
    from app.evaluation.benchmark import (
        _run_condition,
        build_conditions,
        load_tasks,
        summarize_results,
    )
    from app.llm.providers import GroqClient, MockLLMClient, get_llm_client
    from app.retrieval.embeddings import get_embedding_model

    output = args.results.resolve()
    report = json.loads(output.read_text(encoding="utf-8"))
    if report.get("provider") != "groq" or report.get("model") is None:
        raise ValueError("Existing results are not a Groq benchmark report")
    settings = Settings(
        _env_file=backend_dir / ".env",
        default_token_budget=args.token_budget,
        qdrant_url="",
        qdrant_api_key="",
        qdrant_path=args.storage_dir.resolve() / "qdrant",
        index_storage_dir=args.storage_dir.resolve() / "index",
    )
    if settings.llm_provider != "groq":
        raise RuntimeError("Configured provider is not Groq; refusing provider substitution")
    model_name = args.model or settings.groq_model_name
    generation_settings = settings.model_copy(
        update={"groq_model_name": model_name}
    )
    llm_client = get_llm_client(generation_settings)
    if not isinstance(llm_client, GroqClient) or isinstance(llm_client, MockLLMClient):
        raise RuntimeError("A real configured Groq client is required")
    if report.get("model") != settings.groq_model_name:
        raise ValueError(
            f"Saved report model {report.get('model')!r} differs from configured "
            f"Groq model {settings.groq_model_name!r}"
        )

    tasks = load_tasks(args.tasks.resolve())
    indexing_report = json.loads(
        args.indexing_results.resolve().read_text(encoding="utf-8")
    )
    indexing_by_repo = {
        item["repo_id"]: item
        for item in indexing_report["repositories"]
        if item.get("status") == "success"
    }
    conditions = build_conditions(settings, include_ablations=True)

    qdrant = QdrantClient(path=str(settings.qdrant_path))
    try:
        embedding_model = get_embedding_model(settings)
        results = report.setdefault("results", [])
        result_lookup = {
            (row["task_id"], row["condition"]): row
            for row in results
        }
        pending = [
            (task, condition)
            for task in tasks
            for condition in conditions
            if (
                (row := result_lookup.get((task.task_id, condition.name))) is None
                or row.get("error_type") is not None
            )
        ]
        completed = [
            task_id
            for task_id, condition_name in result_lookup
            if result_lookup[(task_id, condition_name)].get("error_type") is None
        ]
        print(f"Completed saved condition rows: {len(completed)}")
        print(f"Pending condition rows: {len(pending)}")
        if not pending:
            return 0

        task, condition = pending[0]
        old = result_lookup.get((task.task_id, condition.name))
        if old is not None:
            print(f"Retrying failed row: {task.task_id} / {condition.name}")
            report["results"].remove(old)
        else:
            print(f"Running missing row: {task.task_id} / {condition.name}")

        repo = RepoIndex.load(
            task.repo_id,
            settings.index_storage_dir,
            settings,
            vector_client=qdrant,
        )
        if args.request_delay_seconds:
            time.sleep(args.request_delay_seconds)
        attempt_started = datetime.now(timezone.utc).isoformat()
        row = _run_condition(
            task,
            condition,
            repo,
            embedding_model,
            settings,
            llm_client,
        )
        row["repository_id"] = repo.repo_id
        row["indexing_time_seconds"] = indexing_by_repo.get(
            repo.repo_id, {}
        ).get("index_time_seconds")
        row["java_source_files"] = indexing_by_repo.get(
            repo.repo_id, {}
        ).get("java_source_files")
        row["attempted_at"] = attempt_started
        row["provider"] = "groq"
        row["model"] = model_name
        if old is not None:
            row["previous_error"] = {
                "type": old.get("error_type"),
                "message": old.get("error_message"),
            }
        report["results"].append(row)
        report["context_token_budget"] = settings.default_token_budget
        report["summary"] = summarize_results(report["results"])
        report["indexing"] = indexing_report
        report["last_resume_attempt_at"] = attempt_started
        report["models_used"] = sorted({
            existing.get("model", report.get("model"))
            for existing in report["results"]
            if existing.get("error_type") is None
        })
        _write_results(report, output)
        status = "completed" if row.get("error_type") is None else "failed"
        print(
            f"{status}: {task.task_id} / {condition.name}; "
            f"model={model_name}; "
            f"actual_input_tokens={row.get('actual_input_tokens')}; "
            f"llm_latency_ms={row.get('llm_latency_ms')}; "
            f"error_type={row.get('error_type')}"
        )
        if row.get("error_type") and (
            "RateLimit" in row["error_type"]
            or "rate limit" in row.get("error_message", "").casefold()
        ):
            print("Stopping after provider rate-limit response; no further calls made.")
            return 2
        return 0 if row.get("error_type") is None else 1
    finally:
        qdrant.close()


if __name__ == "__main__":
    raise SystemExit(main())
