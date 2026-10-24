# MCP tool validation

**Result:** both query tools passed the Python MCP SDK client/server test
over a spawned stdio process on 2026-10-24. The server subprocess used a
temporary local Java fixture and a test-only fake LLM; no external model or
credential was used.

## Verified

- `codelens_root_cause` returns a structured answer, evidence references,
  and provider-usage metrics.
- `codelens_change_impact` accepts a changed symbol and returns structured
  output with selected evidence.
- The MCP tool list contains both tools and the read-only
  `codelens_graph_inspect` tool.
- Invalid token budgets are surfaced as tool errors.
- The test uses `mcp.client.stdio.stdio_client` and launches
  `tests/fixtures/mcp_stdio_server.py` as a child process; this is stronger
  than an in-memory stream test.

## Command

From `backend/`:

```powershell
python -m pytest tests\test_mcp_server.py -q
```

Verified result: **2 passed**. One test uses the in-memory MCP transport to
check structured validation and errors; the second uses a spawned stdio
server/client process.

## Not verified

- No VS Code, Cursor, Claude Desktop, or other MCP-capable IDE was launched
  for a UI session.
- The spawned-process test uses a synthetic indexed repository fixture and
  fake LLM; it does not certify Groq availability, a user-specific IDE
  launch configuration, or a long-running server lifecycle.
- Live provider generation and IDE secret handling were not exercised in
  this protocol test.

See [`MCP_SERVER_SETUP.md`](MCP_SERVER_SETUP.md) for setup and repository
status troubleshooting.
