"""Reproducible baseline-vs-ACOR bug-fix benchmark orchestration.

The external coding-agent adapter is invoked identically for both conditions
and reports model/tool telemetry through a validated JSONL event file.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import platform
import random
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Sequence

from app.config import Settings
from app.evaluation.benchmark import (
    BenchmarkCondition,
    BenchmarkTask,
    _condition_context,
)
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.tokenizer import count_tokens

logger = logging.getLogger("codelens.agent_benchmark")

CONDITIONS = ("baseline", "acor")
SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".md", ".py", ".rs", ".scala", ".ts", ".tsx",
    ".xml", ".yaml", ".yml",
}
RESULT_FIELDS = (
    "task_id", "condition", "repetition", "run_order", "repository_commit",
    "provider", "model", "agent_version", "prompt_version",
    "run_status", "started_at", "completed_at",
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_tokens", "total_tokens", "token_measurement_source",
    "retrieval_context_tokens", "naive_baseline_tokens_estimated",
    "estimated_input_tokens",
    "retrieval_latency_ms", "model_latency_ms", "model_ttft_ms",
    "agent_execution_ms",
    "tool_execution_ms", "test_execution_ms", "total_wall_time_ms", "cost",
    "cost_currency", "model_request_count", "retry_count", "tool_call_count",
    "agent_exit_code", "regression_test_exit_code", "full_test_exit_code",
    "regression_test_timed_out", "full_test_timed_out",
    "regression_test_passed", "full_test_suite_passed", "patch_correct",
    "build_failed", "regression_failing_tests", "full_suite_failing_tests",
    "regression_test_log", "full_test_log", "patch_file", "event_file",
    "task_file", "context_file", "prompt_file", "failure_reason", "environment", "model_config",
    "tool_permissions",
)


@dataclass(frozen=True)
class BugFixTask:
    task_id: str
    repository_path: Path
    repository_commit: str
    description: str
    repository_id: str
    regression_test_command: tuple[str, ...]
    full_test_command: tuple[str, ...]


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_command(value: Any, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(part, str) or not part for part in value)
    ):
        raise ValueError(f"{name} must be a non-empty array of command arguments")
    return tuple(value)


def load_experiment(path: Path) -> dict[str, Any]:
    """Load and validate experiment metadata and its fixed bug tasks."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Experiment manifest must be a JSON object")
    for key in ("experiment_id", "provider", "model", "prompt_version"):
        _require_string(raw.get(key), key)
    command = _require_command(raw.get("agent_command"), "agent_command")
    permissions = _require_command(raw.get("tool_permissions"), "tool_permissions")
    if any(permission.casefold().startswith(("codelens", "acor")) for permission in permissions):
        raise ValueError(
            "tool_permissions must not expose ACOR retrieval tools; ACOR context is supplied separately"
        )
    model_config = raw.get("model_config", {})
    if not isinstance(model_config, dict):
        raise ValueError("model_config must be a JSON object")
    tasks_data = raw.get("tasks")
    if not isinstance(tasks_data, list) or not tasks_data:
        raise ValueError("tasks must be a non-empty array")
    tasks: list[BugFixTask] = []
    seen: set[str] = set()
    for entry in tasks_data:
        if not isinstance(entry, dict):
            raise ValueError("Every task must be a JSON object")
        task_id = _require_string(entry.get("task_id"), "task_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id):
            raise ValueError(f"Task id {task_id!r} must be a safe filename component")
        if task_id in seen:
            raise ValueError(f"Duplicate task_id: {task_id}")
        seen.add(task_id)
        commit = _require_string(entry.get("repository_commit"), "repository_commit")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise ValueError(f"Task {task_id!r} requires a full 40-character commit SHA")
        repository_path = Path(
            _require_string(entry.get("repository_path"), "repository_path")
        ).resolve()
        if not repository_path.is_dir():
            raise ValueError(f"Repository path does not exist: {repository_path}")
        repository_id = _require_string(entry.get("repository_id"), "repository_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repository_id):
            raise ValueError(f"Repository id {repository_id!r} must be a safe filename component")
        tasks.append(BugFixTask(
            task_id=task_id,
            repository_path=repository_path,
            repository_commit=commit.lower(),
            description=_require_string(entry.get("description"), "description"),
            repository_id=repository_id,
            regression_test_command=_require_command(
                entry.get("regression_test_command"), "regression_test_command"
            ),
            full_test_command=_require_command(
                entry.get("full_test_command"), "full_test_command"
            ),
        ))
    repetitions = raw.get("repetitions", 1)
    timeout_seconds = raw.get("timeout_seconds", 1800)
    test_timeout_seconds = raw.get("test_timeout_seconds", timeout_seconds)
    token_budget = raw.get("token_budget", 8000)
    for name, number, minimum in (
        ("repetitions", repetitions, 1),
        ("timeout_seconds", timeout_seconds, 1),
        ("test_timeout_seconds", test_timeout_seconds, 1),
        ("token_budget", token_budget, 1),
    ):
        if isinstance(number, bool) or not isinstance(number, int) or number < minimum:
            raise ValueError(f"{name} must be an integer of at least {minimum}")
    raw["agent_command"] = list(command)
    raw["tool_permissions"] = list(permissions)
    raw["model_config"] = model_config
    raw["tasks"] = tasks
    raw["repetitions"] = repetitions
    raw["timeout_seconds"] = timeout_seconds
    raw["test_timeout_seconds"] = test_timeout_seconds
    raw["token_budget"] = token_budget
    return raw


def aggregate_events(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate provider-reported request usage and adapter-reported tools."""
    requests = [event for event in events if event.get("type") == "model_request"]
    tools = [event for event in events if event.get("type") == "tool_call"]
    if any(event.get("type") not in {"model_request", "tool_call"} for event in events):
        raise ValueError("Telemetry contains an unsupported event type")
    usage_fields = (
        "input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_tokens", "total_tokens",
    )

    def sum_if_complete(field: str) -> int | None:
        values = [
            event.get(field)
            if event.get("token_measurement_source") == "provider_reported"
            else None
            for event in requests
        ]
        if not values or any(value is None for value in values):
            return None
        return sum(values)

    for event in requests:
        for field in ("provider", "model", "status"):
            _require_string(event.get(field), f"model_request.{field}")
        source = event.get("token_measurement_source", "unavailable")
        if source not in {"provider_reported", "unavailable", "local_estimate"}:
            raise ValueError(f"Unsupported token measurement source: {source}")
        for field in usage_fields:
            value = event.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field} must be a non-negative integer or null")
            if source != "provider_reported" and value is not None:
                raise ValueError(
                    f"{field} must not be presented as actual usage when source is {source}"
                )
        for field in ("generation_ms", "time_to_first_token_ms"):
            value = event.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{field} must be a non-negative number or null")
    for event in tools:
        _require_string(event.get("tool"), "tool_call.tool")
        _require_string(event.get("status"), "tool_call.status")
        value = event.get("duration_ms")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
        ):
            raise ValueError("tool duration_ms must be a non-negative number or null")

    sources = {event.get("token_measurement_source", "unavailable") for event in requests}
    source = (
        "provider_reported"
        if (
            requests
            and sources == {"provider_reported"}
            and all(
                all(event.get(field) is not None for field in usage_fields)
                for event in requests
            )
        )
        else "provider_reported_partial"
        if requests and sources == {"provider_reported"}
        else "mixed_or_partial"
        if "provider_reported" in sources
        else "unavailable"
    )
    return {
        **{field: sum_if_complete(field) for field in usage_fields},
        "token_measurement_source": source,
        "model_request_count": len(requests),
        "retry_count": sum(
            1 for event in requests
            if event.get("is_retry") is True
            or (isinstance(event.get("attempt"), int) and event["attempt"] > 1)
        ),
        "model_latency_ms": (
            sum(event["generation_ms"] for event in requests if event.get("generation_ms") is not None)
            if requests and all(event.get("generation_ms") is not None for event in requests)
            else None
        ),
        "model_ttft_ms": (
            mean(event["time_to_first_token_ms"] for event in requests
                 if event.get("time_to_first_token_ms") is not None)
            if requests and all(event.get("time_to_first_token_ms") is not None for event in requests)
            else None
        ),
        "tool_call_count": len(tools),
        "tool_execution_ms": (
            sum(event["duration_ms"] for event in tools if event.get("duration_ms") is not None)
            if tools and all(event.get("duration_ms") is not None for event in tools)
            else None
        ),
        "request_statuses": [event.get("status") for event in requests],
        "provider_models": sorted({
            (event.get("provider"), event.get("model"))
            for event in requests
            if event.get("provider") or event.get("model")
        }),
        "estimated_input_tokens": _sum_optional(requests, "estimated_input_tokens"),
    }


def _sum_optional(events: Sequence[dict[str, Any]], field: str) -> int | None:
    values = [event.get(field) for event in events]
    if not values or any(
        value is None or isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        return None
    return sum(values)


def calculate_cost(
    usage: dict[str, Any],
    pricing: dict[str, Any] | None,
) -> float | None:
    """Calculate cost only when usage and all applicable rates are known."""
    if not pricing or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    input_rate = pricing.get("input_per_million")
    output_rate = pricing.get("output_per_million")
    cached_rate = pricing.get("cached_input_per_million")
    reasoning_rate = pricing.get("reasoning_per_million")
    for key, value in pricing.items():
        if key.endswith("_per_million") and value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
        ):
            raise ValueError(f"{key} must be a non-negative number or null")
    if input_rate is None or output_rate is None:
        return None
    cached = usage.get("cached_input_tokens")
    if cached_rate is not None and cached is None:
        return None
    cached = cached or 0
    input_tokens = usage["input_tokens"]
    if cached > input_tokens:
        raise ValueError("cached_input_tokens cannot exceed input_tokens")
    reasoning = usage.get("reasoning_tokens")
    if reasoning_rate is not None and reasoning is None:
        return None
    if reasoning is not None and reasoning > usage["output_tokens"]:
        raise ValueError("reasoning_tokens cannot exceed output_tokens")
    billed_input = (input_tokens - cached) * input_rate
    if cached_rate is not None:
        billed_input += cached * cached_rate
    if reasoning_rate is None:
        billed_output = usage["output_tokens"] * output_rate
    else:
        billed_output = (
            (usage["output_tokens"] - (reasoning or 0)) * output_rate
            + (reasoning or 0) * reasoning_rate
        )
    return (billed_input + billed_output) / 1_000_000


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _tracked_repository_tokens(repository: Path) -> int:
    tracked = _git(repository, "ls-files", "-z").split("\0")
    total = 0
    for relative in tracked:
        if not relative or Path(relative).suffix.casefold() not in SOURCE_EXTENSIONS:
            continue
        source = repository / relative
        try:
            total += count_tokens(source.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError):
            continue
    return total


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid telemetry JSON at line {line_number}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"Telemetry line {line_number} must contain an object")
            events.append(event)
    return events


def _run_test(
    command: Sequence[str],
    repository: Path,
    timeout_seconds: int,
    log_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            list(command),
            cwd=repository,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        log_path.write_text(output, encoding="utf-8")
        failed = re.findall(
            r"(?im)^(?:\[ERROR\]\s*)?([^:\r\n]+(?:Test|Tests)[^:\r\n]*):",
            output,
        )
        lower_output = output.casefold()
        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "timed_out": False,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "failing_tests": list(dict.fromkeys(failed)),
            "log_file": str(log_path),
            "build_failed": any(marker in lower_output for marker in (
                "compilation failure", "compilation error", "build failed",
                "failed to execute goal", "compiler error",
            )),
        }
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part.decode("utf-8", errors="replace")
            if isinstance(part, bytes)
            else part or ""
            for part in (exc.stdout, exc.stderr)
        )
        log_path.write_text(output, encoding="utf-8")
        return {
            "passed": False,
            "exit_code": None,
            "timed_out": True,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "failing_tests": [],
            "log_file": str(log_path),
            "build_failed": None,
        }
    except OSError as exc:
        log_path.write_text(str(exc), encoding="utf-8")
        return {
            "passed": False,
            "exit_code": None,
            "timed_out": False,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "failing_tests": [],
            "log_file": str(log_path),
            "build_failed": None,
            "execution_error": f"{type(exc).__name__}: {exc}",
        }


def _create_acor_context(
    task: BugFixTask,
    repository_commit: str,
    index_storage_dir: Path,
    settings: Settings,
) -> tuple[str, int, float]:
    from app.core.repo_index import RepoIndex
    from qdrant_client import QdrantClient

    started = time.perf_counter()
    index_path = index_storage_dir / f"{task.repository_id}.pkl"
    if not index_path.is_file():
        raise FileNotFoundError(f"ACOR index not found: {index_path}")
    with index_path.open("rb") as stream:
        import pickle
        payload = pickle.load(stream)
    indexed_root = Path(payload["repo_root"])
    indexed_commit = _git(indexed_root, "rev-parse", "HEAD")
    if indexed_commit != repository_commit:
        raise ValueError(
            f"ACOR index commit mismatch for {task.task_id}: "
            f"{indexed_commit} != {repository_commit}"
        )
    vector_client = QdrantClient(path=str(settings.qdrant_path))
    try:
        repo_index = RepoIndex.load(
            task.repository_id,
            index_storage_dir,
            settings,
            vector_client=vector_client,
        )
        retrieval_task = BenchmarkTask(
            task_id=task.task_id,
            task_type="bug_localization",
            query=task.description,
            bug_gold_ids=(),
            impact_gold_ids=(),
            repo_id=task.repository_id,
        )
        condition = BenchmarkCondition(
            name="acor",
            weights=settings.acor_weights,
            ranker_strategy=settings.ranker_strategy,
            use_graph_candidate_generation=True,
        )
        context, _retrieved_ids, _candidate_count = _condition_context(
            retrieval_task,
            condition,
            repo_index,
            get_embedding_model(settings),
            settings,
        )
        return context, count_tokens(context), (time.perf_counter() - started) * 1000
    finally:
        vector_client.close()


def _event_failure(events: Sequence[dict[str, Any]], exit_code: int | None, timed_out: bool) -> tuple[str, str | None]:
    requests = [event for event in events if event.get("type") == "model_request"]
    rate_limited = any(
        event.get("status") in {"rate_limited", "429"}
        or event.get("status_code") == 429
        for event in requests
    )
    if timed_out:
        return "agent_timeout", "Agent command exceeded its configured timeout."
    if rate_limited or exit_code == 429:
        return "rate_limited", "Agent telemetry reported a provider rate limit."
    if exit_code != 0:
        return "agent_failed", f"Agent command exited with code {exit_code}."
    return "completed", None


def run_one(
    *,
    experiment: dict[str, Any],
    task: BugFixTask,
    condition: str,
    repetition: int,
    output_dir: Path,
    index_storage_dir: Path,
    settings: Settings,
    pricing: dict[str, Any] | None,
    run_order: int = 1,
) -> dict[str, Any]:
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    run_tag = f"{task.task_id}-r{repetition}-{condition}"
    worktree_root = output_dir / "worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    worktree = worktree_root / run_tag
    if worktree.exists():
        raise FileExistsError(f"Benchmark worktree already exists: {worktree}")
    event_path = output_dir / "events" / f"{run_tag}.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    task_file = output_dir / "tasks" / f"{run_tag}.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = output_dir / "prompts" / f"{run_tag}.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    context_file = output_dir / "contexts" / f"{run_tag}.txt"
    context_file.parent.mkdir(parents=True, exist_ok=True)
    patch_path = output_dir / "patches" / f"{run_tag}.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists() or task_file.exists() or prompt_file.exists() or patch_path.exists():
        raise FileExistsError(f"Benchmark run artifacts already exist for {run_tag}")
    task_doc = {
        "task_id": task.task_id,
        "description": task.description,
        "repository_commit": task.repository_commit,
        "prompt_version": experiment["prompt_version"],
        "test_commands": {
            "regression": list(task.regression_test_command),
            "full": list(task.full_test_command),
        },
    }
    task_file.write_text(json.dumps(task_doc, indent=2), encoding="utf-8")
    acor_context_tokens: int | None = None
    retrieval_ms: float | None = None
    naive_tokens: int | None = None
    agent_exit: int | None = None
    agent_execution_ms: float | None = None
    timed_out = False
    events: list[dict[str, Any]] = []
    failure_reasons: list[str] = []
    regression: dict[str, Any] | None = None
    full_suite: dict[str, Any] | None = None
    worktree_added = False
    try:
        _git(task.repository_path, "worktree", "add", "--detach", str(worktree), task.repository_commit)
        worktree_added = True
        actual_commit = _git(worktree, "rev-parse", "HEAD")
        if actual_commit != task.repository_commit:
            raise RuntimeError("Created worktree does not match the pinned task commit")
        naive_tokens = _tracked_repository_tokens(worktree)
        context = None
        if condition == "acor":
            context, acor_context_tokens, retrieval_ms = _create_acor_context(
                task, task.repository_commit, index_storage_dir, settings
            )
            context_file.write_text(context, encoding="utf-8")

        environment = os.environ.copy()
        environment.update({
            "CODELENS_BENCHMARK_TASK": str(task_file),
            "CODELENS_BENCHMARK_REPOSITORY": str(worktree),
            "CODELENS_BENCHMARK_CONDITION": condition,
            "CODELENS_BENCHMARK_EVENTS": str(event_path),
            "CODELENS_BENCHMARK_PROMPT_CAPTURE_FILE": str(prompt_file),
            "CODELENS_BENCHMARK_PROVIDER": experiment["provider"],
            "CODELENS_BENCHMARK_MODEL": experiment["model"],
            "CODELENS_BENCHMARK_PROMPT_VERSION": experiment["prompt_version"],
            "CODELENS_BENCHMARK_MODEL_CONFIG": json.dumps(experiment["model_config"]),
            "CODELENS_BENCHMARK_TOOL_PERMISSIONS": json.dumps(experiment["tool_permissions"]),
        })
        environment.pop("CODELENS_ACOR_CONTEXT_FILE", None)
        if condition == "acor":
            environment["CODELENS_ACOR_CONTEXT_FILE"] = str(context_file)
        else:
            context_file.unlink(missing_ok=True)
        agent_started = time.perf_counter()
        try:
            result = subprocess.run(
                experiment["agent_command"],
                cwd=worktree,
                env=environment,
                text=True,
                capture_output=True,
                timeout=experiment["timeout_seconds"],
                check=False,
            )
            agent_exit = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            failure_reasons.append("Agent command exceeded its configured timeout.")
        finally:
            agent_execution_ms = (time.perf_counter() - agent_started) * 1000
        try:
            events = _read_events(event_path)
            usage = aggregate_events(events)
        except (OSError, ValueError) as exc:
            usage = aggregate_events([])
            failure_reasons.append(f"Invalid or unavailable agent telemetry: {exc}")
        run_status, agent_failure = _event_failure(events, agent_exit, timed_out)
        if agent_failure:
            failure_reasons.append(agent_failure)
        if usage["model_request_count"] == 0:
            failure_reasons.append("Agent telemetry contains no model requests.")
            if run_status == "completed":
                run_status = "telemetry_missing"
        models = set(usage["provider_models"])
        if models and models != {(experiment["provider"], experiment["model"])}:
            run_status = "configuration_mismatch"
            failure_reasons.append("Reported provider/model does not match experiment manifest.")
        regression = _run_test(
            task.regression_test_command,
            worktree,
            experiment["test_timeout_seconds"],
            output_dir / "test-logs" / f"{run_tag}-regression.log",
        )
        full_suite = _run_test(
            task.full_test_command,
            worktree,
            experiment["test_timeout_seconds"],
            output_dir / "test-logs" / f"{run_tag}-full-suite.log",
        )
        if regression.get("execution_error"):
            failure_reasons.append(
                f"Regression test could not execute: {regression['execution_error']}"
            )
        if full_suite.get("execution_error"):
            failure_reasons.append(
                f"Full test could not execute: {full_suite['execution_error']}"
            )
        if regression["timed_out"]:
            failure_reasons.append("Regression test command timed out.")
        elif not regression["passed"]:
            failure_reasons.append("Regression test command failed.")
        if full_suite["timed_out"]:
            failure_reasons.append("Full test command timed out.")
        elif not full_suite["passed"]:
            failure_reasons.append("Full test command failed.")
        untracked = _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
        if untracked:
            subprocess.run(
                ["git", "-C", str(worktree), "add", "-N", "--", *untracked],
                check=True,
                text=True,
                capture_output=True,
            )
        patch = _git(worktree, "diff", "--binary", task.repository_commit)
        patch_path.write_text(patch, encoding="utf-8")
        if run_status == "completed" and agent_exit == 0 and not patch.strip():
            run_status = "no_patch"
            failure_reasons.append("Agent completed without producing a tracked patch.")
        result_row = {
            "task_id": task.task_id,
            "condition": condition,
            "repetition": repetition,
            "repository_commit": actual_commit,
            "provider": experiment["provider"],
            "model": experiment["model"],
            "agent_version": experiment.get("agent_version"),
            "prompt_version": experiment["prompt_version"],
            "model_config": experiment["model_config"],
            "tool_permissions": experiment["tool_permissions"],
            "run_status": run_status,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "run_order": run_order,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "agent_execution_ms": agent_execution_ms,
            **{field: usage.get(field) for field in (
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_tokens", "total_tokens", "token_measurement_source",
                "model_latency_ms", "model_ttft_ms", "tool_execution_ms",
                "model_request_count", "retry_count", "tool_call_count",
                "estimated_input_tokens",
            )},
            "retrieval_context_tokens": acor_context_tokens,
            "naive_baseline_tokens_estimated": naive_tokens,
            "estimated_input_tokens": usage.get("estimated_input_tokens"),
            "retrieval_latency_ms": retrieval_ms,
            "test_execution_ms": (
                regression["duration_ms"] + full_suite["duration_ms"]
                if regression and full_suite else None
            ),
            "total_wall_time_ms": (time.perf_counter() - run_started) * 1000,
            "cost": calculate_cost(usage, pricing),
            "cost_currency": pricing.get("currency") if pricing else None,
            "agent_exit_code": agent_exit,
            "regression_test_exit_code": regression["exit_code"] if regression else None,
            "full_test_exit_code": full_suite["exit_code"] if full_suite else None,
            "regression_test_timed_out": regression["timed_out"] if regression else None,
            "full_test_timed_out": full_suite["timed_out"] if full_suite else None,
            "regression_test_passed": regression["passed"] if regression else None,
            "full_test_suite_passed": full_suite["passed"] if full_suite else None,
            "patch_correct": regression["passed"] if regression else None,
            "build_failed": (
                regression["build_failed"] is True or full_suite["build_failed"] is True
                if regression and full_suite else None
            ),
            "regression_failing_tests": regression["failing_tests"] if regression else [],
            "full_suite_failing_tests": full_suite["failing_tests"] if full_suite else [],
            "regression_test_log": regression["log_file"] if regression else None,
            "full_test_log": full_suite["log_file"] if full_suite else None,
            "failure_reason": "; ".join(dict.fromkeys(failure_reasons)) or None,
            "patch_file": str(patch_path),
            "event_file": str(event_path),
            "task_file": str(task_file),
            "context_file": str(context_file) if condition == "acor" else None,
            "prompt_file": str(prompt_file),
            "worktree_path": str(worktree),
        }
    except Exception as exc:
        logger.exception("Benchmark run %s failed", run_tag)
        run_status = "infrastructure_error"
        failure_reasons.append(f"{type(exc).__name__}: {exc}")
        usage = aggregate_events(events)
        result_row = {
            "task_id": task.task_id,
            "condition": condition,
            "repetition": repetition,
            "repository_commit": task.repository_commit,
            "provider": experiment["provider"],
            "model": experiment["model"],
            "agent_version": experiment.get("agent_version"),
            "prompt_version": experiment["prompt_version"],
            "model_config": experiment["model_config"],
            "tool_permissions": experiment["tool_permissions"],
            "run_status": run_status,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "run_order": run_order,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "agent_execution_ms": agent_execution_ms,
            **{field: usage.get(field) for field in (
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_tokens", "total_tokens", "token_measurement_source",
                "model_latency_ms", "model_ttft_ms", "tool_execution_ms",
                "model_request_count", "retry_count", "tool_call_count",
                "estimated_input_tokens",
            )},
            "retrieval_context_tokens": acor_context_tokens,
            "naive_baseline_tokens_estimated": naive_tokens,
            "estimated_input_tokens": usage.get("estimated_input_tokens"),
            "retrieval_latency_ms": retrieval_ms,
            "test_execution_ms": None,
            "total_wall_time_ms": (time.perf_counter() - run_started) * 1000,
            "cost": calculate_cost(usage, pricing),
            "cost_currency": pricing.get("currency") if pricing else None,
            "agent_exit_code": agent_exit,
            "regression_test_exit_code": None,
            "full_test_exit_code": None,
            "regression_test_timed_out": None,
            "full_test_timed_out": None,
            "regression_test_passed": None,
            "full_test_suite_passed": None,
            "patch_correct": None,
            "build_failed": None,
            "regression_failing_tests": [],
            "full_suite_failing_tests": [],
            "regression_test_log": None,
            "full_test_log": None,
            "failure_reason": "; ".join(dict.fromkeys(failure_reasons)),
            "patch_file": str(patch_path),
            "event_file": str(event_path),
            "task_file": str(task_file),
            "context_file": str(context_file) if condition == "acor" else None,
            "prompt_file": str(prompt_file),
            "worktree_path": str(worktree),
        }
    finally:
        if worktree_added:
            try:
                _git(task.repository_path, "worktree", "remove", "--force", str(worktree))
            except (subprocess.CalledProcessError, OSError):
                logger.exception("Unable to remove benchmark worktree %s", worktree)
    return result_row


def _average_known(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    values = [row[field] for row in rows if isinstance(row.get(field), (int, float))]
    return mean(values) if values else None


def summarize_results(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Produce condition summaries and paired per-task deltas without significance claims."""
    by_condition = {
        condition: [row for row in rows if row["condition"] == condition]
        for condition in CONDITIONS
    }
    condition_summary: dict[str, Any] = {}
    for condition, condition_rows in by_condition.items():
        condition_summary[condition] = {
            "run_count": len(condition_rows),
            "completed_run_count": sum(row["run_status"] == "completed" for row in condition_rows),
            "mean_input_tokens": _average_known(condition_rows, "input_tokens"),
            "mean_total_tokens": _average_known(condition_rows, "total_tokens"),
            "mean_cost": _average_known(condition_rows, "cost"),
            "mean_total_wall_time_ms": _average_known(condition_rows, "total_wall_time_ms"),
            "mean_model_latency_ms": _average_known(condition_rows, "model_latency_ms"),
            "mean_retrieval_latency_ms": _average_known(condition_rows, "retrieval_latency_ms"),
            "median_model_latency_ms": _median_known(condition_rows, "model_latency_ms"),
            "median_retrieval_latency_ms": _median_known(condition_rows, "retrieval_latency_ms"),
            "median_total_wall_time_ms": (
                median(row["total_wall_time_ms"] for row in condition_rows
                       if row.get("total_wall_time_ms") is not None)
                if any(row.get("total_wall_time_ms") is not None for row in condition_rows)
                else None
            ),
            "task_completion_rate": (
                sum(row["run_status"] == "completed" for row in condition_rows) / len(condition_rows)
                if condition_rows else None
            ),
            "regression_test_pass_rate": _known_rate(condition_rows, "regression_test_passed"),
            "full_test_suite_pass_rate": _known_rate(condition_rows, "full_test_suite_passed"),
            "patch_correctness_rate": _known_rate(condition_rows, "patch_correct"),
            "build_failure_rate": _known_rate(condition_rows, "build_failed"),
            "failures": [
                {"task_id": row["task_id"], "repetition": row.get("repetition"),
                 "status": row["run_status"], "reason": row.get("failure_reason")}
                for row in condition_rows
                if row["run_status"] != "completed" or row.get("failure_reason")
            ],
        }
    pairs: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        pairs.setdefault((row["task_id"], row.get("repetition", 1)), {})[
            row["condition"]
        ] = row
    paired: list[dict[str, Any]] = []
    for (task_id, repetition), conditions in sorted(pairs.items()):
        baseline, acor = conditions.get("baseline"), conditions.get("acor")
        record: dict[str, Any] = {"task_id": task_id, "repetition": repetition}
        for metric in ("input_tokens", "total_tokens", "cost", "total_wall_time_ms"):
            before = baseline.get(metric) if baseline else None
            after = acor.get(metric) if acor else None
            record[f"{metric}_baseline"] = before
            record[f"{metric}_acor"] = after
            record[f"{metric}_absolute_reduction"] = (
                before - after if before is not None and after is not None else None
            )
            record[f"{metric}_reduction_percent"] = (
                (before - after) / before * 100
                if before is not None and after is not None and before != 0
                else None
            )
        paired.append(record)
    metrics = ("input_tokens", "total_tokens", "cost", "total_wall_time_ms")
    deltas = {
        metric: [
            pair[f"{metric}_absolute_reduction"]
            for pair in paired if pair.get(f"{metric}_absolute_reduction") is not None
        ]
        for metric in metrics
    }
    by_task: dict[str, dict[str, Any]] = {}
    for task_id in sorted({pair["task_id"] for pair in paired}):
        task_pairs = [pair for pair in paired if pair["task_id"] == task_id]
        task_result: dict[str, Any] = {"task_id": task_id, "repetitions": len(task_pairs)}
        for metric in metrics:
            values = [
                pair[f"{metric}_absolute_reduction"]
                for pair in task_pairs
                if pair.get(f"{metric}_absolute_reduction") is not None
            ]
            task_result[f"{metric}_mean_paired_reduction"] = mean(values) if values else None
            task_result[f"{metric}_sample_stdev_paired_reduction"] = (
                stdev(values) if len(values) > 1 else None
            )
        by_task[task_id] = task_result
    return {
        "conditions": condition_summary,
        "paired_comparisons": paired,
        "per_task_variability": by_task,
        "mean_paired_absolute_reduction": {
            metric: mean(values) if values else None for metric, values in deltas.items()
        },
        "mean_paired_percent_reduction": {
            metric: _average_known(paired, f"{metric}_reduction_percent")
            for metric in metrics
        },
        "interpretation": "Descriptive pilot statistics only; no significance claim.",
    }


def _known_rate(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    known = [row[field] for row in rows if row.get(field) is not None]
    return sum(value is True for value in known) / len(known) if known else None


def _median_known(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    values = [row[field] for row in rows if isinstance(row.get(field), (int, float))]
    return median(values) if values else None


def write_results(
    output_dir: Path,
    experiment: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    pricing: dict[str, Any] | None,
    *,
    seed: int,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "results.json"
    csv_path = output_dir / "results.csv"
    report = {
        "schema_version": 1,
        "experiment_id": experiment["experiment_id"],
        "provider": experiment["provider"],
        "model": experiment["model"],
        "prompt_version": experiment["prompt_version"],
        "model_config": experiment["model_config"],
        "tool_permissions": experiment["tool_permissions"],
        "pricing": pricing,
        "pricing_note": (
            "Cost is null where provider-reported usage or applicable rates are unavailable."
        ),
        "seed": seed,
        "run_count": len(rows),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "results": list(rows),
        "summary": summarize_results(rows),
        "limitations": [
            "Model and tool telemetry is attested by the external adapter.",
            "Tokenizer counts and naive full-repository token counts are estimates.",
            "No statistical significance is inferred from a small pilot.",
        ],
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csv_rows = []
    for row in rows:
        csv_rows.append({
            field: (
                json.dumps(row[field])
                if isinstance(row.get(field), (list, dict))
                else "null"
                if row.get(field) is None
                else row.get(field)
            )
            for field in RESULT_FIELDS
        })
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)
    return json_path, csv_path


def run_experiment(
    experiment: dict[str, Any],
    *,
    output_dir: Path,
    index_storage_dir: Path,
    settings: Settings,
    pricing: dict[str, Any] | None,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for repetition in range(1, experiment["repetitions"] + 1):
        for task in experiment["tasks"]:
            order = list(CONDITIONS)
            rng.shuffle(order)
            for condition in order:
                run_order = len(rows) + 1
                rows.append(run_one(
                    experiment=experiment,
                    task=task,
                    condition=condition,
                    repetition=repetition,
                    output_dir=output_dir,
                    index_storage_dir=index_storage_dir,
                    settings=settings,
                    pricing=pricing,
                    run_order=run_order,
                ))
    return rows


def load_pricing(path: Path | None, provider: str, model: str) -> dict[str, Any] | None:
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    pricing = raw.get("models", {}).get(f"{provider}/{model}")
    if pricing is None:
        return None
    for key in ("source", "as_of", "currency"):
        _require_string(pricing.get(key), f"pricing.{key}")
    return pricing
