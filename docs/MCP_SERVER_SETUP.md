# MCP server setup and Swagger index troubleshooting

## Start the backend

CodeLens resolves default index and Qdrant directories relative to its
working directory. Start the API from `backend/`:

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

In Swagger at `http://localhost:8000/docs`, check
`GET /repositories/{repo_id}/status` before querying. For the saved Lang
index:

```powershell
Invoke-RestMethod http://localhost:8000/repositories/commons-lang-818-demo/status
```

The API query URL uses the exact indexed `repo_id`:

```text
POST /repositories/commons-lang-818-demo/query
```

Example body:

```json
{
  "query": "Why does FastDateFormat.format(Calendar) return the wrong timezone name when the Calendar uses a different zone?",
  "token_budget": 5000
}
```

The current workspace's persisted index returned `indexed: true` with 2,151
methods and 155 classes when loaded from `backend/`. If it is unavailable,
the status response includes an `error_code` distinguishing
`repository_not_indexed` from `repository_source_unavailable`. That result cannot
prove what a separately running server has loaded. If the server status
is false, verify its working directory and `INDEX_STORAGE_DIR` first. An
index file without its source checkout is reported as unavailable (HTTP
410 on query); provide the valid checkout and re-index only if needed.

## Configure a stdio MCP client

The MCP server process also needs `backend/` as its working directory so
it reads the same settings and index. Example VS Code `.vscode/mcp.json`
entry for a local virtual environment:

```json
{
  "servers": {
    "codelens": {
      "type": "stdio",
      "command": "${workspaceFolder}/backend/.venv/Scripts/python.exe",
      "args": ["-m", "app.mcp.server"],
      "cwd": "${workspaceFolder}/backend"
    }
  }
}
```

For a global Python installation, use the absolute Python executable
instead. Do not put API keys in this file; provider credentials belong in
an ignored backend `.env` file or process environment.

Index the repository with the HTTP API before using the tools. Both query
tools use the same configured index and provider as the HTTP API:

- `codelens_root_cause(repo_id, query, token_budget?)`
- `codelens_change_impact(repo_id, query, changed_symbol, token_budget?)`
- `codelens_graph_inspect(repo_id, symbol?, max_hops?, limit?)`

The three tools return structured results; the first two include the
backward-compatible text answer plus evidence and metrics. The graph tool
is read-only and bounds edge output by `max_hops` and `limit`.

## Validation status

`backend/tests/test_mcp_server.py` includes a spawned server process using
the Python MCP SDK stdio client. It invokes both query tools and asserts
structured content. This verifies protocol compatibility with the SDK, not
the editor UI or editor-specific configuration discovery. See
[`MCP_TOOL_VALIDATION.md`](MCP_TOOL_VALIDATION.md).
