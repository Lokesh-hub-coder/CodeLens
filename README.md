# CodeLens

CodeLens indexes Java repositories and retrieves a token-budgeted code
excerpt for root-cause and change-impact questions. ACOR ranks methods
using semantic similarity, symbol matching, call-graph proximity, and
dependency-graph proximity. This is an actively tested prototype, not a
production-ready service.

## Verified components

- Java parsing is delegated to the JavaParser CLI under `parser-service/`.
- FastEmbed generates method vectors; Qdrant is the only supported vector
  store (local embedded storage by default, remote Qdrant with URL/key).
- ACOR scores and deduplicates candidates. The final formatted excerpt,
  including file and method headers, is recounted and kept within its
  configured context-token budget.
- Watchdog observes indexed repositories. A debounced change triggers a
  full Java repository reparse so cross-file method resolution, vectors,
  symbol indexes, call/dependency graphs, and persisted counts are rebuilt.
- The MCP stdio server exposes `codelens_root_cause` and
  `codelens_change_impact`, plus a read-only `codelens_graph_inspect` tool.
  The query tools return the compatible text `answer` field alongside
  validated structured answers, selected-source evidence, and metrics.
  The tools have been exercised through a spawned MCP Python SDK stdio
  client/server test; actual IDE-specific setup has not been tested here.
- `GET /repositories/{repo_id}/status` checks the active server's index
  visibility. `GET /repositories/{repo_id}/graph` reports graph counts and
  bounded edges around a symbol. See
  [`docs/MCP_SERVER_SETUP.md`](docs/MCP_SERVER_SETUP.md) for the Swagger
  "repository not indexed" diagnostic.
- The labeled-task research runner compares whole-file, semantic-only, and
  ACOR retrieval. No empirical benchmark results are claimed; see
  [`docs/evaluation.md`](docs/evaluation.md).

## Prerequisites

- Python 3.12 (the dependency versions below were exercised with Python
  3.12).
- JDK 17 and Maven for building the parser service.
- Node.js only if you want the React frontend.

## Setup

From the repository root, create and activate a backend environment and
install dependencies:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a local backend configuration. The example contains no credentials:

```powershell
Copy-Item ..\.env.example .env
```

Set `GROQ_API_KEY` (or configure another supported LLM provider) for live
answers. Without credentials, interactive queries use an offline demo
response; the research benchmark refuses to treat that mock as a real
model. Local Qdrant storage requires no account. For Qdrant Cloud, set
`QDRANT_URL` and `QDRANT_API_KEY`.

Build the parser JAR from the repository root:

```powershell
mvn -f parser-service\pom.xml -q package
```

The default parser JAR path is
`parser-service/target/parser-service-1.0.0.jar`. Start the backend from
`backend/`:

```powershell
python -m uvicorn app.main:app --reload
```

The API docs are available at `http://localhost:8000/docs`. Index a Java
repository with the API or the CLI:

```powershell
python ..\scripts\index_repository.py --repo C:\path\to\java-repository --query "Why does parseAmount fail?"
```

`EXCLUDE_TEST_SOURCES` defaults to `true`; set it to `false` to include
test-source files. Index data and Qdrant files are written under the
configured runtime data directories and are not source-controlled.

Swagger's query URL must use the exact `repo_id` returned by indexing.
Before reindexing after a "not indexed" response, call
`GET /repositories/{repo_id}/status` on the same running server and confirm
its working directory is `backend/` or that `INDEX_STORAGE_DIR` points to
the intended index directory.

To run the frontend, in a separate terminal:

```powershell
cd frontend
npm install
npm run dev
```

### Docker Compose development setup

Compose is a local development setup, not a production deployment. Build
the parser JAR first, then create the root Compose environment file and
start the services:

```powershell
mvn -f parser-service\pom.xml -q package
Copy-Item .env.example .env
docker compose up --build
```

Set a real provider key in the root `.env` for model-backed answers. Put
Java repositories to index under `data\repos` so the backend container can
read them; submit their container-visible paths (for example,
`/data/repos/sample`) to the API. Compose persists local Qdrant and index
data under `data\`. The frontend service runs the development server.

## Live sync

After a successful index request, a background watcher starts for that
repository. Create, modify, rename, and delete events for Java files are
debounced (`LIVE_SYNC_DEBOUNCE_SECONDS`, default `0.4`) and trigger a
repository-wide reparse. This favors graph and symbol correctness over
incremental speed. A failed parse is logged and leaves the previous
in-memory graphs and symbol index intact; retry by making another change
or re-indexing. The watcher is process-local and is not a multi-worker
coordination mechanism.

## MCP tools

The MCP server runs over stdio. Configure an MCP-capable editor to launch
Python with working directory `backend/` and arguments
`-m app.mcp.server`. For VS Code, for example, add an MCP server entry to
`.vscode/mcp.json`:

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

Index the repository through the HTTP API before calling an MCP tool. The
server uses the same backend `.env`, Qdrant collection, and index storage
settings. MCP tools are `codelens_root_cause`,
`codelens_change_impact`, and `codelens_graph_inspect`.

## Tests

Run the backend tests from `backend/`:

```powershell
python -m pytest tests -q
```

The regression suite covers scoring/ranking, rendered token limits,
Qdrant record removal, live-sync graph/vector updates and failures,
configuration validation, and both MCP tools over an MCP client/server
transport. Parser-JAR and real-provider integration still require the
external Java/model setup described above.

## Research evaluation

The checked-in Defects4J Apache Commons Lang pilot includes five
patch-anchored bug-localization tasks with revision-specific repository
indexes. See [`docs/evaluation.md`](docs/evaluation.md) for the reproducible
checkout/index commands and task provenance. Run a retrieval-only
measurement without contacting a model:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --retrieval-only --include-ablations
```

For model predictions, use the full-run command in the evaluation guide
only when an authorized provider may receive the task prompts and retrieved
source. The runner records real provider usage separately from estimates;
it does not treat estimates or mock output as model benchmark evidence.

## Secrets and release archives

Keep local credentials in an ignored `.env` file. `.gitignore` excludes
environment and key files, and `.gitattributes` plus the release script
exclude them from committed-source archives. Check a release ZIP with:

```powershell
python scripts\create_release_zip.py
```

The script archives `HEAD` only, refuses to overwrite an existing output,
and fails if sensitive paths or probable secret values are present. Review
the resulting file before publishing.

## Limitations

- Parsing is Java-focused and reparses the full repository after a Java
  file event. JavaParser may not resolve project-specific dependencies
  without their build classpath.
- The vector/index registry is process-local; multi-worker deployments
  need shared coordination and storage.
- The context budget limits the formatted repository excerpt, not the
  system prompt or developer query. `tiktoken` counts are a proxy for
  providers that do not report usage.
- The API has no authentication and should not be exposed to untrusted
  networks. Treat indexed source, local index pickles, and LLM-provider
  data handling as sensitive.
- No benchmark numbers, live IDE validation, or production-readiness
  claim is made by this repository.

For implementation details, see
[`docs/architecture.md`](docs/architecture.md) and
[`docs/ACOR_algorithm.md`](docs/ACOR_algorithm.md).
