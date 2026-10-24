# ACOR remaining-work audit

**Evidence date:** 2026-10-24  
**Scope:** API/MCP contract, graph inspection, LANG-818 root cause, saved
Commons Lang evaluation, and reproducibility. This is an audit of the
current workspace, not a production-readiness certification.

| Work item | Status | Evidence and limitation |
|---|---|---|
| Distinguish an absent index from an index whose source checkout is gone | **Complete** | `backend/app/api/routes.py` returns 404 for a missing index and 410 for a missing indexed source root. The status response now includes `error_code`/`message` to distinguish those cases. A local `TestClient` status request for `commons-lang-818-demo` returned 200/indexed=true when run from `backend/`. |
| Structured HTTP query response | **Complete** | `backend/app/api/schemas.py` keeps the legacy string `answer` and adds validated `structured_answer`, selected-source `evidence`, and `metrics`. `backend/app/core/query_service.py` validates provider JSON using Pydantic; invalid JSON remains visible as raw answer and has an explicit limitation. |
| Provider errors | **Complete** | Provider exceptions now return 429 for a provider 429, otherwise 502, with a non-secret error code/message instead of a success-shaped fallback. Covered by `test_query_endpoint_surfaces_provider_errors_without_success_fallback`. |
| MCP root-cause and change-impact response | **Complete at protocol level** | Both tools now share the query service and declare structured output. A spawned MCP stdio server/client test invokes both tools and checks structured response fields. No compatible IDE application was available for UI certification. |
| Read-only graph inspection | **Complete** | HTTP `GET /repositories/{repo_id}/graph` and MCP `codelens_graph_inspect` expose graph counts and bounded edges near a resolved symbol. Qualified method IDs now resolve exactly instead of falling back to the first same-name method; a regression test and live HTTP probe cover this. |
| LANG-818 source-grounded causal validation | **Complete** | `ACOR_LANG818_ROOT_CAUSE_VALIDATION.md` cites the pinned buggy and fixing commits, the production method, the upstream regression test, and the incorrect `format(Date)` citation distinction. |
| Explain the old 13% parser figure | **Complete, narrowly** | Parser source computes the displayed graph-signal percentage as resolved in-repository edges divided by all visited `MethodCallExpr`s. External and unresolved calls are intentionally omitted; this is not labelled recall. The name/arity comparison is explicitly marked heuristic in `parser-service/src/main/java/com/codelensai/parser/RepoParser.java`. |
| Labelled call-graph measurement | **Complete for fixture only** | `RepoParserCallGraphTest` compares six manually labelled fixture edges with parsed output: 6 TP, 0 FP, 0 FN; precision 1.00 and recall 1.00. It does not establish repository-wide resolution. |
| Commons Lang full-repository retrieval/model evaluation | **Generation pilot preserved; retrieval rerun complete** | Existing `benchmarks/results/commons-lang-final.*` records 45/45 generation rows. The main conditions share `openai/gpt-oss-120b`; six ablation generations used `qwen/qwen3.8-27b` after quota limits. A new retrieval-only run is separate and made no provider calls. Neither five-task run establishes general effectiveness. |
| Five-task retrieval-only comparison and ablations | **Reproduced** | All five pinned checkouts were re-indexed serially with 0 unparsed files and assembled into a fresh combined state. The new 45-row report measured ACOR Recall@10 0.8/MRR 0.6538 versus whole-file 0.4/0.2667 and semantic-only 0.2/0.2000. LANG-818 was rank 62 before budget selection and position 53 in selected context. Exact metrics and output paths are in `benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md`. |
| LANG-818 rank diagnosis | **Cause supported; ranking fix not adopted** | The gold method is present in the 161-candidate pool, but scores 0.459261 (semantic 0.8357, call graph 0, dependency 0, symbol 0.5). Exact-class competitors receive full class/dependency scores. The gold node has no call-graph edges and no dependency path to `FastDateFormat`. A tested generic related-class propagation heuristic worsened the target rank and was discarded; a non-arbitrary improvement remains open. |
| Indexing memory/write failures | **Recovery succeeded; original cause unresolved** | Earlier temporary workers reported an ONNX allocation failure and Qdrant write timeouts. Each repository subsequently indexed successfully in a separate serial worker run. No parser/embedding implementation change was made; the earlier failures were not reproduced or causally diagnosed. |
| Reproducibility documents and preliminary paper | **Updated** | `../research/ACOR_experiment_protocol.md`, `../research/ACOR_REPRODUCIBILITY_CHECKLIST.md`, `../research/ACOR_paper_draft.md`, and `../research/RELATED_WORK.md` distinguish generation evidence from the new retrieval-only rerun. The five-task result remains exploratory. |
| IDE-specific MCP test | **Pending** | Protocol-level stdio tests passed; no IDE was available in this environment. |
| Production readiness | **Not claimed** | HTTP authentication, multiprocess watcher coordination, full Lang indexing resource limits, and provider-dependent behavior remain limitations. |

## Validation run for this audit

From `backend/`:

```powershell
python -m pytest tests -q
python -m pytest tests\test_mcp_server.py -q
python -m pytest tests\test_verification_batch.py -q
```

Results: **98 backend tests passed** (one Starlette deprecation warning),
**2 MCP tests passed** including the spawned stdio client, and **21 focused
API/graph tests passed**. From the repository root,
`mvn -f parser-service\pom.xml -q -T 1 -DforkCount=0 test` passed all **3
parser tests**. `git diff --check` passed with existing LF/CRLF warnings.

The final API probe used an isolated Uvicorn process whose explicit
`INDEX_STORAGE_DIR` pointed at the recovered benchmark state. It returned:

```text
commons-lang-818-demo: repository_not_indexed (alias absent from this state)
lang-8-cfab1e4f: indexed=true, 2,151 methods
source-unavailable-fixture: repository_source_unavailable
```

The live query and graph requests returned 200; the query response was
Pydantic-validated using a deterministic test-only fake client, and the
graph route resolved an exact fully-qualified method ID and returned nearby
call edges. The test process made no provider calls. For the Swagger 404,
call `/repositories/{repo_id}/status` on the same server and confirm both
the exact `repo_id` and `INDEX_STORAGE_DIR`; do not reindex merely because
another process uses a different alias or storage directory.
