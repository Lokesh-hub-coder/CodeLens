"""Controlled retrieval and change-impact benchmark runner.

Ground-truth tasks are supplied by the project owner. The runner uses the
same task prompt and configured LLM for every retrieval condition.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from app.acor.context_builder import build_context
from app.acor.ranker import rank_and_select
from app.acor.scorer import Candidate, ScoredCandidate, score_candidates
from app.api.schemas import ScoreBreakdown
from app.config import ACORWeights, Settings
from app.core.candidate_pipeline import get_candidates
from app.core.repo_index import RepoIndex
from app.graphs.symbol_index import SymbolKind
from app.llm.providers import LLMClient, MockLLMClient
from app.parsing.repo_walker import list_java_files, read_snippet
from app.parsing.source_filter import is_test_path
from app.retrieval.embeddings import EmbeddingModel
from app.retrieval.query_understanding import analyze_query
from app.retrieval.tokenizer import count_tokens

logger = logging.getLogger("codelens.evaluation")

EVALUATION_SYSTEM_PROMPT = """You are evaluating repository code retrieval.
Use only the repository excerpt. Return one JSON object and no other text:
{"bug_locations": ["Class#method(signature)"], "change_impact": ["Class#method(signature)"]}
Use exact method identifiers visible in the excerpt. Use empty arrays when the excerpt does not support a prediction.
"""

EVALUATION_CONDITIONS = (
    "whole_file",
    "semantic_only",
    "acor",
)
ABLATION_CONDITIONS = (
    "acor_no_semantic",
    "acor_no_symbol",
    "acor_no_call_graph",
    "acor_no_dependency",
    "acor_greedy",
    "acor_exact",
)


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    task_type: str
    query: str
    bug_gold_ids: tuple[str, ...]
    impact_gold_ids: tuple[str, ...]
    changed_symbol: str | None = None
    repo_id: str | None = None


@dataclass(frozen=True)
class BenchmarkCondition:
    name: str
    weights: ACORWeights
    ranker_strategy: str
    use_graph_candidate_generation: bool
    whole_file: bool = False


def load_tasks(path: Path) -> list[BenchmarkTask]:
    """Load explicit, externally labeled tasks; never synthesize gold labels."""
    raw_tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("Benchmark task file must contain a non-empty JSON array")

    tasks: list[BenchmarkTask] = []
    seen_ids: set[str] = set()
    for item in raw_tasks:
        if not isinstance(item, dict):
            raise ValueError("Every benchmark task must be a JSON object")
        task_id = item.get("id")
        task_type = item.get("type")
        query = item.get("query")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen_ids:
            raise ValueError("Each benchmark task needs a unique, non-empty id")
        if task_type not in {"bug_localization", "change_impact"}:
            raise ValueError(f"Unsupported benchmark task type for {task_id!r}")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Task {task_id!r} needs a non-empty query")

        bug_gold = item.get("bug_gold_ids", [])
        impact_gold = item.get("impact_gold_ids", [])
        changed_symbol = item.get("changed_symbol")
        repo_id = item.get("repo_id")
        if repo_id is not None and (
            not isinstance(repo_id, str) or not repo_id.strip()
        ):
            raise ValueError(f"Task {task_id!r} has an invalid repo_id")
        if not isinstance(bug_gold, list) or not isinstance(impact_gold, list):
            raise ValueError(f"Task {task_id!r} gold ids must be JSON arrays")
        if task_type == "bug_localization" and not bug_gold:
            raise ValueError(f"Bug-localization task {task_id!r} needs bug_gold_ids")
        if task_type == "change_impact" and (
            not isinstance(changed_symbol, str)
            or not changed_symbol.strip()
        ):
            raise ValueError(
                f"Change-impact task {task_id!r} needs impact_gold_ids and changed_symbol"
            )
        if not all(isinstance(method_id, str) and method_id for method_id in bug_gold + impact_gold):
            raise ValueError(f"Task {task_id!r} contains an invalid gold method id")

        tasks.append(BenchmarkTask(
            task_id=task_id,
            task_type=task_type,
            query=query,
            bug_gold_ids=tuple(bug_gold),
            impact_gold_ids=tuple(impact_gold),
            changed_symbol=changed_symbol,
            repo_id=repo_id,
        ))
        seen_ids.add(task_id)
    return tasks


def build_conditions(settings: Settings, include_ablations: bool = False) -> list[BenchmarkCondition]:
    base_weights = settings.acor_weights
    conditions = [
        BenchmarkCondition(
            "whole_file",
            base_weights,
            settings.ranker_strategy,
            False,
            whole_file=True,
        ),
        BenchmarkCondition(
            "semantic_only",
            ACORWeights(semantic=1, call_graph=0, dependency=0, symbol=0),
            settings.ranker_strategy,
            False,
        ),
        BenchmarkCondition(
            "acor",
            base_weights,
            settings.ranker_strategy,
            True,
        ),
    ]
    if include_ablations:
        def without(signal: str) -> ACORWeights:
            weights = {
                "semantic": base_weights.semantic,
                "call_graph": base_weights.call_graph,
                "dependency": base_weights.dependency,
                "symbol": base_weights.symbol,
            }
            weights[signal] = 0
            return ACORWeights(**weights)

        conditions.extend([
            BenchmarkCondition(
                "acor_no_semantic",
                without("semantic"),
                settings.ranker_strategy,
                True,
            ),
            BenchmarkCondition(
                "acor_no_symbol",
                without("symbol"),
                settings.ranker_strategy,
                True,
            ),
            BenchmarkCondition(
                "acor_no_call_graph",
                without("call_graph"),
                settings.ranker_strategy,
                True,
            ),
            BenchmarkCondition(
                "acor_no_dependency",
                without("dependency"),
                settings.ranker_strategy,
                True,
            ),
            BenchmarkCondition(
                "acor_greedy",
                base_weights,
                "greedy",
                True,
            ),
            BenchmarkCondition(
                "acor_exact",
                base_weights,
                "exact",
                True,
            ),
        ])
    return conditions


def _semantic_candidates(
    task: BenchmarkTask,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings,
) -> list[Candidate]:
    query_vector = embedding_model.encode_one(task.query)
    results = repo_index.vector_store.search(
        query_vector,
        top_k=settings.top_k_candidates,
    )
    candidates: dict[str, Candidate] = {}
    for result in results:
        metadata = result.metadata
        method_id = metadata.get("method_id")
        if not method_id or method_id in candidates:
            continue
        snippet = read_snippet(
            repo_index.repo_root / metadata["file_path"],
            metadata["start_line"],
            metadata["end_line"],
        )
        candidates[method_id] = Candidate(
            method_id=method_id,
            class_id=metadata["class_id"],
            name=metadata["name"],
            kind="method",
            file_path=metadata["file_path"],
            start_line=metadata["start_line"],
            end_line=metadata["end_line"],
            snippet=snippet,
            token_count=count_tokens(snippet),
            raw_semantic_score=float(result.score),
        )
    return list(candidates.values())


def _file_terms(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.casefold())


def _whole_file_context(
    task: BenchmarkTask,
    repo_index: RepoIndex,
    settings: Settings,
) -> tuple[str, list[str], int]:
    context_budget = _context_token_budget(task, settings)
    documents: list[tuple[str, str, list[str]]] = []
    for repo_file in list_java_files(repo_index.repo_root):
        if settings.exclude_test_sources and is_test_path(repo_file.relative_path):
            continue
        content = repo_file.absolute_path.read_text(encoding="utf-8", errors="replace")
        documents.append((repo_file.relative_path, content, _file_terms(content)))

    query_terms = _file_terms(task.query)
    document_frequency: dict[str, int] = {}
    for term in set(query_terms):
        document_frequency[term] = sum(term in set(tokens) for _, _, tokens in documents)
    average_length = (
        sum(len(tokens) for _, _, tokens in documents) / len(documents)
        if documents
        else 0.0
    )

    def bm25_score(tokens: list[str]) -> float:
        if not query_terms or average_length == 0:
            return 0.0
        frequencies: dict[str, int] = {}
        for term in tokens:
            frequencies[term] = frequencies.get(term, 0) + 1
        score = 0.0
        for term in set(query_terms):
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_frequency = math.log(
                1.0 + (len(documents) - df + 0.5) / (df + 0.5)
            )
            length_norm = 1.5 * (
                1.0 - 0.75 + 0.75 * len(tokens) / average_length
            )
            score += inverse_frequency * frequency * 2.5 / (frequency + length_norm)
        return score

    ranked = sorted(
        documents,
        key=lambda document: (-bm25_score(document[2]), document[0]),
    )
    selected_parts: list[str] = []
    selected_paths: set[str] = set()
    selected_path_order: list[str] = []
    for path, content, _ in ranked:
        part = f"// === {path} ===\n{content}\n"
        candidate_context = "\n".join([*selected_parts, part])
        if count_tokens(candidate_context) <= context_budget:
            selected_parts.append(part)
            selected_paths.add(path)
            selected_path_order.append(path)

    context = "\n".join(selected_parts)
    if count_tokens(context) > context_budget:
        raise RuntimeError("Whole-file baseline exceeded the shared prompt-token budget")

    file_rank = {
        path: rank
        for rank, path in enumerate(selected_path_order)
    }
    method_ranks: dict[str, tuple[int, int]] = {}
    for locations in repo_index.symbol_index._exact.values():
        for location in locations:
            if (
                location.kind == SymbolKind.METHOD
                and location.file_path in selected_paths
            ):
                method_ranks.setdefault(
                    location.symbol_id,
                    (file_rank[location.file_path], location.start_line),
                )
    method_ids = sorted(method_ranks, key=method_ranks.__getitem__)
    return context, method_ids, len(documents)


def _context_token_budget(task: BenchmarkTask, settings: Settings) -> int:
    fixed_tokens = count_tokens(EVALUATION_SYSTEM_PROMPT) + count_tokens(
        _prompt(task, "")
    )
    available = settings.default_token_budget - fixed_tokens
    if available < 0:
        raise ValueError(
            "The system prompt and task headers exceed the configured total "
            f"input-token budget ({fixed_tokens} > {settings.default_token_budget})"
        )
    return available


def _retrieval_metrics(
    retrieved: list[str],
    gold_ids: tuple[str, ...],
    prefix: str,
) -> dict[str, float | bool]:
    retrieved_set, gold_set = set(retrieved), set(gold_ids)
    true_positives = len(retrieved_set & gold_set)
    precision = true_positives / len(retrieved_set) if retrieved_set else 0.0
    recall = true_positives / len(gold_set) if gold_set else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    reciprocal_rank = next(
        (1.0 / rank for rank, item in enumerate(retrieved, 1) if item in gold_set),
        0.0,
    )
    return {
        f"{prefix}_retrieval_precision": precision,
        f"{prefix}_retrieval_recall": recall,
        f"{prefix}_retrieval_f1": f1,
        f"{prefix}_retrieval_hit": bool(true_positives),
        f"{prefix}_retrieval_mrr": reciprocal_rank,
        **{
            f"{prefix}_retrieval_recall_at_{cutoff}": (
                len(set(retrieved[:cutoff]) & gold_set) / len(gold_set)
                if gold_set
                else 1.0
            )
            for cutoff in (1, 5, 10)
        },
    }


def _prompt(task: BenchmarkTask, context: str) -> str:
    task_kind = (
        "bug localization"
        if task.task_type == "bug_localization"
        else "change-impact analysis"
    )
    changed = f"\nChanged symbol: {task.changed_symbol}" if task.changed_symbol else ""
    return (
        f"Task type: {task_kind}\n"
        f"Developer query: {task.query}{changed}\n\n"
        "Repository excerpt:\n"
        f"{context}\n"
    )


def _parse_predictions(response: str) -> tuple[list[str], list[str]]:
    start = response.find("{")
    end = response.rfind("}")
    if start < 0 or end < start:
        return [], []
    try:
        payload = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return [], []
    if not isinstance(payload, dict):
        return [], []

    def read_ids(field: str) -> list[str]:
        value = payload.get(field, [])
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))

    return read_ids("bug_locations"), read_ids("change_impact")


def _canonicalize_predictions(
    predicted: list[str],
    retrieved_ids: list[str],
) -> list[str]:
    """Resolve unambiguous short class names to retrieved fully-qualified IDs."""
    normalized_retrieved = {
        method_id: re.sub(r"\s+", "", method_id)
        for method_id in retrieved_ids
    }
    canonical: list[str] = []
    for prediction in predicted:
        normalized_prediction = re.sub(r"\s+", "", prediction)
        matches = [
            method_id
            for method_id, normalized_id in normalized_retrieved.items()
            if normalized_id == normalized_prediction
            or normalized_id.endswith("." + normalized_prediction)
        ]
        resolved = matches[0] if len(matches) == 1 else prediction
        if resolved not in canonical:
            canonical.append(resolved)
    return canonical


def _classification_metrics(predicted: list[str], gold: tuple[str, ...]) -> dict[str, float]:
    predicted_set, gold_set = set(predicted), set(gold)
    true_positives = len(predicted_set & gold_set)
    precision = (
        true_positives / len(predicted_set)
        if predicted_set
        else (1.0 if not gold_set else 0.0)
    )
    recall = (
        true_positives / len(gold_set)
        if gold_set
        else (1.0 if not predicted_set else 0.0)
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def _condition_candidates(
    task: BenchmarkTask,
    condition: BenchmarkCondition,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings,
) -> list[ScoredCandidate] | list[Candidate]:
    query = analyze_query(task.query)
    if condition.use_graph_candidate_generation:
        candidates = get_candidates(
            query=query,
            repo_index=repo_index,
            embedding_model=embedding_model,
            settings=settings,
            changed_symbol=task.changed_symbol,
        )
    else:
        candidates = _semantic_candidates(task, repo_index, embedding_model, settings)

    weights = condition.weights
    if condition.name == "semantic_only":
        return [
            ScoredCandidate(
                **{
                    field: getattr(candidate, field)
                    for field in Candidate.__dataclass_fields__
                },
                score=ScoreBreakdown(
                    semantic=candidate.raw_semantic_score,
                    call_graph=0,
                    dependency=0,
                    symbol=0,
                    composite=candidate.raw_semantic_score,
                ),
            )
            for candidate in candidates
        ]
    return score_candidates(
        candidates,
        query,
        repo_index.call_graph,
        repo_index.dependency_graph,
        repo_index.symbol_index,
        weights,
        max_hops=settings.graph_max_hops,
    )


def _condition_context(
    task: BenchmarkTask,
    condition: BenchmarkCondition,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings,
) -> tuple[str, list[str], int]:
    if condition.whole_file:
        return _whole_file_context(task, repo_index, settings)

    candidates = _condition_candidates(
        task,
        condition,
        repo_index,
        embedding_model,
        settings,
    )
    candidate_count = len(candidates)
    candidates = rank_and_select(
        candidates,
        _context_token_budget(task, settings),
        settings.model_copy(update={"ranker_strategy": condition.ranker_strategy}),
    ).selected
    candidates = list(candidates)
    context = build_context(candidates, repo_index.repo_root)
    while (
        candidates
        and count_tokens(EVALUATION_SYSTEM_PROMPT)
        + count_tokens(_prompt(task, context))
        > settings.default_token_budget
    ):
        candidates.pop()
        context = build_context(candidates, repo_index.repo_root)
    retrieved_ids = [candidate.method_id for candidate in candidates]
    if count_tokens(EVALUATION_SYSTEM_PROMPT) + count_tokens(_prompt(task, context)) > settings.default_token_budget:
        raise RuntimeError("Formatted prompt exceeds the shared total input-token budget")
    return context, retrieved_ids, candidate_count


def _retrieval_task_metrics(
    task: BenchmarkTask,
    retrieved_ids: list[str],
) -> dict[str, float | bool]:
    if task.task_type == "bug_localization":
        return _retrieval_metrics(
            retrieved_ids,
            task.bug_gold_ids,
            "bug_localization",
        )
    return _retrieval_metrics(
        retrieved_ids,
        task.impact_gold_ids,
        "change_impact",
    )


def _run_condition(
    task: BenchmarkTask,
    condition: BenchmarkCondition,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings,
    llm_client: LLMClient,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        retrieval_started = time.perf_counter()
        context, retrieved_ids, candidate_count = _condition_context(
            task,
            condition,
            repo_index,
            embedding_model,
            settings,
        )
        retrieval_latency_ms = (time.perf_counter() - retrieval_started) * 1000
        user_prompt = _prompt(task, context)
        prompt_tokens_estimated = count_tokens(EVALUATION_SYSTEM_PROMPT) + count_tokens(user_prompt)
        llm_started = time.perf_counter()
        response = llm_client.generate(EVALUATION_SYSTEM_PROMPT, user_prompt)
        llm_wall_latency_ms = (time.perf_counter() - llm_started) * 1000
        bug_predictions, impact_predictions = _parse_predictions(response.text)
        bug_predictions = _canonicalize_predictions(
            bug_predictions,
            retrieved_ids,
        )
        impact_predictions = _canonicalize_predictions(
            impact_predictions,
            retrieved_ids,
        )
        effective_ranker = (
            "greedy"
            if condition.ranker_strategy == "exact"
            and candidate_count > settings.exact_knapsack_max_candidates
            else condition.ranker_strategy
        )

        metrics: dict[str, Any] = {}
        if task.task_type == "bug_localization":
            metrics = _retrieval_task_metrics(task, retrieved_ids) | {
                "bug_localization_hit": bool(set(bug_predictions) & set(task.bug_gold_ids)),
                "bug_localization_exact": set(bug_predictions) == set(task.bug_gold_ids),
                "bug_localization_accuracy": float(
                    set(bug_predictions) == set(task.bug_gold_ids)
                ),
            }
        else:
            metrics = _retrieval_task_metrics(task, retrieved_ids) | {
                f"model_{name}": value
                for name, value in _classification_metrics(
                    impact_predictions,
                    task.impact_gold_ids,
                ).items()
            }

        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "condition": condition.name,
            "gold_method_ids": list(
                task.bug_gold_ids
                if task.task_type == "bug_localization"
                else task.impact_gold_ids
            ),
            "retrieved_method_ids": retrieved_ids,
            "predicted_bug_ids": bug_predictions,
            "predicted_impact_ids": impact_predictions,
            "metrics": metrics,
            "actual_input_tokens": response.prompt_tokens,
            "estimated_input_tokens": prompt_tokens_estimated,
            "output_tokens": response.completion_tokens,
            "llm_latency_ms": response.latency_ms,
            "llm_wall_latency_ms": llm_wall_latency_ms,
            "retrieval_latency_ms": retrieval_latency_ms,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
            "candidate_count": candidate_count,
            "ranker_strategy": effective_ranker,
            "context_tokens_estimated": count_tokens(context),
            "error_type": None,
        }
    except Exception as exc:
        logger.exception("Benchmark condition %s failed for task %s", condition.name, task.task_id)
        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "condition": condition.name,
            "retrieved_method_ids": [],
            "predicted_bug_ids": [],
            "predicted_impact_ids": [],
            "metrics": {},
            "actual_input_tokens": None,
            "estimated_input_tokens": None,
            "output_tokens": None,
            "llm_latency_ms": None,
            "llm_wall_latency_ms": None,
            "retrieval_latency_ms": None,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
            "candidate_count": 0,
            "ranker_strategy": None,
            "context_tokens_estimated": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize_results(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate measured values without substituting estimates for actuals."""
    baseline_tokens = {
        row["task_id"]: row["actual_input_tokens"]
        for row in results
        if row["condition"] == "whole_file"
    }
    by_condition: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        row["actual_token_reduction_vs_whole_file"] = None
        baseline = baseline_tokens.get(row["task_id"])
        actual = row["actual_input_tokens"]
        if (
            baseline is not None
            and actual is not None
            and baseline > 0
        ):
            row["actual_token_reduction_vs_whole_file"] = 1.0 - actual / baseline
        by_condition.setdefault(row["condition"], []).append(row)

    summaries: dict[str, dict[str, Any]] = {}
    for condition, rows in by_condition.items():
        metric_names = sorted({
            name
            for row in rows
            for name in row["metrics"]
        })
        summaries[condition] = {
            "tasks": len(rows),
            "successful_runs": sum(row["error_type"] is None for row in rows),
            "mean_actual_input_tokens": _mean([
                row["actual_input_tokens"]
                for row in rows
                if row["actual_input_tokens"] is not None
            ]),
            "mean_estimated_input_tokens": _mean([
                row["estimated_input_tokens"]
                for row in rows
                if row["estimated_input_tokens"] is not None
            ]),
            "mean_actual_token_reduction_vs_whole_file": _mean([
                row["actual_token_reduction_vs_whole_file"]
                for row in rows
                if row["actual_token_reduction_vs_whole_file"] is not None
            ]),
            "mean_total_latency_ms": _mean([
                row["total_latency_ms"] for row in rows
            ]),
            "mean_retrieval_latency_ms": _mean([
                row["retrieval_latency_ms"]
                for row in rows
                if row["retrieval_latency_ms"] is not None
            ]),
            "mean_llm_latency_ms": _mean([
                row["llm_latency_ms"]
                for row in rows
                if row["llm_latency_ms"] is not None
            ]),
            "metrics": {
                name: _mean([
                    row["metrics"][name]
                    for row in rows
                    if name in row["metrics"]
                ])
                for name in metric_names
            },
        }
    return summaries


def run_retrieval_evaluation(
    tasks: list[BenchmarkTask],
    repo_index: RepoIndex | Mapping[str, RepoIndex],
    embedding_model: EmbeddingModel,
    settings: Settings,
    include_ablations: bool = False,
) -> dict[str, Any]:
    """Measure retrieval against gold labels without making model calls."""
    repo_indices = (
        {repo_index.repo_id: repo_index}
        if isinstance(repo_index, RepoIndex)
        else dict(repo_index)
    )
    default_repo_id = next(iter(repo_indices)) if len(repo_indices) == 1 else None
    conditions = build_conditions(settings, include_ablations)
    results: list[dict[str, Any]] = []
    for task in tasks:
        task_repo_id = task.repo_id or default_repo_id
        if task_repo_id is None or task_repo_id not in repo_indices:
            raise ValueError(
                f"Task {task.task_id!r} needs a repo_id with an indexed repository"
            )
        repo = repo_indices[task_repo_id]
        for condition in conditions:
            started = time.perf_counter()
            try:
                retrieval_started = time.perf_counter()
                context, retrieved_ids, candidate_count = _condition_context(
                    task,
                    condition,
                    repo,
                    embedding_model,
                    settings,
                )
                retrieval_latency_ms = (
                    time.perf_counter() - retrieval_started
                ) * 1000
                prompt_tokens_estimated = (
                    count_tokens(EVALUATION_SYSTEM_PROMPT)
                    + count_tokens(_prompt(task, context))
                )
                if prompt_tokens_estimated > settings.default_token_budget:
                    raise RuntimeError(
                        "Formatted prompt exceeds the shared total input-token budget"
                    )
                row = {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "condition": condition.name,
                    "gold_method_ids": list(
                        task.bug_gold_ids
                        if task.task_type == "bug_localization"
                        else task.impact_gold_ids
                    ),
                    "repository_id": repo.repo_id,
                    "retrieved_method_ids": retrieved_ids,
                    "metrics": _retrieval_task_metrics(task, retrieved_ids),
                    "actual_input_tokens": None,
                    "estimated_input_tokens": prompt_tokens_estimated,
                    "total_latency_ms": (time.perf_counter() - started) * 1000,
                    "retrieval_latency_ms": retrieval_latency_ms,
                    "candidate_count": candidate_count,
                    "context_tokens_estimated": count_tokens(context),
                    "error_type": None,
                    "error_message": None,
                }
            except Exception as exc:
                logger.exception(
                    "Retrieval evaluation condition %s failed for task %s",
                    condition.name,
                    task.task_id,
                )
                row = {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "condition": condition.name,
                    "gold_method_ids": list(
                        task.bug_gold_ids
                        if task.task_type == "bug_localization"
                        else task.impact_gold_ids
                    ),
                    "repository_id": repo.repo_id,
                    "retrieved_method_ids": [],
                    "metrics": {},
                    "actual_input_tokens": None,
                    "estimated_input_tokens": None,
                    "total_latency_ms": (time.perf_counter() - started) * 1000,
                    "retrieval_latency_ms": None,
                    "candidate_count": 0,
                    "context_tokens_estimated": None,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            results.append(row)

    summary: dict[str, dict[str, Any]] = {}
    baseline_estimated = {
        row["task_id"]: row["estimated_input_tokens"]
        for row in results
        if row["condition"] == "whole_file"
    }
    for row in results:
        baseline = baseline_estimated.get(row["task_id"])
        actual = row["estimated_input_tokens"]
        row["estimated_token_reduction_vs_whole_file"] = (
            1.0 - actual / baseline
            if baseline and actual is not None
            else None
        )
    for condition in conditions:
        condition_rows = [r for r in results if r["condition"] == condition.name]
        metric_names = sorted({
            name
            for row in condition_rows
            for name in row["metrics"]
        })
        summary[condition.name] = {
            "tasks": len(condition_rows),
            "successful_runs": sum(row["error_type"] is None for row in condition_rows),
            "mean_estimated_input_tokens": _mean([
                row["estimated_input_tokens"]
                for row in condition_rows
                if row["estimated_input_tokens"] is not None
            ]),
            "mean_estimated_token_reduction_vs_whole_file": _mean([
                row["estimated_token_reduction_vs_whole_file"]
                for row in condition_rows
                if row["estimated_token_reduction_vs_whole_file"] is not None
            ]),
            "mean_total_latency_ms": _mean([
                row["total_latency_ms"] for row in condition_rows
            ]),
            "mean_retrieval_latency_ms": _mean([
                row["retrieval_latency_ms"]
                for row in condition_rows
                if row["retrieval_latency_ms"] is not None
            ]),
            "metrics": {
                name: _mean([
                    row["metrics"][name]
                    for row in condition_rows
                    if name in row["metrics"]
                ])
                for name in metric_names
            },
        }
    repository_ids = sorted(repo_indices)
    return {
        "protocol_version": 2,
        "execution_mode": "retrieval_only_no_model_requests",
        "repository_id": repository_ids[0] if len(repository_ids) == 1 else "multiple",
        "repository_ids": repository_ids,
        "provider": "none",
        "model": "not invoked",
        "task_count": len(tasks),
        "context_token_budget": settings.default_token_budget,
        "conditions": [
            asdict(condition) | {"weights": asdict(condition.weights)}
            for condition in conditions
        ],
        "results": results,
        "summary": summary,
        "token_count_note": (
            "No model call was made; actual_input_tokens is null. "
            "estimated_input_tokens and estimated reduction use the local tokenizer."
        ),
    }


def run_benchmark(
    tasks: list[BenchmarkTask],
    repo_index: RepoIndex | Mapping[str, RepoIndex],
    embedding_model: EmbeddingModel,
    settings: Settings,
    llm_client: LLMClient,
    include_ablations: bool = False,
    request_delay_seconds: float = 0,
) -> dict[str, Any]:
    if request_delay_seconds < 0:
        raise ValueError("request_delay_seconds cannot be negative")
    if isinstance(llm_client, MockLLMClient):
        raise ValueError(
            "Research evaluation requires a real configured LLM provider; mock responses are not benchmark evidence"
        )
    conditions = build_conditions(settings, include_ablations)
    repo_indices = (
        {repo_index.repo_id: repo_index}
        if isinstance(repo_index, RepoIndex)
        else dict(repo_index)
    )
    default_repo_id = next(iter(repo_indices)) if len(repo_indices) == 1 else None
    results: list[dict[str, Any]] = []
    first_request = True
    for task in tasks:
        task_repo_id = task.repo_id or default_repo_id
        if task_repo_id is None or task_repo_id not in repo_indices:
            raise ValueError(
                f"Task {task.task_id!r} needs a repo_id with an indexed repository"
            )
        task_repo = repo_indices[task_repo_id]
        for condition in conditions:
            if not first_request and request_delay_seconds:
                time.sleep(request_delay_seconds)
            first_request = False
            row = _run_condition(
                task,
                condition,
                task_repo,
                embedding_model,
                settings,
                llm_client,
            )
            row["repository_id"] = task_repo.repo_id
            results.append(row)
    summary = summarize_results(results)
    model_names = {
        "groq": settings.groq_model_name,
        "openai": settings.llm_model_name,
        "llama": settings.llama_model_name,
        "qwen": settings.qwen_model_name,
    }
    return {
        "protocol_version": 1,
        "repository_id": (
            next(iter(repo_indices))
            if len(repo_indices) == 1
            else "multiple"
        ),
        "repository_ids": sorted(repo_indices),
        "provider": settings.llm_provider,
        "model": model_names.get(settings.llm_provider, settings.llm_model_name),
        "task_count": len(tasks),
        "request_delay_seconds": request_delay_seconds,
        "conditions": [asdict(condition) | {"weights": asdict(condition.weights)} for condition in conditions],
        "results": results,
        "summary": summary,
        "token_count_note": (
            "actual_input_tokens comes only from provider-reported usage; "
            "estimated_input_tokens is a local tokenizer proxy"
        ),
    }
