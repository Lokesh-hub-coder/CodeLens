# CodeLens project audit

**Audit date:** 2026-10-24  
**Status:** Prototype with regression coverage; not production-ready.

## Implemented and locally verified

- Backend regression suite after the API/MCP response and graph updates:
  `python -m pytest tests -q` — **98 passed, 1 Starlette deprecation
  warning**.
- Parser tests: `mvn -f parser-service\pom.xml -q -T 1 -DforkCount=0 test`
  and `package` both succeeded; **3 parser tests, 0 failures**. The
  no-classpath fixture run measured 6 true-positive in-repository edges
  out of 6 expected (precision **100%**, recall **100%**) and correctly
  omitted 2 external JDK calls.
- All five Defects4J labels were present when parsing only their affected
  source file; each produced 0 unparsed files. In the final pass, all five
  complete pinned checkouts were indexed serially: 537 Java source files,
  11,060 methods, 0 unparsed files, and 81.55 seconds mean indexing time
  per repository. Earlier temporary workers had reported ONNX memory
  allocation and Qdrant write-timeout failures; the isolated retries
  succeeded, but the earlier transient failure cause was not established.
- Parser source-root discovery now covers nested Maven/Gradle `src/main/java`,
  `src/test/java`, and `src/integrationTest/java` layouts, with an optional
  host path-separated `PARSER_CLASSPATH` for dependency JARs and source dirs.
  Backend indexing now tells the parser to exclude test sources when
  `EXCLUDE_TEST_SOURCES=true`.
- A five-task Defects4J Lang pilot is included with buggy/fixed revisions,
  issue references, and patch-anchored method labels. The saved 45-row
  model comparison and retrieval-only report are summarized in
  `evaluation.md`; they do not show ACOR superiority.
- Docker Compose configuration validation passed. Docker build/run remains
  blocked: `docker` resolves to the zero-byte
  `C:\Windows\System32\docker` placeholder; no Docker Desktop, Podman, or
  real Docker executable was found. The apparent successful `docker build`
  command returned no output and did not produce an inspectable image.
- The MCP client/server integration passed over both an in-memory SDK
  transport and a spawned stdio server/client process. Both query tools
  return structured output in the tests. No compatible IDE UI was available
  for a live editor test.
- Prior local model testing failed with `cudaMalloc failed: out of memory`;
  the saved Groq benchmark report contains actual provider usage and latency
  for completed rows. No LLM/provider calls were made during this audit.
- A temporary archive of committed `HEAD` contained 69 files and passed
  excluded-artifact and secret-pattern checks; the test archive was removed.
- Java parsing through the JavaParser command-line service.
- FastEmbed method embeddings stored in per-repository Qdrant collections.
  Qdrant is the only configured vector backend.
- ACOR candidate scoring from semantic, symbol, call-graph, and dependency
  signals; duplicate method candidates are removed before ranking.
- Final context selection accounts for formatted file/method headers and
  enforces the configured excerpt-token limit. The limit excludes the
  system prompt and developer query; provider usage is recorded separately
  when available.
- Watchdog live sync for Java file create, modify, move/rename, and delete
  events. Each debounced event reparses the repository and refreshes
  embeddings, Qdrant IDs, symbol indexes, graphs, file counts, and persisted
  index state. This intentionally favors cross-file correctness over
  incremental parse speed.
- MCP tools `codelens_root_cause` and `codelens_change_impact`, exercised
  through spawned MCP stdio client/server protocol tests, plus the
  read-only `codelens_graph_inspect` tool. Query responses preserve the
  string `answer` field and add a Pydantic-validated structured answer,
  selected-method evidence, estimated prompt/context tokens, and provider
  usage values when returned. Invalid structured output and provider
  errors are explicitly represented; no specific IDE was launched or
  certified.
- Read-only graph endpoint `GET /repositories/{repo_id}/graph` returns
  node/edge counts and bounded edges near a resolved symbol. Qualified
  method IDs now resolve by exact symbol ID rather than falling back to the
  first repository-wide method with the same name; a regression test covers
  duplicate method names. The local `commons-lang-818-demo` pickle contains
  2,143 call nodes/1,866 graph edges, 155 dependency nodes/135 edges, and
  2,298 symbol entries.
- Repository status responses distinguish a missing index from an
  unavailable stored source checkout using `error_code` and `message`;
  this makes the reported Swagger 404 actionable without forcing a
  reindex.
- A labeled-task evaluation runner for whole-file retrieval,
  semantic-only retrieval, ACOR, signal ablations, and ranker comparisons.
  It requires explicit ground truth and a real configured model.

## Security and cleanup findings

- Local `.env` files are ignored and were not found among tracked paths or
  the scanned Git history. A scan of 208 text blobs across 421 reachable
  Git objects found no tracked or historical `.env` paths and no
  recognizable private-key, GitHub, Groq, or AWS token patterns. Three
  generic credential-assignment matches were reviewed as configuration
  plumbing or an example variable, not literal credentials. Heuristic scans
  cannot prove that no secret was ever committed.
- Release ZIP creation archives committed `HEAD`, applies export-ignore
  rules, and rejects sensitive paths and probable secret values. The
  validation run produced a 95-entry ZIP with no excluded paths or detected
  probable secrets. Because this workspace's changes are uncommitted, that
  archive contained only `HEAD` and did not include the current changes or
  `.env.example`; rerun after committing to validate a deliverable release.
- FAISS and Chroma support and dependencies have been removed. Runtime
  indexes, local Qdrant data, caches, logs, and build output are excluded
  from Git and release archives.
- The workspace virtual environment, parser build output, test caches, and
  JVM crash/replay logs found during this audit were removed. Local `.env`
  files were preserved.

## Measurements

The labelled fixture's call-graph precision/recall above is the only
ground-truth graph measurement. The saved Commons Lang generation pilot
has five bug-localization tasks and 45 successful generation conditions;
it reports actual provider token/latency values where returned, separately
from local estimates. A fresh retrieval-only run also completed 45/45
task-condition rows without provider calls. It measured whole-file /
semantic-only / ACOR Recall@10 **0.4 / 0.2 / 0.8**, MRR
**0.2667 / 0.2000 / 0.6538**, and mean retrieval latency
**664.4 / 59.7 / 85.7 ms**. Candidate-pool coverage was 5/5; LANG-818's
full-candidate rank was 62 and its budgeted-context position was 53.
See `evaluation.md` and the new retrieval-only report for per-task and
ablation results. Five tasks do not support a general superiority claim.

## Remaining limitations

- Live sync reparses all Java sources on every debounced event.
- Index registry and watchers are process-local; multi-worker coordination
  is not implemented.
- The parser's earlier `13%` graph-signal diagnostic is not ground-truth
  call-graph recall: source computes `resolved in-repository call edges /
  all visited method-call expressions`. That denominator includes external
  and unresolved calls. Name/arity overlap is a separate weak heuristic.
  The labelled fixture provides a valid but narrow precision/recall
  measurement; representative project coverage remains to be measured.
- Dependency JARs must be supplied using `PARSER_CLASSPATH` for external
  overload resolution. Real project-wide call-graph precision/recall is
  not yet established.
- The checked-in Defects4J task set currently measures bug localization
  only; change-impact gold labels still need independent annotation.
- Qdrant upsert/removal is not a transaction. Live sync retries vector
  refresh once; a persistent Qdrant failure can still leave vector data
  partially refreshed until a later successful sync or reindex.
- Token budgeting is for the formatted repository excerpt, not the entire
  model prompt. `tiktoken` may not match a provider's tokenizer.
- The HTTP API has no authentication. Do not expose it to untrusted
  networks.
- No IDE-specific MCP validation or production readiness assessment is
  claimed. Docker image build/run remains pending until a real container
  engine is available.
- During live API validation with the explicit recovered benchmark
  `INDEX_STORAGE_DIR`, `/repositories/commons-lang-818-demo/status` returned
  `repository_not_indexed` because that alias is not in this five-task
  state; `/repositories/lang-8-cfab1e4f/status` returned indexed with 2,151
  methods. The separate local `backend/data/index` contains the
  `commons-lang-818-demo` pickle. A separately running server can return
  either result depending on its configured index directory; check that
  server's status endpoint before reindexing.

See [`../README.md`](../README.md) for setup,
[`architecture.md`](architecture.md) for implementation boundaries, and
[`evaluation.md`](evaluation.md) for the benchmark protocol.
