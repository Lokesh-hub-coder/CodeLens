from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.evaluation import copilot_cli_adapter as adapter
from app.evaluation.agent_benchmark import aggregate_events


class _CapturedStdin(io.StringIO):
    def close(self):
        self.flush()


class _FakeProcess:
    def __init__(self, stdout: str):
        self.stdin = _CapturedStdin()
        self.stdout = io.StringIO(stdout)
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


def _acp_output() -> str:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "session-1"}},
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "session-1",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tool-1",
                    "title": "view",
                    "status": "in_progress",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "session-1",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "status": "completed",
                },
            },
        },
        {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}},
    ]
    return "".join(json.dumps(message) + "\n" for message in messages)


@pytest.mark.parametrize("condition", ["baseline", "acor"])
def test_cli_adapter_uses_same_acp_command_and_logs_only_observable_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    condition: str,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "task_id": "bug-1",
        "description": "Fix the reported issue.",
        "test_commands": {"regression": ["pytest", "test_bug.py"]},
    }), encoding="utf-8")
    event_path = tmp_path / "events.jsonl"
    prompt_capture_path = tmp_path / "prompt.txt"
    context_path = tmp_path / "context.txt"
    context_path.write_text("retrieved-only context", encoding="utf-8")
    monkeypatch.setenv("CODELENS_BENCHMARK_TASK", str(task_path))
    monkeypatch.setenv("CODELENS_BENCHMARK_REPOSITORY", str(repository))
    monkeypatch.setenv("CODELENS_BENCHMARK_CONDITION", condition)
    monkeypatch.setenv("CODELENS_BENCHMARK_EVENTS", str(event_path))
    monkeypatch.setenv("CODELENS_BENCHMARK_PROMPT_CAPTURE_FILE", str(prompt_capture_path))
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL", "fixed-model")
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL_CONFIG", "{}")
    monkeypatch.setenv(
        "CODELENS_BENCHMARK_TOOL_PERMISSIONS",
        json.dumps(["list_files", "read_file", "search_text", "apply_patch"]),
    )
    if condition == "acor":
        monkeypatch.setenv("CODELENS_ACOR_CONTEXT_FILE", str(context_path))
    else:
        monkeypatch.delenv("CODELENS_ACOR_CONTEXT_FILE", raising=False)

    process = _FakeProcess(_acp_output())
    launched: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["cwd"] = kwargs["cwd"]
        launched["process"] = process
        return process

    monkeypatch.setattr(adapter.subprocess, "Popen", fake_popen)

    assert adapter.main() == 0
    command = launched["command"]
    assert command == [
        "copilot",
        "--acp",
        "--stdio",
        "--model=fixed-model",
        "--available-tools=edit,glob,grep,view",
    ]
    assert launched["cwd"] == repository.resolve()
    sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    prompt_message = next(message for message in sent if message.get("id") == 3)
    prompt = prompt_message["params"]["prompt"][0]["text"]
    assert "Fix the reported issue." in prompt
    assert ("retrieved-only context" in prompt) is (condition == "acor")
    assert prompt_capture_path.read_text(encoding="utf-8") == prompt

    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["type"] == "tool_call"
    assert events[0]["tool"] == "view"
    assert events[0]["status"] == "success"
    assert isinstance(events[0]["duration_ms"], (int, float))
    assert events[0]["duration_ms"] >= 0
    assert aggregate_events(events)["tool_call_count"] == 1
    assert not any(event["type"] == "model_request" for event in events)


def test_baseline_context_is_rejected_without_reading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    task_path = tmp_path / "task.json"
    task_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODELENS_BENCHMARK_TASK", str(task_path))
    monkeypatch.setenv("CODELENS_BENCHMARK_REPOSITORY", str(repository))
    monkeypatch.setenv("CODELENS_BENCHMARK_CONDITION", "baseline")
    monkeypatch.setenv("CODELENS_BENCHMARK_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL", "fixed-model")
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL_CONFIG", "{}")
    monkeypatch.setenv("CODELENS_BENCHMARK_TOOL_PERMISSIONS", '["read_file"]')
    monkeypatch.setenv("CODELENS_ACOR_CONTEXT_FILE", str(tmp_path / "must-not-read"))

    def unexpected_launch(*args, **kwargs):
        raise AssertionError("Copilot CLI must not be launched for a baseline context leak")

    monkeypatch.setattr(adapter.subprocess, "Popen", unexpected_launch)
    assert adapter.main() == 1


def test_auto_model_selection_is_rejected_for_smoke_adapter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL_CONFIG", "{}")
    with pytest.raises(ValueError, match="explicitly selected"):
        adapter._cli_command("auto", ["read_file"])


@pytest.mark.parametrize(("value", "valid"), [("30", True), ("29", False), ("invalid", False)])
def test_cli_credit_limit_is_explicit_and_validated(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    valid: bool,
):
    monkeypatch.setenv("CODELENS_BENCHMARK_MODEL_CONFIG", "{}")
    monkeypatch.setenv("CODELENS_BENCHMARK_MAX_AI_CREDITS", value)

    if valid:
        command = adapter._cli_command("fixed-model", ["read_file"])
        assert command[-2:] == ["--max-ai-credits", value]
    else:
        with pytest.raises(ValueError):
            adapter._cli_command("fixed-model", ["read_file"])
