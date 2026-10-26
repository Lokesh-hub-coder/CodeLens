import asyncio

import pytest
from fastapi import HTTPException

from app.api import routes
from app.api.schemas import IndexRepositoryRequest
from app.config import Settings
from app.parsing.java_bridge import ParsedRepo


def test_indexing_aborts_when_java_sources_were_unparsed(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "Broken.java").write_text("class Broken {", encoding="utf-8")
    monkeypatch.setattr(
        routes,
        "parse_repository",
        lambda *_: ParsedRepo([], [], [], [], ["src/main/java/Broken.java"]),
    )
    settings = Settings(index_storage_dir=tmp_path / "indexes")

    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.index_repository(
            IndexRepositoryRequest(repo_path=str(repository), repo_id="incomplete"),
            settings,
        ))

    assert error.value.status_code == 422
    assert "1 Java source files failed to parse" in error.value.detail
