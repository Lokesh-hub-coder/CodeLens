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
| Read-only graph inspection | **Complete** | HTTP `GET /repositories/{repo_id}/graph` and MCP `codelens_graph_inspect` expose graph counts and bounded edges near a resolved symbol. Counts and sample Lang edges are documented in `ACOR_GRAPH_VERIFICATION.md`. |
| LANG-818 source-grounded causal validation | **Complete** | `ACOR_LANG818_ROOT_CAUSE_VALIDATION.md` cites the pinned buggy and fixing commits, the production method, the upstream regression test, and the incorrect `format(Date)` citation distinction. |
| Explain the old 13% parser figure | **Complete, narrowly** | Parser source computes the displayed graph-signal percentage as resolved in-repository edges divided by all visited `MethodCallExpr`s. External and unresolved calls are intentionally omitted; this is not labelled recall. The name/arity comparison is explicitly marked heuristic in `parser-service/src/main/java/com/codelensai/parser/RepoParser.java`. |
| Labelled call-graph measurement | **Complete for fixture only** | `RepoParserCallGraphTest` compares six manually labelled fixture edges with parsed output: 6 TP, 0 FP, 0 FN; precision 1.00 and recall 1.00. It does not establish repository-wide resolution. |
| Commons Lang full-repository retrieval/model evaluation | **Complete as saved pilot; not rerun here** | Existing `benchmarks/results/commons-lang-final.*` records 45/45 task-condition rows. The main conditions share `openai/gpt-oss-120b`; six `acor_no_semantic` calls used `qwen/qwen3.8-27b` after quota limits. The five-task pilot is too small for a general claim. |
| Targeted five-task retrieval after class-anchor/BM25 changes | **Measured; rerun blocked** | The earlier recorded no-LLM run was persisted without rerunning into new `benchmarks/results/commons-lang-targeted-retrieval-after-anchor-bm25-5000.{json,csv,md}` files. It measured 5/5 coverage, Recall@10 0.8, MRR 0.6538, with LANG-818 rank 53. Its per-task latency was not retained. Only `backend/data/index/commons-lang-818-demo.pkl`, `commons_lang.pkl`, and `parser_svc.pkl` are present; the temporary five-index directory is absent, so this audit did not rerun all five tasks. |
| Reproducibility documents and preliminary paper | **Complete as drafts** | See `../research/ACOR_experiment_protocol.md`, `../research/ACOR_REPRODUCIBILITY_CHECKLIST.md`, `../research/ACOR_paper_draft.md`, and `../research/RELATED_WORK.md`. The draft makes no superiority or novelty claim. |
| IDE-specific MCP test | **Pending** | Protocol-level stdio tests passed; no IDE was available in this environment. |
| Production readiness | **Not claimed** | HTTP authentication, multiprocess watcher coordination, full Lang indexing resource limits, and provider-dependent behavior remain limitations. |

## Validation run for this audit

From `backend/`:

```powershell
python -m pytest tests\test_verification_batch.py tests\test_mcp_server.py tests\test_prompts.py -q
```

Result: **22 passed** before adding the spawned-process MCP case. The
individual MCP suite subsequently passed **2 tests**, including the spawned
stdio client and both query tools. The full backend and parser suites are
reported in `project.md` and `evaluation.md` after final verification.

The local status endpoint probe returned:

```text
200 {'repo_id': 'commons-lang-818-demo', 'indexed': True,
     'num_methods': 2151, 'num_classes': 155, ...}
```

This proves the stored index can be loaded by this workspace process when
started from `backend/`; it does not prove that another already-running
server uses the same current directory or settings. For the Swagger 404,
first call `GET /repositories/commons-lang-818-demo/status` on that server.
If it reports `indexed: false`, verify that the server was started from
`backend/` and that its `INDEX_STORAGE_DIR` points to the directory
containing `commons-lang-818-demo.pkl`. Do not reindex unless that status
check or the 410 source-checkout response shows the loaded index is stale.
