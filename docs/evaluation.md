# Retrieval and model evaluation

## Included labelled Apache Commons Lang tasks

[`../benchmarks/commons-lang-defects4j.json`](../benchmarks/commons-lang-defects4j.json)
contains five real Defects4J Lang bug reports (LANG-747, LANG-693, LANG-865,
LANG-857, and LANG-818). Each task is pinned to its buggy commit and its
fix commit; the gold method is the method changed by the published fix
patch. The task file records the issue, source revisions, patch URL, and
label rationale. Labels were checked against the patch and against the
parser's method IDs. This is a small pilot set, not a representative
benchmark, and the five tasks do not include change-impact gold labels.

Prepare isolated checkouts without touching another clone or workspace
data:

```powershell
python scripts\prepare_commons_lang_benchmark.py --workdir "$env:TEMP\codelens-lang-defects4j"
```

Build and test the parser:

```powershell
$env:MAVEN_OPTS = "-Xms32m -Xmx384m -XX:ActiveProcessorCount=2"
mvn -f parser-service\pom.xml -q -T 1 -DforkCount=0 package
```

For greater cross-project resolution, set `PARSER_CLASSPATH` in the backend
environment to the host-platform path-separated list of dependency JARs
(and source directories) used by the checked-out project. For Maven
projects, build it from that checkout with
`mvn dependency:build-classpath -Dmdep.outputFile=<path>` and append any
compiled project source roots if relevant. The parser automatically
discovers `src/main/java`, `src/test/java`, and `src/integrationTest/java`
roots, including nested modules. JDK and in-repository source roots are
always configured. A missing or unreadable classpath entry is an explicit
parser error, not a silent fallback.

Prepare and index the five revisions with the benchmark harness. It uses a
fresh temporary index directory and **explicitly disables remote Qdrant URL
and API-key settings**, so benchmark collections are local-only. Each
repository is indexed in a separate sequential worker to release native
ONNX/JVM memory between tasks. Indexing does not call an LLM:

```powershell
python scripts\prepare_commons_lang_benchmark.py --workdir "$env:TEMP\codelens-lang-defects4j"
cd backend
python ..\scripts\index_commons_lang_benchmark.py --workdir "$env:TEMP\codelens-lang-defects4j"
```

The index report records the exact commit, source-file count, parsed symbols,
indexing duration, and any unparsed files. Any unparsed file fails that
repository's indexing status; do not use an incomplete index for evaluation.
The parser is launched with a bounded 512 MB heap by default. Embeddings are
encoded in small CPU batches to cap ONNX memory use.

## Retrieval-only run

This compares retrieval against gold methods without any model request.
It reports retrieval precision, recall, F1, hit rate, Recall@1/5/10, MRR, estimated prompt
tokens, estimated reduction, and retrieval/context latency. Actual
provider-reported input tokens are null by design:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --retrieval-only --include-ablations --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --output ..\benchmarks\results\commons-lang-retrieval-only.json
```

The pinned primary Groq model returned an 8,000-token-per-minute ceiling
and a 200,000-token-per-day quota during this run. Its configured output
allowance is 2,048 tokens. The full-prompt 5,000-token local-estimator cap
was therefore used for all conditions and requests were paced. This does
not modify provider configuration:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --include-ablations --token-budget 5000 --request-delay-seconds 60 --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --output ..\benchmarks\results\commons-lang-groq-5000.json
```

## Full model comparison

The Groq run sends the same issue query and system prompt to the pinned Groq
model for each task and condition. Keep a common `--token-budget` for all
conditions and pace requests to the configured account's TPM limit:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --include-ablations --token-budget 5000 --request-delay-seconds 60 --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --output ..\benchmarks\results\commons-lang-groq-5000.json
```

Run this only in an environment where an authorized provider may receive
the prompts and retrieved source context. Provider keys are read from the
backend environment and must not be added to task files, command history,
or results. If the LLM client resolves to the offline mock, the full runner
refuses to run. A provider must return usage metadata to report actual
input-token counts; estimates are never substituted.

If quota interrupts the full matrix, do **not** rerun the full comparison.
The resume helper inspects the saved result file, runs at most one missing
or failed condition, and atomically saves JSON/CSV after that call. To use
an available Groq fallback model, verify it is supported by the account
first, then pass its exact ID; this task used
`qwen/qwen3.8-27b` after the primary model's TPD limit:

```powershell
python ..\scripts\resume_groq_benchmark.py --tasks ..\benchmarks\commons-lang-defects4j.json --results ..\benchmarks\results\commons-lang-groq-5000.json --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --token-budget 5000 --request-delay-seconds 60 --model qwen/qwen3.8-27b
```

Repeat only while the report shows pending rows. The resume helper stops
after a provider rate-limit response and preserves the failed response
details in the report.

The CLI can also use a single indexed repository for tasks without
`repo_id`, for a one-revision experiment:

```powershell
python scripts\evaluate_retrieval.py --repo-id my-indexed-repo --tasks path\to\tasks.json --include-ablations
```

## Conditions and protocol

| Condition | Retrieval and context |
|---|---|
| `whole_file` | BM25-style lexical ranking of source files, then whole-file packing in rank order under the shared excerpt-token budget. |
| `semantic_only` | Semantic top-K method candidates, packed under that same excerpt-token budget. |
| `acor` | Normal semantic/symbol/call/dependency candidate generation and configured ranking under the same budget. |
| `acor_no_semantic`, `acor_no_symbol`, `acor_no_call_graph`, `acor_no_dependency` | ACOR scoring ablations; candidate generation remains fixed and the named score weight is zeroed and remaining weights renormalized. |
| `acor_greedy`, `acor_exact` | Same ACOR candidates and scores with alternative selection algorithms. Exact selection may fall back above the configured candidate ceiling. |

The whole-file baseline obeys the same total estimated input-prompt budget
as semantic-only and ACOR. The cap includes the system prompt, task
instructions, task metadata, file/method headers, and retrieved context.
Because the provider tokenizer may differ, record provider-reported input
tokens separately and do not treat local estimates as exact provider usage.

All conditions report retrieval precision/recall/F1, Recall@1/5/10, hit,
and MRR against the task's gold method IDs. The full model run additionally
reports bug-localization hit and exact-set accuracy; change-impact tasks
also get model precision/recall/F1. Actual provider input tokens,
completion tokens, provider latency, retrieval latency, total condition
latency, and token reduction against whole-file are recorded only where
usage is returned. Estimated token counts are separate fields. Whole-file
prompts are not silently truncated mid-file; oversized files are skipped
and smaller ranked files are considered next.

## Results and limitations

Final generated reports are in
[`../benchmarks/results/commons-lang-final.md`](../benchmarks/results/commons-lang-final.md),
with per-row data in
[`../benchmarks/results/commons-lang-final-results.csv`](../benchmarks/results/commons-lang-final-results.csv)
and condition aggregates in
[`../benchmarks/results/commons-lang-final-conditions.csv`](../benchmarks/results/commons-lang-final-conditions.csv).

This run indexed all five pinned revisions successfully: 537 production
Java files, 11,060 methods, 0 unparsed files; mean indexing time was 71.44 s
per revision. Retrieval-only executed all 45 task-condition rows without an
LLM. The Groq-generation matrix also completed all 45 rows with no final
failures. The primary model was `openai/gpt-oss-120b`; six LANG-818 ACOR
ablation calls used the account-supported Groq model `qwen/qwen3.8-27b`
after the primary model reached its TPD quota. No provider was changed.
Those six task-level ablation generations are model-confounded; the main
whole-file, semantic-only, and ACOR rows all used the same primary model.

At the five-task aggregate, retrieval-only whole-file / semantic-only / ACOR
Recall@10 was **0.4 / 0.0 / 0.2**, with MRR **0.267 / 0.0 / 0.218**.
Generation-evaluation retrieval metrics gave Recall@10 **0.4 / 0.0 / 0.2**
and MRR **0.267 / 0.0 / 0.218** for those same conditions. ACOR did not
outperform the whole-file baseline on these retrieval measures; semantic-only
retrieval failed to include the gold method in these contexts at this budget.
The ACOR `no_semantic` ablation reached Recall@10 0.6/MRR 0.45 and `exact`
reached 0.4/0.418, but the set is too small to conclude these variants are
better.

The model's exact-set localization accuracy after unambiguous short-class
name normalization was **0.4 whole-file / 0.0 semantic-only / 0.4 ACOR**.
Mean provider-reported input tokens were **4,985 / 4,396 / 4,408**, and mean
provider latency **1,902 / 2,022 / 2,625 ms**, respectively. ACOR reduced
retrieval latency to 89 ms on average versus 1,189 ms for whole-file, but
end-to-end provider latency was higher than whole-file in this sample.
Actual tokens show approximately **11.7%** paired reduction for ACOR versus
whole-file; this is a measured usage comparison, not evidence of improved
accuracy.

The 5,000-token cap was enforced using the local `tiktoken` estimate. Groq
reported more than 5,000 input tokens in two completed whole-file requests
(5,035 and 5,136). Thus the 5,000 value is an estimate-budget, not a hard
provider-token ceiling; no completed requests were rerun. The data contain
only five bug-localization tasks, one gold method per task, and no
change-impact labels. No significance test or generalization claim is
supported. Overall, these measurements **do not establish ACOR superiority
or effectiveness**; they show a retrieval-latency reduction and token
reduction alongside mixed/same-or-lower localization metrics.

## Targeted retrieval after class-anchor/BM25 changes

The earlier no-LLM targeted retrieval run, using the same five tasks and
5,000-token estimated budget after the LANG-818 class-anchor expansion and
LANG-857 BM25 scoring change, measured candidate coverage **5/5**,
Recall@10 **0.8**, and MRR **0.6538**. Gold ranks were 1, 1, 1, 4, and 53
for LANG-747, LANG-693, LANG-865, LANG-857, and LANG-818. Estimated prompt
tokens were 4,376, 4,297, 4,565, 4,356, and 3,730. LANG-818 remained
outside the top ten.

The previous saved ACOR report has Recall@10 0.2 and MRR 0.2182. This is a
measured retrieval-only change on the five-task pilot, not an LLM answer
quality result or a general superiority claim. Per-task latency was not
retained by the earlier run; it is left null in the new targeted report
rather than inferred. The values and provenance are in
[`../benchmarks/results/commons-lang-targeted-retrieval-after-anchor-bm25-5000.md`](../benchmarks/results/commons-lang-targeted-retrieval-after-anchor-bm25-5000.md)
and its JSON/CSV siblings. The temporary state containing all five indexes
is currently absent, so this audit did not rerun the matrix.

## Validation status (2026-10-24)

- `python -m pytest tests -q` from `backend/`: **97 passed**, one
  Starlette deprecation warning. This includes both MCP tools over the
-  MCP SDK client/server transport; `tests\test_mcp_server.py` separately
  includes a spawned stdio server process and client for both query tools.
- Focused API/MCP/prompt command:
  `python -m pytest tests\test_verification_batch.py tests\test_mcp_server.py tests\test_prompts.py -q`
  — **22 passed** before the spawned-process test was added. The final full
  suite above includes the spawned-process test.
- `$env:MAVEN_OPTS="-Xms32m -Xmx384m -XX:ActiveProcessorCount=2"; mvn
  -f parser-service\pom.xml -q -T 1 -DforkCount=0 test`: **passed, 3 parser
  tests**. The labelled fixture has 6 expected
  in-repository edges and 6 returned: precision **6/6 = 100%**, recall
  **6/6 = 100%**. Two resolved JDK calls were correctly excluded.
- The five gold method IDs were each present when parsing the individual
  affected source file from its Defects4J buggy revision; these targeted
  parses each reported 0 unparsed files. This checks label spelling and
  source-level parseability only, not whole-project retrieval quality.
- The stored `commons-lang-818-demo` index loaded via a local TestClient
  status request from `backend/`: **200, indexed=true, 2,151 methods, 155
  classes**. The live Swagger process was not available, so the user's
  separate process working directory/configuration remains unverified.
- The stored LANG-818 pickle graph has 2,143 call nodes/1,866 edges and 155
  dependency nodes/135 edges; these are graph storage counts, not call-graph
  precision/recall. The labelled Java fixture remains 6/6 precision and
  6/6 recall.
- The API and MCP responses now preserve a string answer while adding
  validated structured output, selected-source evidence, estimates, and
  provider usage fields when supplied. Malformed structured output and
  provider failures are explicit; provider errors no longer produce a
  success-shaped answer.
- `python scripts\prepare_commons_lang_benchmark.py --workdir
  "$env:TEMP\codelens-commons-lang-script-validation"` completed and
  prepared all five pinned buggy-revision worktrees. Those temporary
  checkouts were validation-only and are not benchmark results.
- Initial Groq requests at the 8,000-token estimated budget were rejected
  with HTTP 413 because requested input plus completion exceeded the observed
  8,000 TPM tier. At 5,000, the primary model later hit its 200,000 TPD
  quota. The six remaining LANG-818 ablation calls succeeded on the
  explicitly requested Groq model `qwen/qwen3.8-27b`; their model identity
  is recorded per row.
- Retrieval JSON/CSV and generation JSON/CSV preserve provider usage,
  latencies, error history, raw predictions, and canonicalized predictions.
  Final Markdown/JSON/CSV are generated by
  `python scripts\summarize_commons_lang_benchmark.py`.

To complete the comparison, use a machine where the full Lang parser/index
fits available memory and either a working local inference service or an
authorized provider endpoint. Follow the checkout/index steps above, run
the retrieval-only command first, and then the full model command. A real
container engine is required for Docker validation. The IDE check can be
performed after the MCP client/server test on an editor that supports the
documented stdio MCP configuration.

The full five-task index state under the temporary directory described by
the command examples is not present in the current workspace. Only local
indexes such as `backend\data\index\commons-lang-818-demo.pkl` remain. No
new five-task retrieval matrix was run or written during the API/MCP audit;
existing `commons-lang-final` reports were preserved.

The old parser "13%" figure should not be called recall. The parser source
defines its graph-signal percentage as in-repository edges divided by all
visited method-call expressions, which includes external and unresolved
calls. The separate name/arity signal is a heuristic, not labelled ground
truth. See [`ACOR_LANG818_ROOT_CAUSE_VALIDATION.md`](ACOR_LANG818_ROOT_CAUSE_VALIDATION.md),
[`ACOR_GRAPH_VERIFICATION.md`](ACOR_GRAPH_VERIFICATION.md), and
[`ACOR_REMAINING_WORK_AUDIT.md`](ACOR_REMAINING_WORK_AUDIT.md) for the
source and protocol details.
