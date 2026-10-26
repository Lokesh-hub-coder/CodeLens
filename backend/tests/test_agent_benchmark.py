from __future__ import annotations

import csv
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.evaluation.agent_benchmark import (
    aggregate_events,
    calculate_cost,
    load_experiment,
    run_one,
    write_results,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _git_repository(path: Path) -> str:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "benchmark@example.invalid")
    _git(path, "config", "user.name", "Benchmark Test")
    (path / "change.txt").write_text("buggy\n", encoding="utf-8")
    _git(path, "add", "change.txt")
    _git(path, "commit", "-m", "fixture")
    return _git(path, "rev-parse", "HEAD")


def _task(path: Path, commit: str, task_id: str = "bug-1") -> dict:
    from app.evaluation.agent_benchmark import BugFixTask

    return BugFixTask(
        task_id=task_id,
        repository_path=path,
        repository_commit=commit,
        description="Repair the fixture bug.",
        repository_id="fixture",
        regression_test_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; raise SystemExit(0 if Path('change.txt').read_text() == 'fixed\\n' else 1)",
        ),
        full_test_command=(sys.executable, "-c", "raise SystemExit(0)"),
    )


def _agent_script(path: Path, *, rate_limited: bool = False) -> list[str]:
    path.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "condition = os.environ['CODELENS_BENCHMARK_CONDITION']\n"
        "context = os.environ.get('CODELENS_ACOR_CONTEXT_FILE')\n"
        "if condition == 'baseline' and context is not None: raise SystemExit(11)\n"
        "if condition == 'acor' and (not context or Path(context).read_text() != 'retrieved context'): raise SystemExit(12)\n"
        + ("" if rate_limited else "Path('change.txt').write_text('fixed\\n')\n")
        + "events = Path(os.environ['CODELENS_BENCHMARK_EVENTS'])\n"
        + "events.parent.mkdir(parents=True, exist_ok=True)\n"
        + "with events.open('a', encoding='utf-8') as stream:\n"
        + " stream.write(json.dumps({'type':'model_request','provider':'test-provider','model':'test-model','status':'rate_limited' if "
        + repr(rate_limited)
        + " else 'success','token_measurement_source':'provider_reported','input_tokens':100,'cached_input_tokens':10,'output_tokens':20,'reasoning_tokens':5,'total_tokens':120,'generation_ms':15,'time_to_first_token_ms':4,'attempt':1})+'\\n')\n"
        + " stream.write(json.dumps({'type':'tool_call','tool':'read_file','status':'success','duration_ms':2})+'\\n')\n"
        + ("raise SystemExit(3)\n" if rate_limited else ""),
        encoding="utf-8",
    )
    return [sys.executable, str(path)]


def _experiment(command: list[str]) -> dict:
    return {
        "experiment_id": "test",
        "provider": "test-provider",
        "model": "test-model",
        "prompt_version": "fixed-v1",
        "agent_command": command,
        "tool_permissions": ["list_files", "read_file", "search_text", "apply_patch"],
        "model_config": {"temperature": 0, "max_output_tokens": 2048},
        "timeout_seconds": 15,
        "test_timeout_seconds": 10,
        "token_budget": 200,
    }


def test_usage_aggregates_multiple_model_requests_and_tool_calls():
    events = [
        {
            "type": "model_request",
            "provider": "p",
            "model": "m",
            "status": "success",
            "token_measurement_source": "provider_reported",
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 3,
            "reasoning_tokens": 1,
            "total_tokens": 13,
            "generation_ms": 20,
            "time_to_first_token_ms": 4,
            "attempt": 1,
        },
        {
            "type": "model_request",
            "provider": "p",
            "model": "m",
            "status": "success",
            "token_measurement_source": "provider_reported",
            "input_tokens": 15,
            "cached_input_tokens": 1,
            "output_tokens": 4,
            "reasoning_tokens": 2,
            "total_tokens": 19,
            "generation_ms": 30,
            "time_to_first_token_ms": 8,
            "attempt": 2,
        },
        {"type": "tool_call", "tool": "read_file", "status": "success", "duration_ms": 5},
    ]

    usage = aggregate_events(events)

    assert usage["input_tokens"] == 25
    assert usage["cached_input_tokens"] == 3
    assert usage["output_tokens"] == 7
    assert usage["reasoning_tokens"] == 3
    assert usage["total_tokens"] == 32
    assert usage["model_latency_ms"] == 50
    assert usage["model_ttft_ms"] == 6
    assert usage["tool_execution_ms"] == 5
    assert usage["model_request_count"] == 2
    assert usage["retry_count"] == 1
    assert usage["tool_call_count"] == 1
    assert usage["token_measurement_source"] == "provider_reported"


def test_missing_provider_usage_stays_null_and_estimates_are_separate():
    usage = aggregate_events([{
        "type": "model_request",
        "provider": "p",
        "model": "m",
        "status": "success",
        "token_measurement_source": "unavailable",
        "estimated_input_tokens": 71,
    }])

    assert usage["input_tokens"] is None
    assert usage["total_tokens"] is None
    assert usage["estimated_input_tokens"] == 71
    assert usage["token_measurement_source"] == "unavailable"


def test_partial_provider_usage_does_not_silently_sum_incomplete_metrics():
    events = [
        {
            "type": "model_request",
            "provider": "p",
            "model": "m",
            "status": "success",
            "token_measurement_source": "provider_reported",
            "input_tokens": 20,
        },
        {
            "type": "model_request",
            "provider": "p",
            "model": "m",
            "status": "success",
            "token_measurement_source": "unavailable",
        },
    ]

    usage = aggregate_events(events)

    assert usage["input_tokens"] is None
    assert usage["token_measurement_source"] == "mixed_or_partial"


def test_cost_uses_supplied_rates_and_reports_missing_data():
    usage = {
        "input_tokens": 1000,
        "cached_input_tokens": 200,
        "output_tokens": 500,
        "reasoning_tokens": 100,
    }
    prices = {
        "input_per_million": 2,
        "cached_input_per_million": 0.5,
        "output_per_million": 8,
        "reasoning_per_million": 12,
    }

    assert calculate_cost(usage, prices) == pytest.approx(0.0061)
    assert calculate_cost({**usage, "reasoning_tokens": None}, prices) is None
    assert calculate_cost({**usage, "cached_input_tokens": None}, prices) is None
    assert calculate_cost(usage, None) is None


def test_rejects_invalid_usage_and_inconsistent_cost_inputs():
    with pytest.raises(ValueError, match="non-negative integer"):
        aggregate_events([{
            "type": "model_request",
        "provider": "p",
        "model": "m",
        "status": "success",
        "token_measurement_source": "provider_reported",
            "input_tokens": -1,
        }])
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_cost(
            {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1},
            {"input_per_million": 1, "output_per_million": 1},
        )


def test_runs_isolated_baseline_and_acor_worktrees_with_same_agent(tmp_path, monkeypatch):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    agent_file = tmp_path / "agent.py"
    command = _agent_script(agent_file)
    experiment = _experiment(command)
    task = _task(repository, commit)
    output_dir = tmp_path / "runs"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    monkeypatch.setattr(
        "app.evaluation.agent_benchmark._create_acor_context",
        lambda *args: ("retrieved context", 17, 3.5),
    )

    baseline = run_one(
        experiment=experiment,
        task=task,
        condition="baseline",
        repetition=1,
        output_dir=output_dir,
        index_storage_dir=index_dir,
        settings=Settings(default_token_budget=200),
        pricing=None,
    )
    acor = run_one(
        experiment=experiment,
        task=task,
        condition="acor",
        repetition=1,
        output_dir=output_dir,
        index_storage_dir=index_dir,
        settings=Settings(default_token_budget=200),
        pricing=None,
    )

    assert baseline["run_status"] == acor["run_status"] == "completed"
    assert baseline["input_tokens"] == acor["input_tokens"] == 100
    assert baseline["regression_test_passed"] is acor["regression_test_passed"] is True
    assert baseline["full_test_suite_passed"] is acor["full_test_suite_passed"] is True
    assert baseline["patch_correct"] is acor["patch_correct"] is True
    assert baseline["retrieval_context_tokens"] is None
    assert acor["retrieval_context_tokens"] == 17
    assert acor["retrieval_latency_ms"] == 3.5
    assert not Path(baseline["worktree_path"]).exists()
    assert not Path(acor["worktree_path"]).exists()
    assert (repository / "change.txt").read_text(encoding="utf-8") == "buggy\n"
    worktrees = _git(repository, "worktree", "list", "--porcelain")
    assert worktrees.count("worktree ") == 1
    assert Path(baseline["patch_file"]).read_text(encoding="utf-8")
    assert Path(acor["patch_file"]).read_text(encoding="utf-8")


def test_rate_limited_and_failed_run_is_kept_and_tests_are_recorded(tmp_path):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    command = _agent_script(tmp_path / "limited.py", rate_limited=True)
    task = _task(repository, commit)

    row = run_one(
        experiment=_experiment(command),
        task=task,
        condition="baseline",
        repetition=1,
        output_dir=tmp_path / "runs",
        index_storage_dir=tmp_path / "index",
        settings=Settings(),
        pricing=None,
    )

    assert row["run_status"] == "rate_limited"
    assert row["agent_exit_code"] == 3
    assert row["regression_test_passed"] is False
    assert row["full_test_suite_passed"] is True
    assert row["patch_correct"] is False
    assert row["input_tokens"] == 100
    assert "rate limit" in row["failure_reason"]


def test_agent_failure_without_usage_remains_in_report(tmp_path):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    failed_agent = tmp_path / "failed.py"
    failed_agent.write_text("raise SystemExit(9)\n", encoding="utf-8")
    task = _task(repository, commit)

    row = run_one(
        experiment=_experiment([sys.executable, str(failed_agent)]),
        task=task,
        condition="baseline",
        repetition=1,
        output_dir=tmp_path / "runs",
        index_storage_dir=tmp_path / "index",
        settings=Settings(),
        pricing=None,
    )

    assert row["run_status"] == "agent_failed"
    assert row["input_tokens"] is None
    assert row["total_tokens"] is None
    assert row["token_measurement_source"] == "unavailable"
    assert row["model_request_count"] == 0
    assert row["regression_test_passed"] is False


def test_agent_timeout_is_separated_from_test_results(tmp_path):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    slow_agent = tmp_path / "slow.py"
    slow_agent.write_text("import time; time.sleep(3)\n", encoding="utf-8")
    experiment = _experiment([sys.executable, str(slow_agent)])
    experiment["timeout_seconds"] = 1

    row = run_one(
        experiment=experiment,
        task=_task(repository, commit),
        condition="baseline",
        repetition=1,
        output_dir=tmp_path / "runs",
        index_storage_dir=tmp_path / "index",
        settings=Settings(),
        pricing=None,
    )

    assert row["run_status"] == "agent_timeout"
    assert row["agent_exit_code"] is None
    assert row["regression_test_timed_out"] is False
    assert row["regression_test_passed"] is False
    assert "Agent command exceeded" in row["failure_reason"]


def test_acor_context_uses_existing_index_and_retrieval_pipeline(tmp_path, monkeypatch):
    from app.evaluation import agent_benchmark

    repository = tmp_path / "source"
    commit = _git_repository(repository)
    task = _task(repository, commit)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    with (index_dir / "fixture.pkl").open("wb") as stream:
        pickle.dump({"repo_root": repository}, stream)
    captured = {}

    class FakeVectorClient:
        def __init__(self, *, path):
            captured["qdrant_path"] = path

        def close(self):
            captured["closed"] = True

    class FakeIndex:
        @classmethod
        def load(cls, repo_id, storage_dir, settings, vector_client):
            captured["repo_id"] = repo_id
            captured["vector_client"] = vector_client
            return object()

    def fake_context(retrieval_task, condition, repo_index, embedding_model, settings):
        captured["query"] = retrieval_task.query
        captured["condition"] = condition
        return "retrieved context", ["gold"], 1

    monkeypatch.setattr("qdrant_client.QdrantClient", FakeVectorClient)
    monkeypatch.setattr("app.core.repo_index.RepoIndex", FakeIndex)
    monkeypatch.setattr(agent_benchmark, "get_embedding_model", lambda settings: object())
    monkeypatch.setattr(agent_benchmark, "_condition_context", fake_context)

    context, tokens, latency = agent_benchmark._create_acor_context(
        task, commit, index_dir, Settings(default_token_budget=200)
    )

    assert context == "retrieved context"
    assert tokens > 0
    assert latency >= 0
    assert captured["query"] == task.description
    assert captured["condition"].name == "acor"
    assert captured["condition"].use_graph_candidate_generation is True
    assert captured["repo_id"] == "fixture"
    assert captured["closed"] is True


def test_manifest_validation_and_json_csv_output(tmp_path):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    experiment_data = {
        "experiment_id": "schema-test",
        "provider": "test-provider",
        "model": "test-model",
        "prompt_version": "v1",
        "agent_command": ["agent-adapter"],
        "tool_permissions": ["read_file"],
        "model_config": {"temperature": 0},
        "tool_permissions": ["list_files", "read_file", "search_text", "apply_patch"],
        "model_config": {"temperature": 0},
        "tasks": [{
            "task_id": "one",
            "repository_path": str(repository),
            "repository_commit": commit,
            "repository_id": "fixture",
            "description": "Repair the fixture bug.",
            "regression_test_command": ["pytest", "test_target.py"],
            "full_test_command": ["pytest"],
        }],
    }
    manifest = tmp_path / "experiment.json"
    manifest.write_text(json.dumps(experiment_data), encoding="utf-8")
    experiment = load_experiment(manifest)
    row = {
        "task_id": "one",
        "condition": "baseline",
        "repetition": 1,
        "repository_commit": commit,
        "provider": "test-provider",
        "model": "test-model",
        "run_status": "agent_failed",
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "token_measurement_source": "unavailable",
        "retrieval_context_tokens": None,
        "naive_baseline_tokens_estimated": 10,
        "estimated_input_tokens": 9,
        "retrieval_latency_ms": None,
        "model_latency_ms": None,
        "model_ttft_ms": None,
        "tool_execution_ms": None,
        "test_execution_ms": 1,
        "total_wall_time_ms": 2,
        "cost": None,
        "cost_currency": None,
        "model_request_count": 0,
        "retry_count": 0,
        "tool_call_count": 0,
        "agent_exit_code": 2,
        "regression_test_exit_code": 1,
        "full_test_exit_code": 1,
        "regression_test_timed_out": False,
        "full_test_timed_out": False,
        "regression_test_passed": False,
        "full_test_suite_passed": False,
        "patch_correct": False,
        "build_failed": False,
        "regression_failing_tests": [],
        "full_suite_failing_tests": [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:00:01+00:00",
        "run_order": 1,
        "repetition": 1,
        "environment": {"platform": "fixture", "python": "3"},
        "model_config": {"temperature": 0},
        "tool_permissions": ["read_file"],
        "failure_reason": "agent failed",
    }

    json_path, csv_path = write_results(
        tmp_path / "output", experiment, [row], None, seed=3
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        csv_row = next(reader)
        csv_fields = reader.fieldnames
    result_schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "benchmarks"
            / "bugfix-results.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert report["run_count"] == 1
    assert report["results"][0]["input_tokens"] is None
    assert report["summary"]["conditions"]["baseline"]["run_count"] == 1
    assert set(result_schema["required"]).issubset(report)
    assert set(result_schema["properties"]["results"]["items"]["required"]).issubset(
        report["results"][0]
    )
    assert "input_tokens" in csv_fields
    assert csv_row["input_tokens"] == "null"
    assert csv_row["estimated_input_tokens"] == "9"


def test_runner_task_payload_is_condition_neutral_and_bad_events_fail(tmp_path, monkeypatch):
    repository = tmp_path / "source"
    commit = _git_repository(repository)
    task = _task(repository, commit)
    command_file = tmp_path / "noop.py"
    command_file.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['CODELENS_BENCHMARK_EVENTS']).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(os.environ['CODELENS_BENCHMARK_EVENTS']).write_text('{bad json\\n')\n",
        encoding="utf-8",
    )

    row = run_one(
        experiment=_experiment([sys.executable, str(command_file)]),
        task=task,
        condition="baseline",
        repetition=1,
        output_dir=tmp_path / "runs",
        index_storage_dir=tmp_path / "index",
        settings=Settings(),
        pricing=None,
    )
    task_doc = json.loads(Path(row["task_file"]).read_text(encoding="utf-8"))

    assert row["token_measurement_source"] == "unavailable"
    assert "Invalid or unavailable agent telemetry" in row["failure_reason"]
    assert "condition" not in task_doc
