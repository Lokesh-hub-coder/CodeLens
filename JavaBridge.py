"""
Bridge to the parser-service (see /parser-service), a small Java CLI built
on JavaParser (com.github.javaparser) that does the actual AST walk.

Design note (worth restating in the viva if asked "why not parse Java from
Python directly?"): JavaParser's *symbol solver* -- the part that resolves
`helper()` inside `doThing()` to the actual declaring class, including
across files and jars -- has no equivalent-fidelity pure-Python
implementation. Pure-Python Java grammars (e.g. `javalang`) parse syntax
but do not resolve symbols, which is precisely the information ACOR's call
graph needs. Running JavaParser in its native JVM and shipping the result
out as JSON is the pragmatic choice: one `subprocess` call per index
operation, no long-lived second server to operate for a college-scale
deployment.

If the team later wants live, incremental parsing (e.g. re-parse just the
file a developer saved) the natural upgrade is to turn parser-service into
a persistent process the backend talks to over stdin/stdout or a small
socket, instead of paying JVM startup cost per call -- flagged here rather
than built now, since the synopsis' Week 5-10 scope is "get the pipeline
working end to end" first.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


class JavaParserError(RuntimeError):
    """Raised when the parser-service JAR fails, times out, or emits bad JSON."""


@dataclass(frozen=True)
class ClassInfo:
    id: str  # fully-qualified name, e.g. "com.foo.Bar"
    file_path: str
    name: str
    package: str
    start_line: int
    end_line: int
    imports: list[str]


@dataclass(frozen=True)
class MethodInfo:
    id: str  # "com.foo.Bar#doThing(int,String)"
    class_id: str
    name: str
    signature: str
    file_path: str
    start_line: int
    end_line: int
    modifiers: list[str]


@dataclass(frozen=True)
class CallEdge:
    caller_id: str
    callee_id: str


@dataclass(frozen=True)
class ImportEdge:
    from_class_id: str
    to_class_id: str


@dataclass(frozen=True)
class ParsedRepo:
    classes: list[ClassInfo]
    methods: list[MethodInfo]
    call_edges: list[CallEdge]
    import_edges: list[ImportEdge]
    unparsed_files: list[str]  # files JavaParser could not parse (syntax errors, etc.)


def parse_repository(repo_path: Path, settings: Settings) -> ParsedRepo:
    """Invoke the parser-service JAR and load its JSON report.

    Raises JavaParserError on any failure so callers (the /repositories/index
    route) can turn it into a clean 4xx/5xx instead of a raw traceback.
    """
    jar_path = settings.java_parser_jar_path
    if not jar_path.exists():
        raise JavaParserError(
            f"parser-service JAR not found at {jar_path}. Build it first: "
            f"cd parser-service && mvn -q package"
        )

    output_path = repo_path / ".codelens-parse-output.json"
    command = [
        settings.java_bin,
        "-jar",
        str(jar_path),
        str(repo_path),
        str(output_path),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.parser_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JavaParserError(
            f"parser-service timed out after {settings.parser_timeout_seconds}s "
            f"on {repo_path}. Large monorepos may need PARSER_TIMEOUT_SECONDS raised."
        ) from exc

    if completed.returncode != 0:
        raise JavaParserError(
            f"parser-service exited with code {completed.returncode}.\n"
            f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
        )

    if not output_path.exists():
        raise JavaParserError(
            "parser-service reported success but produced no output file "
            f"at {output_path}"
        )

    try:
        raw = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JavaParserError(f"parser-service output was not valid JSON: {exc}") from exc
    finally:
        output_path.unlink(missing_ok=True)

    return _deserialize(raw)


def _deserialize(raw: dict) -> ParsedRepo:
    try:
        classes = [ClassInfo(**c) for c in raw["classes"]]
        methods = [MethodInfo(**m) for m in raw["methods"]]
        call_edges = [CallEdge(**e) for e in raw["call_edges"]]
        import_edges = [ImportEdge(**e) for e in raw["import_edges"]]
        unparsed_files = raw.get("unparsed_files", [])
    except (KeyError, TypeError) as exc:
        raise JavaParserError(f"parser-service output did not match expected schema: {exc}") from exc

    return ParsedRepo(
        classes=classes,
        methods=methods,
        call_edges=call_edges,
        import_edges=import_edges,
        unparsed_files=unparsed_files,
    )