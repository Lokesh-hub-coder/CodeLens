import json
from pathlib import Path
import subprocess

from app.config import Settings
from app.parsing import java_bridge


def test_parser_bridge_passes_configured_classpath(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    jar = tmp_path / "parser-service.jar"
    jar.touch()
    configured_classpath = "dependency-one.jar;dependency-two.jar"
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        output = Path(command[7])
        output.write_text(
            json.dumps({
                "classes": [],
                "methods": [],
                "call_edges": [],
                "import_edges": [],
                "unparsed_files": [],
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(java_bridge.subprocess, "run", fake_run)

    result = java_bridge.parse_repository(
        repo_root,
        Settings(
            java_parser_jar_path=jar,
            parser_classpath=configured_classpath,
        ),
    )

    assert result.methods == []
    assert observed["command"][1:4] == [
        "-Xmx512m",
        "-XX:ActiveProcessorCount=2",
        "-XX:+UseSerialGC",
    ]
    assert observed["command"][8] == configured_classpath
    assert observed["command"][9] == "true"
