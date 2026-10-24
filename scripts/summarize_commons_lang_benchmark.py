#!/usr/bin/env python3
"""Create final, reproducible analysis from saved Commons Lang results."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _csv_write(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _condition_summary(
    rows: list[dict[str, Any]],
    retrieval_rows: list[dict[str, Any]],
    whole_by_task: dict[str, int],
    budget: int,
) -> dict[str, Any]:
    successful = [row for row in rows if row.get("error_type") is None]
    metrics = [
        row.get("metrics", {})
        for row in retrieval_rows
        if row.get("error_type") is None
    ]
    names = (
        "bug_localization_retrieval_recall",
        "bug_localization_retrieval_recall_at_1",
        "bug_localization_retrieval_recall_at_5",
        "bug_localization_retrieval_recall_at_10",
        "bug_localization_retrieval_mrr",
    )
    paired_reductions = [
        1 - row["actual_input_tokens"] / whole_by_task[row["task_id"]]
        for row in successful
        if row.get("actual_input_tokens")
        and whole_by_task.get(row["task_id"])
    ]
    prediction_hits = [
        bool(set(row.get("canonical_predicted_bug_ids", [])) & set(row["gold_method_ids"]))
        for row in successful
    ]
    prediction_exact = [
        set(row.get("canonical_predicted_bug_ids", []))
        == set(row["gold_method_ids"])
        for row in successful
    ]
    actual_tokens = [
        row["actual_input_tokens"]
        for row in successful
        if row.get("actual_input_tokens") is not None
    ]
    return {
        "tasks": len(rows),
        "successful_runs": len(successful),
        "failed_runs": len(rows) - len(successful),
        "models": dict(sorted({
            row.get("model", "openai/gpt-oss-120b"): sum(
                item.get("error_type") is None
                and item.get("model", "openai/gpt-oss-120b")
                == row.get("model", "openai/gpt-oss-120b")
                for item in successful
            )
            for row in successful
        }.items())),
        "retrieval_metrics": {
            name: _mean([metric[name] for metric in metrics if name in metric])
            for name in names
        },
        "model_bug_localization_hit_rate": _mean(
            [float(value) for value in prediction_hits]
        ),
        "model_bug_localization_exact_set_accuracy": _mean(
            [float(value) for value in prediction_exact]
        ),
        "mean_actual_provider_input_tokens": _mean(actual_tokens),
        "mean_estimated_input_tokens": _mean([
            row["estimated_input_tokens"]
            for row in successful
            if row.get("estimated_input_tokens") is not None
        ]),
        "actual_token_reduction_vs_whole_file": _mean(paired_reductions),
        "mean_provider_latency_ms": _mean([
            row["llm_latency_ms"]
            for row in successful
            if row.get("llm_latency_ms") is not None
        ]),
        "mean_retrieval_latency_ms": _mean([
            row["retrieval_latency_ms"]
            for row in successful
            if row.get("retrieval_latency_ms") is not None
        ]),
        "actual_input_budget_overruns": sum(
            value > budget for value in actual_tokens
        ),
        "actual_input_tokens_max": max(actual_tokens, default=None),
        "llm_latency_samples": sum(
            row.get("llm_latency_ms") is not None for row in successful
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retrieval-results",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "results"
        / "commons-lang-retrieval-only-5000.json",
    )
    parser.add_argument(
        "--groq-results",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "results"
        / "commons-lang-groq-5000.json",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "results"
        / "commons-lang-final",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    backend_dir = project_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.evaluation.benchmark import (
        _canonicalize_predictions,
        _classification_metrics,
    )

    retrieval = json.loads(args.retrieval_results.resolve().read_text(encoding="utf-8"))
    generation = json.loads(args.groq_results.resolve().read_text(encoding="utf-8"))
    tasks = {
        row["task_id"]: row
        for row in retrieval["results"]
    }
    budget = retrieval["context_token_budget"]
    expected = {
        (task_id, condition["name"])
        for task_id in tasks
        for condition in generation["conditions"]
    }
    observed = [(row["task_id"], row["condition"]) for row in generation["results"]]
    if len(observed) != len(set(observed)):
        raise ValueError("Groq result file contains duplicate task-condition rows")
    if set(observed) != expected:
        missing = sorted(expected - set(observed))
        extra = sorted(set(observed) - expected)
        raise ValueError(f"Incomplete result matrix; missing={missing}; extra={extra}")
    if budget != 5000:
        raise ValueError(f"Expected the saved retrieval budget to be 5000, got {budget}")

    retrieval_by_key = {
        (row["task_id"], row["condition"]): row
        for row in retrieval["results"]
    }
    detailed_rows: list[dict[str, Any]] = []
    for row in generation["results"]:
        task_row = tasks[row["task_id"]]
        row["gold_method_ids"] = task_row["gold_method_ids"]
        model_name = row.get("model", generation.get("model"))
        row["model"] = model_name
        row["provider"] = "groq"
        row["canonical_predicted_bug_ids"] = _canonicalize_predictions(
            row.get("predicted_bug_ids", []),
            row.get("retrieved_method_ids", []),
        )
        row["metrics"].update({
            "bug_localization_hit": bool(
                set(row["canonical_predicted_bug_ids"])
                & set(row["gold_method_ids"])
            ),
            "bug_localization_exact": (
                set(row["canonical_predicted_bug_ids"])
                == set(row["gold_method_ids"])
            ),
            "bug_localization_accuracy": float(
                set(row["canonical_predicted_bug_ids"])
                == set(row["gold_method_ids"])
            ),
            **{
                f"model_{key}": value
                for key, value in _classification_metrics(
                    row["canonical_predicted_bug_ids"],
                    tuple(row["gold_method_ids"]),
                ).items()
            },
        })
        retrieval_row = retrieval_by_key[(row["task_id"], row["condition"])]
        detailed_rows.append({
            "task_id": row["task_id"],
            "condition": row["condition"],
            "provider": "groq",
            "model": model_name,
            "status": "success" if row.get("error_type") is None else "failed",
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "gold_method_ids": json.dumps(row["gold_method_ids"]),
            "retrieved_method_ids": json.dumps(row.get("retrieved_method_ids", [])),
            "predicted_bug_ids_raw": json.dumps(row.get("predicted_bug_ids", [])),
            "predicted_bug_ids_canonical": json.dumps(row["canonical_predicted_bug_ids"]),
            "recall": retrieval_row.get("metrics", {}).get(
                "bug_localization_retrieval_recall"
            ),
            "recall_at_1": retrieval_row.get("metrics", {}).get(
                "bug_localization_retrieval_recall_at_1"
            ),
            "recall_at_5": retrieval_row.get("metrics", {}).get(
                "bug_localization_retrieval_recall_at_5"
            ),
            "recall_at_10": retrieval_row.get("metrics", {}).get(
                "bug_localization_retrieval_recall_at_10"
            ),
            "mrr": retrieval_row.get("metrics", {}).get(
                "bug_localization_retrieval_mrr"
            ),
            "model_localization_hit": row["metrics"]["bug_localization_hit"],
            "model_localization_exact": row["metrics"]["bug_localization_exact"],
            "actual_provider_input_tokens": row.get("actual_input_tokens"),
            "estimated_input_tokens": row.get("estimated_input_tokens"),
            "provider_latency_ms": row.get("llm_latency_ms"),
            "retrieval_latency_ms": row.get("retrieval_latency_ms"),
            "total_latency_ms": row.get("total_latency_ms"),
            "indexing_time_seconds": row.get("indexing_time_seconds"),
        })

    condition_summaries: dict[str, Any] = {}
    whole_by_task = {
        row["task_id"]: row["actual_input_tokens"]
        for row in generation["results"]
        if row["condition"] == "whole_file"
        and row.get("error_type") is None
        and row.get("actual_input_tokens")
    }
    for condition in generation["conditions"]:
        name = condition["name"]
        model_rows = [row for row in generation["results"] if row["condition"] == name]
        retrieval_rows = [
            retrieval_by_key[(row["task_id"], name)]
            for row in model_rows
        ]
        condition_summaries[name] = _condition_summary(
            model_rows,
            retrieval_rows,
            whole_by_task,
            budget,
        )

    index_report = generation.get("indexing", {})
    index_rows = index_report.get("repositories", [])
    analysis = {
        "report_version": 1,
        "provider": "groq",
        "primary_model": generation["model"],
        "models_used": sorted({row["model"] for row in generation["results"]}),
        "context_token_budget_estimate": budget,
        "tasks": sorted(tasks),
        "expected_condition_rows": len(expected),
        "completed_condition_rows": sum(
            row.get("error_type") is None for row in generation["results"]
        ),
        "failed_condition_rows": sum(
            row.get("error_type") is not None for row in generation["results"]
        ),
        "conditions": condition_summaries,
        "indexing": {
            "repositories": len(index_rows),
            "successful_repositories": sum(
                row.get("status") == "success" for row in index_rows
            ),
            "java_source_files_total": sum(
                row.get("java_source_files", 0) for row in index_rows
            ),
            "methods_total": sum(row.get("num_methods", 0) for row in index_rows),
            "unparsed_files_total": sum(
                len(row.get("unparsed_files", [])) for row in index_rows
            ),
            "mean_index_time_seconds": _mean([
                row["index_time_seconds"]
                for row in index_rows
                if row.get("status") == "success"
                and row.get("index_time_seconds") is not None
            ]),
            "per_repository": [
                {
                    "repo_id": row.get("repo_id"),
                    "revision": row.get("revision"),
                    "java_source_files": row.get("java_source_files"),
                    "methods": row.get("num_methods"),
                    "index_time_seconds": row.get("index_time_seconds"),
                    "unparsed_file_count": len(row.get("unparsed_files", [])),
                    "status": row.get("status"),
                }
                for row in index_rows
            ],
        },
        "limitations": [
            "Five labelled Defects4J tasks are too few for generalization.",
            "Six Lang-8 ablation generations used qwen/qwen3.8-27b after the primary Groq model hit its TPD quota; other generation rows used the primary model. These rows are model-confounded and should not be pooled as a same-model ablation.",
            "The 5,000-token cap was enforced using the local tokenizer estimate. Provider-reported input tokens exceeded 5,000 in two completed whole-file requests; no completed request was rerun.",
            "Ground truth contains one bug method per task; no change-impact gold labels are present.",
        ],
    }
    generation["context_token_budget"] = budget
    generation["models_used"] = analysis["models_used"]
    generation["evaluation_notes"] = [
        "Raw predictions are preserved in predicted_bug_ids; canonical_predicted_bug_ids resolves only unique abbreviations among retrieved method identifiers.",
        "Provider-reported input tokens exceeded the local estimated 5,000-token budget in two completed whole-file requests.",
        "LANG-818 ACOR ablation rows completed on qwen/qwen3.8-27b after primary-model quota exhaustion; all other rows used the primary Groq model.",
    ]
    groq_results_path = args.groq_results.resolve()
    groq_temp = groq_results_path.with_suffix(groq_results_path.suffix + ".tmp")
    groq_temp.write_text(json.dumps(generation, indent=2), encoding="utf-8")
    groq_temp.replace(groq_results_path)
    groq_csv_path = groq_results_path.with_suffix(".csv")
    groq_csv_rows = []
    for row in generation["results"]:
        groq_csv_rows.append({
            **{key: value for key, value in row.items() if key != "metrics"},
            **{f"metric_{key}": value for key, value in row.get("metrics", {}).items()},
            "gold_method_ids": json.dumps(row.get("gold_method_ids", [])),
            "retrieved_method_ids": json.dumps(row.get("retrieved_method_ids", [])),
            "predicted_bug_ids": json.dumps(row.get("predicted_bug_ids", [])),
            "canonical_predicted_bug_ids": json.dumps(
                row.get("canonical_predicted_bug_ids", [])
            ),
        })
    _csv_write(groq_csv_path, groq_csv_rows)

    prefix = args.output_prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix.with_suffix(".json")
    json_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    _csv_write(prefix.with_name(prefix.name + "-results.csv"), detailed_rows)

    aggregate_rows = []
    for name, summary in condition_summaries.items():
        rec = summary["retrieval_metrics"]
        aggregate_rows.append({
            "condition": name,
            "successful_runs": summary["successful_runs"],
            "failed_runs": summary["failed_runs"],
            "models": json.dumps(summary["models"], sort_keys=True),
            "recall": rec["bug_localization_retrieval_recall"],
            "recall_at_1": rec["bug_localization_retrieval_recall_at_1"],
            "recall_at_5": rec["bug_localization_retrieval_recall_at_5"],
            "recall_at_10": rec["bug_localization_retrieval_recall_at_10"],
            "mrr": rec["bug_localization_retrieval_mrr"],
            "model_localization_hit_rate": summary["model_bug_localization_hit_rate"],
            "model_localization_exact_accuracy": summary[
                "model_bug_localization_exact_set_accuracy"
            ],
            "mean_actual_provider_input_tokens": summary[
                "mean_actual_provider_input_tokens"
            ],
            "mean_estimated_input_tokens": summary["mean_estimated_input_tokens"],
            "actual_token_reduction_vs_whole_file": summary[
                "actual_token_reduction_vs_whole_file"
            ],
            "mean_provider_latency_ms": summary["mean_provider_latency_ms"],
            "mean_retrieval_latency_ms": summary["mean_retrieval_latency_ms"],
            "actual_input_budget_overruns": summary["actual_input_budget_overruns"],
        })
    aggregate_path = prefix.with_name(prefix.name + "-conditions.csv")
    _csv_write(aggregate_path, aggregate_rows)

    lines = [
        "# Apache Commons Lang ACOR benchmark",
        "",
        f"- Tasks: {len(tasks)} pinned Defects4J bug-localization tasks.",
        f"- Indexing: {analysis['indexing']['successful_repositories']}/5 revisions indexed; "
        f"{analysis['indexing']['java_source_files_total']} Java production source files, "
        f"{analysis['indexing']['methods_total']} methods, "
        f"{analysis['indexing']['unparsed_files_total']} unparsed files.",
        f"- Mean indexing time: {analysis['indexing']['mean_index_time_seconds']:.2f} s per revision.",
        f"- Retrieval-only: {retrieval['execution_mode']}; no LLM calls.",
        f"- Generation: Groq primary `{generation['model']}`; models used: "
        f"{', '.join(analysis['models_used'])}.",
        f"- Input budget: {budget} local-estimator tokens for every condition; "
        "provider-reported usage is reported separately.",
        f"- Generation outcomes: {analysis['completed_condition_rows']}/{analysis['expected_condition_rows']} "
        f"successful rows; {analysis['failed_condition_rows']} failed.",
        "",
        "## Results by condition",
        "",
        "| Condition | Success | Recall | R@1 | R@5 | R@10 | MRR | Model hit | Exact accuracy | Actual input tokens | Token reduction | Provider latency ms | Retrieval ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in condition_summaries.items():
        rec = summary["retrieval_metrics"]
        def fmt(value: float | None, digits: int = 3) -> str:
            return "n/a" if value is None else f"{value:.{digits}f}"
        lines.append(
            f"| {name} | {summary['successful_runs']}/{summary['tasks']} | "
            f"{fmt(rec['bug_localization_retrieval_recall'])} | "
            f"{fmt(rec['bug_localization_retrieval_recall_at_1'])} | "
            f"{fmt(rec['bug_localization_retrieval_recall_at_5'])} | "
            f"{fmt(rec['bug_localization_retrieval_recall_at_10'])} | "
            f"{fmt(rec['bug_localization_retrieval_mrr'])} | "
            f"{fmt(summary['model_bug_localization_hit_rate'])} | "
            f"{fmt(summary['model_bug_localization_exact_set_accuracy'])} | "
            f"{fmt(summary['mean_actual_provider_input_tokens'], 1)} | "
            f"{fmt(summary['actual_token_reduction_vs_whole_file'] * 100 if summary['actual_token_reduction_vs_whole_file'] is not None else None, 1)}% | "
            f"{fmt(summary['mean_provider_latency_ms'], 1)} | "
            f"{fmt(summary['mean_retrieval_latency_ms'], 1)} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "These are descriptive measurements on five tasks, not evidence of broad superiority. "
        "Retrieval Recall@K/MRR evaluates whether the labelled method was included and ranked; "
        "model hit/exact accuracy evaluates the generated answer after resolving unambiguous "
        "short class names against methods actually retrieved. The full prompt budget used "
        "local estimates. Provider usage exceeded 5,000 tokens in "
        f"{sum(s['actual_input_budget_overruns'] for s in condition_summaries.values())} "
        "completed rows, demonstrating estimator/provider-tokenizer mismatch.",
        "",
        "Six LANG-818 ACOR signal/ranker ablation generations used `qwen/qwen3.8-27b` after "
        "the primary Groq model hit its daily token quota. Thus primary baseline conditions "
        "use the same model, while LANG-818 ablation generation comparisons are model-confounded. "
        "The retrieval-only ablations remain same-system and GPU-free.",
        "",
        "The dataset has no change-impact labels, and no statistical significance or "
        "generalization claim is supported. Retrieval gains, if present, do not imply "
        "model-generation accuracy gains.",
        "",
        "## Reproduction",
        "",
        "```powershell",
        "cd backend",
        "python ..\\scripts\\evaluate_retrieval.py --tasks ..\\benchmarks\\commons-lang-defects4j.json --retrieval-only --include-ablations --token-budget 5000 --storage-dir <isolated-state> --indexing-results <index-report> --output ..\\benchmarks\\results\\commons-lang-retrieval-only-5000.json",
        "python ..\\scripts\\evaluate_retrieval.py --tasks ..\\benchmarks\\commons-lang-defects4j.json --include-ablations --token-budget 5000 --request-delay-seconds 60 --storage-dir <isolated-state> --indexing-results <index-report> --output ..\\benchmarks\\results\\commons-lang-groq-5000.json",
        "python ..\\scripts\\summarize_commons_lang_benchmark.py",
        "```",
        "",
        "## Output files",
        "",
        f"- Summary JSON: `{json_path}`",
        f"- Per-task CSV: `{prefix.with_name(prefix.name + '-results.csv')}`",
        f"- Condition summary CSV: `{aggregate_path}`",
    ])
    markdown_path = prefix.with_suffix(".md")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Summary JSON: {json_path}")
    print(f"Per-task CSV: {prefix.with_name(prefix.name + '-results.csv')}")
    print(f"Condition CSV: {aggregate_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
