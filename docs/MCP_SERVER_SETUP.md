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
index alias (when it exists in the server's configured storage):

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

The `repo_id` must match the index filename (without `.pkl`) in the running
server's configured `INDEX_STORAGE_DIR`. For example, the recovered
five-task benchmark state uses `lang-8-cfab1e4f`, not
`commons-lang-818-demo`. In a live test using that explicit state,
`commons-lang-818-demo/status` correctly returned
`repository_not_indexed`, while `lang-8-cfab1e4f/status` returned
`indexed: true` with 2,151 methods. The separate
`backend/data/index/commons-lang-818-demo.pkl` is a different index alias;
a separately running server may use a different storage directory.

If status is false, inspect the response's `error_code` and verify the
server working directory and `INDEX_STORAGE_DIR` before reindexing.
`repository_not_indexed` means the configured directory has no matching
index. `repository_source_unavailable` means the index exists but its
stored source checkout is gone (HTTP 410 on query). Restore the correct
checkout rather than reindexing if the persisted index is otherwise valid.

Graph-inspection requests should use exact indexed method identifiers for
overloaded methods, for example:

```text
GET /repositories/lang-8-cfab1e4f/graph?symbol=org.apache.commons.lang3.time.FastDateFormat%23format%28Calendar%2CStringBuffer%29
```

The API resolves this to the exact method ID. Simple class/method names
remain supported, but an ambiguous short name can resolve to multiple
declarations.

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
