"""Smoke-oriented ACP adapter for GitHub Copilot CLI.

Copilot CLI does not expose the request-level provider telemetry required by
the bug-fix benchmark, so this adapter records observable tool calls only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO


PERMISSION_TO_COPILOT_TOOL = {
    "list_files": "glob",
    "read_file": "view",
    "search_text": "grep",
    "apply_patch": "edit",
}
_TERMINAL_TOOL_STATUSES = {"completed", "failed", "cancelled"}


def _read_json_env(name: str, expected_type: type) -> Any:
    try:
        value = json.loads(os.environ[name])
    except KeyError as exc:
        raise ValueError(f"Missing required environment variable: {name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain valid JSON") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} must contain a JSON {expected_type.__name__}")
    return value


def _build_prompt(task: dict[str, Any], context_path: str | None) -> str:
    task_id = task.get("task_id")
    description = task.get("description")
    test_commands = task.get("test_commands")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Task file must contain a non-empty task_id")
    if not isinstance(description, str) or not description:
        raise ValueError("Task file must contain a non-empty description")
    if not isinstance(test_commands, dict):
        raise ValueError("Task file must contain test_commands")

    prompt = (
        f"Fix bug-fix benchmark task {task_id}.\n\n"
        f"Task:\n{description}\n\n"
        "The benchmark harness will run these tests after you finish; do not "
        "change them:\n"
        f"{json.dumps(test_commands, indent=2)}\n"
    )
    if context_path is not None:
        context = Path(context_path).read_text(encoding="utf-8")
        prompt += (
            "\nAdditional CodeLens retrieved context (ACOR):\n"
            f"{context}\n"
        )
    return prompt


def _cli_command(model: str, permissions: list[str]) -> list[str]:
    unsupported = sorted(set(permissions) - PERMISSION_TO_COPILOT_TOOL.keys())
    if unsupported:
        raise ValueError(
            "Unsupported benchmark tool permission(s) for this adapter: "
            + ", ".join(unsupported)
        )
    if not model or model.casefold() == "auto":
        raise ValueError(
            "The smoke adapter requires an explicitly selected Copilot CLI model; "
            "Auto selection cannot be attested as a fixed model."
        )
    model_config = _read_json_env("CODELENS_BENCHMARK_MODEL_CONFIG", dict)
    if model_config:
        raise ValueError(
            "Copilot CLI adapter does not support the generic model_config fields; "
            "use an empty object rather than silently ignoring settings."
        )

    available_tools = sorted({
        PERMISSION_TO_COPILOT_TOOL[permission]
        for permission in permissions
    })
    command = [
        os.environ.get("COPILOT_CLI_PATH", "copilot"),
        "--acp",
        "--stdio",
        f"--model={model}",
        f"--available-tools={','.join(available_tools)}",
    ]
    credit_limit = os.environ.get("CODELENS_BENCHMARK_MAX_AI_CREDITS")
    if credit_limit is not None:
        try:
            parsed_limit = int(credit_limit)
        except ValueError as exc:
            raise ValueError("CODELENS_BENCHMARK_MAX_AI_CREDITS must be an integer") from exc
        if parsed_limit < 30:
            raise ValueError("Copilot CLI requires at least 30 AI credits per session")
        command.extend(["--max-ai-credits", str(parsed_limit)])
    return command


def _write_event(stream: TextIO, event: dict[str, Any]) -> None:
    stream.write(json.dumps(event, separators=(",", ":")) + "\n")
    stream.flush()


def _write_tool_event(
    events: TextIO,
    pending: dict[str, tuple[str, float | None]],
    tool_call: dict[str, Any],
) -> None:
    status = tool_call.get("status")
    if status not in _TERMINAL_TOOL_STATUSES:
        return
    call_id = tool_call.get("toolCallId")
    title = tool_call.get("title")
    if not isinstance(call_id, str) or not call_id:
        raise RuntimeError("Copilot CLI ACP tool event omitted toolCallId")
    if not isinstance(title, str) or not title:
        prior = pending.get(call_id)
        title = prior[0] if prior else None
    if not title:
        raise RuntimeError("Copilot CLI ACP tool event omitted its title")
    started = pending.pop(call_id, (title, None))[1]
    duration_ms = (time.perf_counter() - started) * 1000 if started is not None else None
    _write_event(events, {
        "type": "tool_call",
        "tool": title,
        "status": "success" if status == "completed" else status,
        "duration_ms": duration_ms,
    })


def _handle_message(message: dict[str, Any], events: TextIO,
                    pending: dict[str, tuple[str, float | None]],
                    response_stream: TextIO) -> None:
    method = message.get("method")
    if method == "session/update":
        params = message.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict):
            return
        update_type = update.get("sessionUpdate")
        if update_type == "tool_call":
            call_id = update.get("toolCallId")
            title = update.get("title")
            if isinstance(call_id, str) and isinstance(title, str) and title:
                pending[call_id] = (title, time.perf_counter())
            _write_tool_event(events, pending, update)
        elif update_type == "tool_call_update":
            _write_tool_event(events, pending, update)
        return
    if method == "session/request_permission":
        request_id = message.get("id")
        params = message.get("params")
        options = params.get("options", []) if isinstance(params, dict) else []
        allow_once = next((
            option for option in options
            if isinstance(option, dict)
            and option.get("kind") in {"allow_once", "allow_always"}
        ), None)
        outcome: dict[str, str] = {"outcome": "cancelled"}
        if allow_once and isinstance(allow_once.get("optionId"), str):
            outcome = {
                "outcome": "selected",
                "optionId": allow_once["optionId"],
            }
        if request_id is not None:
            _send_message(response_stream, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"outcome": outcome},
            })


def _send_message(stream: TextIO, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def _wait_for_response(
    process: subprocess.Popen[str],
    request_id: int,
    events: TextIO,
    pending: dict[str, tuple[str, float | None]],
) -> dict[str, Any]:
    assert process.stdout is not None
    assert process.stdin is not None
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Copilot CLI ACP process exited before replying")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Copilot CLI ACP emitted invalid JSON") from exc
        if not isinstance(message, dict):
            raise RuntimeError("Copilot CLI ACP message must be a JSON object")
        _handle_message(message, events, pending, process.stdin)
        if message.get("id") == request_id and "result" in message:
            return message["result"]
        if message.get("id") == request_id and "error" in message:
            raise RuntimeError(f"Copilot CLI ACP request failed: {message['error']}")


def _run_acp(command: list[str], cwd: Path, prompt: str, event_path: Path) -> int:
    event_path.parent.mkdir(parents=True, exist_ok=True)
    pending: dict[str, tuple[str, float | None]] = {}
    with event_path.open("a", encoding="utf-8") as events:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        try:
            initialize_id = 1
            _send_message(process.stdin, {
                "jsonrpc": "2.0",
                "id": initialize_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": "codelens-benchmark-smoke-adapter",
                        "version": "1.0",
                    },
                },
            })
            _wait_for_response(process, initialize_id, events, pending)

            session_id = 2
            _send_message(process.stdin, {
                "jsonrpc": "2.0",
                "id": session_id,
                "method": "session/new",
                "params": {"cwd": str(cwd), "mcpServers": []},
            })
            session = _wait_for_response(process, session_id, events, pending)
            actual_session_id = session.get("sessionId")
            if not isinstance(actual_session_id, str) or not actual_session_id:
                raise RuntimeError("Copilot CLI ACP returned no sessionId")

            prompt_id = 3
            _send_message(process.stdin, {
                "jsonrpc": "2.0",
                "id": prompt_id,
                "method": "session/prompt",
                "params": {
                    "sessionId": actual_session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
            })
            prompt_response = _wait_for_response(process, prompt_id, events, pending)
            if prompt_response.get("stopReason") != "end_turn":
                raise RuntimeError(
                    "Copilot CLI ACP did not complete the task normally: "
                    f"{prompt_response.get('stopReason')!r}"
                )
            process.stdin.close()
            return process.wait()
        finally:
            for call_id, (title, started) in pending.items():
                duration_ms = (
                    (time.perf_counter() - started) * 1000
                    if started is not None
                    else None
                )
                _write_event(events, {
                    "type": "tool_call",
                    "tool": title,
                    "status": "incomplete",
                    "duration_ms": duration_ms,
                })
            pending.clear()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> int:
    try:
        task_path = Path(os.environ["CODELENS_BENCHMARK_TASK"])
        repository = Path(os.environ["CODELENS_BENCHMARK_REPOSITORY"]).resolve()
        event_path = Path(os.environ["CODELENS_BENCHMARK_EVENTS"])
        condition = os.environ["CODELENS_BENCHMARK_CONDITION"]
        model = os.environ["CODELENS_BENCHMARK_MODEL"]
        permissions = _read_json_env("CODELENS_BENCHMARK_TOOL_PERMISSIONS", list)
        if not permissions:
            raise ValueError("Benchmark tool permissions must not be empty")
        if any(not isinstance(item, str) or not item for item in permissions):
            raise ValueError("Benchmark tool permissions must be non-empty strings")
        if condition not in {"baseline", "acor"}:
            raise ValueError(f"Unsupported benchmark condition: {condition}")
        context_path = os.environ.get("CODELENS_ACOR_CONTEXT_FILE")
        if condition == "baseline" and context_path is not None:
            raise ValueError(
                "Baseline must not receive CODELENS_ACOR_CONTEXT_FILE"
            )
        if not task_path.is_file():
            raise FileNotFoundError(f"Benchmark task file not found: {task_path}")
        if not repository.is_dir():
            raise FileNotFoundError(f"Benchmark repository not found: {repository}")
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if not isinstance(task, dict):
            raise ValueError("Benchmark task file must contain a JSON object")
        prompt = _build_prompt(task, context_path if condition == "acor" else None)
        prompt_capture_path = os.environ.get("CODELENS_BENCHMARK_PROMPT_CAPTURE_FILE")
        if prompt_capture_path:
            capture_file = Path(prompt_capture_path)
            capture_file.parent.mkdir(parents=True, exist_ok=True)
            capture_file.write_text(prompt, encoding="utf-8")
        command = _cli_command(model, permissions)
        return _run_acp(command, repository, prompt, event_path)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"Copilot CLI benchmark adapter: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
