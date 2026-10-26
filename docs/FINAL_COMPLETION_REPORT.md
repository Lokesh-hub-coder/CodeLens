# CodeLens final completion report

## Summary

This pass completed the remaining retrieval-only evaluation, diagnosed
LANG-818's low ACOR rank without applying an unsupported ranking change,
verified the recovered index state through the API, corrected exact
qualified-symbol graph lookup, and ran the backend, parser, MCP, and diff
checks. The 45-condition retrieval-only evaluation made no LLM/provider
requests. Existing generation reports were preserved.

The project is a tested research prototype, not production-ready. The
five-task results are descriptive and do not establish general ACOR
superiority. LANG-818 remains poorly ranked, and no live IDE session was
available for MCP validation.

## Completed work

- Recreated five isolated indexes from the pinned Defects4J Lang buggy
  revisions and verified zero unparsed Java files. The combined index state
  remains outside the repository at
  `%TEMP%\codelens-final-index-recovery\combined-state`.
- Ran 45 retrieval-only task-condition rows at the same estimated
  5,000-token budget, including whole-file, semantic-only, ACOR, signal
  ablations, greedy, and exact selection. Saved row-level JSON/CSV and the
  summary report. No provider was called.
- Investigated LANG-818 with the saved query, candidate scores, pinned
  source, and graph data. No ranking heuristic was adopted because a tested
  anchor-propagation trial made the gold method rank worse.
- Fixed qualified graph-symbol resolution so fully qualified class/method
  IDs cannot fall back to a different declaration with the same simple
  method name. Added a regression test.
- Ran an isolated API probe with the recovered index state and a
  deterministic local fake client. Verified status/error distinctions,
  structured query output, token-budget accounting, exact-symbol graph
  lookup, and OpenAPI response schemas. No external model was used.
- Ran the MCP SDK stdio client/server tests for
  `codelens_root_cause` and `codelens_change_impact`.
- Reviewed reachable Git history for `.env` paths and common credential
  formats. No tracked or historical `.env` paths were found; the local
  `backend/.env` exists, is ignored, and was preserved. The heuristic scan
  found no recognizable private-key, GitHub, Groq, or AWS token patterns.
  Three generic assignment-pattern matches were configuration plumbing or
  an example variable, not literal credentials. This scan cannot prove
  that every possible secret format is absent.
- Removed only the named temporary `source-unavailable-fixture.pkl` that
  had been created for the API probe. The recovered indexes and other
  user data were preserved.

## LANG-818 retrieval evidence

The gold method,
`org.apache.commons.lang3.time.FastDatePrinter.TimeZoneNameRule#appendTo(StringBuffer,Calendar)`,
was present in the 161-candidate ACOR pool. Its full-candidate rank was
**62** and its position in the selected budgeted context was **53**. Its
measured scores were semantic **0.8357**, call graph **0.0**, dependency
graph **0.0**, symbol **0.5**, and composite **0.459261**. The top ten
candidates were exact `FastDateFormat` class methods with higher symbol
and dependency scores. The graph does not represent the relevant interface
dispatch or dependency path.

A diagnostic trial propagating one automatically selected similar
same-package class anchor changed the gold rank from **62 to 67** and
budgeted-context position from **53 to 55**. It was rejected; no improvement
is claimed. The earlier targeted report's rank 53 did not specify whether
it meant candidate rank or selected-context position, so it is not treated
as a directly comparable full-candidate rank.

## Retrieval-only evaluation

All five pinned indexes completed: **537 Java source files**, **11,060
methods**, **0 unparsed files**, mean indexing time **81.55 seconds per
repository**. All **45/45** retrieval-only rows succeeded. Metrics below
are selected-context localization metrics; tokens are local estimates, not
provider-reported usage.

| Condition | Recall@10 | MRR | Gold context coverage | Mean estimated tokens | Mean retrieval latency |
|---|---:|---:|---:|---:|---:|
| Whole-file | 0.40 | 0.2667 | 2/5 | 4,814.6 | 664.4 ms |
| Semantic-only | 0.20 | 0.2000 | 1/5 | 4,259.4 | 59.7 ms |
| ACOR | 0.80 | 0.6538 | 5/5 | 4,264.2 | 85.7 ms |
| ACOR without semantic | 0.80 | 0.6500 | 4/5 | 4,305.8 | 84.9 ms |
| ACOR without symbol | 0.80 | 0.6537 | 5/5 | 4,294.2 | 86.6 ms |
| ACOR without call graph | 0.60 | 0.4538 | 4/5 | 4,200.4 | 83.9 ms |
| ACOR without dependency graph | 0.80 | 0.6537 | 5/5 | 4,274.8 | 87.7 ms |
| ACOR greedy | 0.80 | 0.6538 | 5/5 | 4,264.2 | 85.7 ms |
| ACOR exact | 0.40 | 0.2537 | 3/5 | 4,174.4 | 146.4 ms |

The full per-task ranks, Recall@1/5/10, candidate coverage, token
estimates, latency, indexing records, and reproduction command are in
[`../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md`](../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md);
row-level results are in the adjacent
[`commons-lang-final-completion-retrieval-only-5000.json`](../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.json)
and
[`commons-lang-final-completion-retrieval-only-5000.csv`](../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.csv).
The saved model-generation results are separate and were not rerun or
overwritten. Retrieval-only provider input tokens are **not applicable**;
no model request occurred. These five tasks do not establish statistical
or general superiority, and the evaluation does not measure generated
answer accuracy or change-impact precision/recall.

## API and MCP validation

An isolated API server was started from `backend/` with
`INDEX_STORAGE_DIR` and `QDRANT_PATH` pointed at the recovered temporary
state and `LLM_PROVIDER=mock`.

- `commons-lang-818-demo` returned `repository_not_indexed` because that
  alias was absent from the explicitly configured directory.
- `lang-8-cfab1e4f` returned `indexed=true` with 2,151 methods.
- A temporary missing-source fixture returned
  `repository_source_unavailable`; the fixture was removed after the probe.
- A query returned HTTP 200 with a Pydantic-validated structured response,
  80 evidence items, 3,721 estimated context tokens within the 5,000-token
  limit, and null provider usage. The client was test-only and deterministic.
- A graph request for the exact
  `org.apache.commons.lang3.time.FastDateFormat#format(Calendar,StringBuffer)`
  ID resolved that method and returned its nearby edges. OpenAPI included
  `QueryResponse`, `EvidenceReference`, `GraphInspectionResponse`, and
  `StructuredAnalysisAnswer`.
- MCP stdio tests invoked both query tools and validated structured
  results. No VS Code, Cursor, Claude Desktop, or other compatible IDE UI
  was available; IDE-specific discovery and lifecycle behavior remain
  unverified.

## Exact files changed in this pass

- `backend/app/core/query_service.py`
- `backend/tests/test_verification_batch.py`
- `benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md`
- `docs/ACOR_GRAPH_VERIFICATION.md`
- `docs/ACOR_LANG818_ROOT_CAUSE_VALIDATION.md`
- `docs/ACOR_REMAINING_WORK_AUDIT.md`
- `docs/FINAL_COMPLETION_REPORT.md`
- `docs/MCP_SERVER_SETUP.md`
- `docs/evaluation.md`
- `docs/project.md`
- `research/ACOR_REPRODUCIBILITY_CHECKLIST.md`
- `research/ACOR_experiment_protocol.md`
- `research/ACOR_paper_draft.md`

The adjacent JSON and CSV row-level reports are present and preserved; they
were not changed in the final worktree diff. Existing generation reports
were not overwritten. No Docker, ZIP/release packaging, provider, or
frontend work was performed in this pass.

## Tests and checks

Commands are shown relative to their working directories.

From `backend/`:

```powershell
python -m pytest tests -q
python -m pytest tests\test_mcp_server.py -q
python -m pytest tests\test_verification_batch.py -q
```

Results: **98 passed** for the backend suite (one Starlette deprecation
warning); **2 passed** for MCP stdio integration; **21 passed** for focused
API/graph verification.

From the repository root:

```powershell
mvn -f parser-service\pom.xml -q -T 1 -DforkCount=0 test
git diff --check
```

Results: **3 parser tests passed, 0 failures**; `git diff --check` passed.
Git emitted LF/CRLF working-copy warnings for an existing edited test file,
not whitespace errors.

The labelled parser fixture resolved **6/6** expected in-repository edges
(precision **100%**, recall **100%**) and correctly excluded two JDK calls.
This is fixture-only evidence, not representative project-wide call-graph
precision/recall.

## Reproduction and run commands

Recreate the pinned checkouts and indexes using the prerequisites and
serial indexing script in
[`../research/ACOR_REPRODUCIBILITY_CHECKLIST.md`](../research/ACOR_REPRODUCIBILITY_CHECKLIST.md).
The five task IDs, buggy revision hashes, and labels are in
[`../benchmarks/commons-lang-defects4j.json`](../benchmarks/commons-lang-defects4j.json).
The saved retrieval-only result can be reproduced with the command in the
benchmark report, after rebuilding the temporary combined state. Use a new
output basename if preserving the existing result.

Start the API from `backend/`:

```powershell
python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs`; check repository status before sending a
query. Example status and query calls:

```powershell
Invoke-RestMethod http://localhost:8000/repositories/commons-lang-818-demo/status
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/repositories/commons-lang-818-demo/query `
  -ContentType 'application/json' `
  -Body '{"query":"Why does FastDateFormat.format(Calendar) return the wrong timezone name when the Calendar uses a different zone?","token_budget":5000}'
```

The repository ID must exist under the running server's configured
`INDEX_STORAGE_DIR`; the recovered temporary state uses
`lang-8-cfab1e4f`, not the `commons-lang-818-demo` alias.

Start the stdio MCP server from `backend/`:

```powershell
python -m app.mcp.server
```

Use the same `backend/` working directory in the IDE MCP configuration.
The verified protocol command is
`python -m pytest tests\test_mcp_server.py -q`; it does not substitute for a
real IDE session. Provider credentials stay in the ignored local
environment and are not included in these commands.

## Limitations and final status

- LANG-818 candidate generation finds the gold method, but its score and
  rank remain poor; no evidence-backed fix was validated.
- No live IDE MCP test was possible. The API probe used a fake client, and
  this pass made no Groq or other model calls.
- The retrieval evaluation has five tasks and local token estimates only.
  It does not establish generalization, generated-answer quality,
  change-impact metrics, or provider token use.
- Fixture call-graph precision/recall is not representative of real
  projects. External dependency classpaths and interface dispatch remain
  limitations.
- Related-work source links were reviewed, but a publication-ready
  bibliography and citation-format audit remain outstanding.
- Earlier indexing retries succeeded, but the cause of the prior ONNX
  allocation and Qdrant write-timeout failures was not isolated.

**Submission readiness:** Code and local automated validation are complete
for this pass. The repository is suitable for continued research review as
a prototype with explicit limitations, but not for a production-readiness
claim or a paper claim of general ACOR superiority. A real IDE session,
broader representative evaluation, LANG-818 ranking improvement, and
formal citation verification remain open.
