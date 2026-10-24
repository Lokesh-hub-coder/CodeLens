# ACOR graph verification

## Graph construction and use

1. `backend/app/parsing/java_bridge.py` deserializes JavaParser's
   `call_edges` and `import_edges`.
2. `backend/app/api/routes.py:index_repository` builds
   `CallGraph.build(parsed.methods, parsed.call_edges)` and
   `DependencyGraph.build(parsed.classes, parsed.import_edges)`.
3. `backend/app/core/repo_index.py:RepoIndex.save` persists both graph
   objects, the symbol index, and parser edge counts in the repository
   pickle. `RepoIndex.load` restores them.
4. `backend/app/core/query_service.py:execute_repository_query` passes the
   loaded graphs and symbol index into `score_candidates`; the ranker
   selects candidates using those composite scores.
5. `inspect_repository_graphs` and the read-only HTTP/MCP graph inspection
   surfaces expose counts and a bounded neighborhood for verification.

## Existing LANG-818 persisted index

Inspection of `backend/data/index/commons-lang-818-demo.pkl` found:

| Structure | Nodes/entries | Edges |
|---|---:|---:|
| Call graph | 2,143 method nodes | 1,866 stored directed edges |
| Dependency graph | 155 class nodes | 135 stored directed edges |
| Symbol index | 2,298 symbol entries | n/a |

The pickle's parser `num_call_edges` field is 2,127. It counts parser
emissions before the graph builder drops edges whose caller or callee is
not one of the stored method nodes; therefore it is expected to differ from
the 1,866 graph edges. Dependency parser and stored graph counts are both
135 for this index.

Sample stored call edges:

```text
FastDateFormat#format(Calendar)
  -> FastDatePrinter#format(Calendar)
FastDatePrinter#format(Calendar,StringBuffer)
  -> FastDatePrinter#applyRules(Calendar,StringBuffer)
FastDatePrinter#applyRules(Calendar,StringBuffer)
  -> FastDatePrinter.Rule#appendTo(StringBuffer,Calendar)
```

The full IDs include `org.apache.commons.lang3.time`. These demonstrate
that parsed graph edges are populated. They do not prove every source call
was resolved or that a graph edge alone changes top-k retrieval.

## Targeted behavior test

`backend/tests/test_verification_batch.py::test_graph_endpoint_reports_edges_near_resolved_symbol`
asserts a sample caller edge and dependency edge returned around
the exact ID `com.foo.A#changedMethod()`; the test fixture explicitly
builds those edges. A second regression fixture contains the same method
name in two classes and verifies a fully qualified ID does not fall back
to the first same-name declaration. The MCP stdio integration test also
checks `codelens_graph_inspect` symbol lookup by simple name.

Run from `backend/`:

```powershell
python -m pytest tests\test_verification_batch.py tests\test_mcp_server.py -q
```

Graph counts are exposed by
`GET /repositories/{repo_id}/graph?symbol=...&max_hops=2&limit=20`.
No unbounded edge dump is returned. The API currently reports the stored
graph's node/edge counts, not a parser resolution percentage; use labelled
fixtures for precision/recall.

The live API probe with the recovered LANG-818 index resolved
`org.apache.commons.lang3.time.FastDateFormat#format(Calendar,StringBuffer)`
to that exact method and returned the delegation edges to
`FastDatePrinter#format(Calendar,StringBuffer)` and its nearby methods.
Use the full indexed method signature when querying overloaded methods.
