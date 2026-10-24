from pathlib import Path
from runpy import run_path

import pytest


_IS_EXCLUDED = run_path(
    str(Path(__file__).resolve().parents[2] / "scripts" / "create_release_zip.py")
)["is_excluded"]


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "backend/.ENV",
        "backend/.env.local",
        "secrets/private.pem",
        "data/qdrant/index.db",
        "backend/.venv/Lib/site-packages/module.pyc",
        "parser-service/target/parser.jar",
        "logs/backend.log",
    ],
)
def test_release_archive_excludes_secrets_and_generated_files(path):
    assert _IS_EXCLUDED(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "backend/app/main.py",
        "docs/architecture.md",
    ],
)
def test_release_archive_keeps_safe_source_files(path):
    assert not _IS_EXCLUDED(path)
