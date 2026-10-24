# Commons Lang retrieval-only rerun

**Mode:** retrieval-only; no LLM/provider requests
**Tasks:** five pinned Apache Commons Lang Defects4J bug-localization tasks
**Budget:** 5,000 locally estimated prompt tokens per task/condition
**Conditions:** whole-file, semantic-only, ACOR, four signal ablations, greedy, exact
**Rows:** 45/45 successful; 0 failures
**Provider-reported input tokens:** not applicable; no provider was invoked

## Reproduction

The five pinned checkouts were verified against
`benchmarks/commons-lang-defects4j.json`. Each repository was indexed
serially into isolated temporary storage with the existing worker command;
all five runs reported zero unparsed Java files. The Qdrant collections and
index pickles were copied into a fresh combined temporary state, with source
and destination vector counts checked for every collection.

From the repository root, with the combined state still present under
`%TEMP%`:

```powershell
$state = Join-Path $env:TEMP "codelens-final-index-recovery\combined-state"
python scripts\evaluate_retrieval.py `
  --tasks benchmarks\commons-lang-defects4j.json `
  --retrieval-only `
  --include-ablations `
  --token-budget 5000 `
  --storage-dir $state `
  --indexing-results (Join-Path $state "indexing-results.json") `
  --output benchmarks\results\commons-lang-final-completion-retrieval-only-5000.json `
  --csv-output benchmarks\results\commons-lang-final-completion-retrieval-only-5000.csv
```

The task IDs and buggy revisions are `Lang-1/LANG-747` /
`396afc3e4693cfee182efe582455f2d97058c068`,
`Lang-3/LANG-693` / `64cfee77e333d0c31a0fde0abe6dac3d97b0f078`,
`Lang-5/LANG-865` / `bc255ccf5c239666ab54e5a31720d3f482ae78eb`,
`Lang-6/LANG-857` / `f5a83bb90cf7b318ac72823e6b99d01d060abe41`, and
`Lang-8/LANG-818` / `cfab1e4fa19755d65de7b81206d86f9e98622d8e`.

## Indexing results

| Task | Java source files | Parsed files | Methods | Call edges | Unparsed files | Index time (s) |
|---|---:|---:|---:|---:|---:|---:|
| LANG-747 | 108 | 103 | 2,242 | 2,212 | 0 | 82.72 |
| LANG-693 | 108 | 103 | 2,235 | 2,201 | 0 | 82.65 |
| LANG-865 | 108 | 103 | 2,221 | 2,181 | 0 | 82.87 |
| LANG-857 | 107 | 102 | 2,211 | 2,166 | 0 | 81.58 |
| LANG-818 | 106 | 101 | 2,151 | 2,127 | 0 | 77.94 |

Total: 537 Java source files, 11,060 methods, zero unparsed files, and
81.55 seconds mean indexing time per repository. These reattempts succeeded
after the earlier temporary workers had reported write timeouts or an
ONNX memory allocation failure. The prior failure cause was not isolated;
no parser or embedding code was changed as part of this recovery.

## Aggregate results

Retrieval metrics are computed from methods retained in the formatted,
budgeted context. `Recall@k` and MRR therefore reflect the selected context,
not just candidate-pool availability. Token counts are local estimates
including the evaluation system/task framing; actual provider usage is null
because no model request was made. Retrieval and total condition latency
were measured locally.

| Condition | Success | Context coverage | Recall@1 | Recall@5 | Recall@10 | MRR | Mean estimated tokens | Est. reduction vs whole-file | Mean retrieval ms | Mean total ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Whole-file | 5/5 | 2/5 | 0.20 | 0.40 | 0.40 | 0.2667 | 4,814.6 | 0.0% | 664.4 | 666.8 |
| Semantic-only | 5/5 | 1/5 | 0.20 | 0.20 | 0.20 | 0.2000 | 4,259.4 | 11.6% | 59.7 | 61.9 |
| ACOR | 5/5 | 5/5 | 0.60 | 0.80 | 0.80 | 0.6538 | 4,264.2 | 11.5% | 85.7 | 87.8 |
| ACOR without semantic | 5/5 | 4/5 | 0.60 | 0.80 | 0.80 | 0.6500 | 4,305.8 | 10.6% | 84.9 | 87.1 |
| ACOR without symbol | 5/5 | 5/5 | 0.60 | 0.80 | 0.80 | 0.6537 | 4,294.2 | 10.8% | 86.6 | 88.7 |
| ACOR without call graph | 5/5 | 4/5 | 0.40 | 0.60 | 0.60 | 0.4538 | 4,200.4 | 12.8% | 83.9 | 85.9 |
| ACOR without dependency graph | 5/5 | 5/5 | 0.60 | 0.80 | 0.80 | 0.6537 | 4,274.8 | 11.2% | 87.7 | 89.9 |
| ACOR greedy | 5/5 | 5/5 | 0.60 | 0.80 | 0.80 | 0.6538 | 4,264.2 | 11.5% | 85.7 | 87.9 |
| ACOR exact | 5/5 | 3/5 | 0.20 | 0.40 | 0.40 | 0.2537 | 4,174.4 | 13.4% | 146.4 | 148.5 |

## Per-task ACOR localization

The full-candidate rank is measured before budget selection by replaying
the saved candidate/scoring pipeline. Context rank is the position in the
persisted, token-budgeted result. Candidate-pool coverage was 5/5.

| Task | Candidate count | Gold candidate rank | Gold context rank | Estimated prompt tokens | Retrieval ms |
|---|---:|---:|---:|---:|---:|
| LANG-747 | 94 | 1 | 1 | 4,373 | 83.6 |
| LANG-693 | 98 | 1 | 1 | 4,297 | 97.0 |
| LANG-865 | 55 | 1 | 1 | 4,565 | 62.9 |
| LANG-857 | 64 | 4 | 4 | 4,356 | 72.9 |
| LANG-818 | 161 | 62 | 53 | 3,730 | 112.1 |

## LANG-818 ranking diagnosis

The gold method was present in the candidate pool but ranked 62nd before
budget selection and 53rd in the final context. Its score breakdown was
semantic `0.8357`, call graph `0.0`, dependency graph `0.0`, symbol `0.5`,
composite `0.459261`. The top ten were `FastDateFormat` methods with
composite scores from `0.7484` to `0.7705`; they received dependency and
symbol scores of `1.0`. The class-only query resolves `FastDateFormat` as
a class anchor and no method anchors. The gold method has no incoming or
outgoing call-graph edge in this index, and its nested
`FastDatePrinter.TimeZoneNameRule` class has no dependency path to
`FastDateFormat`. Candidate generation includes the gold method through
same-package class-name similarity, but that relation is not a graph edge.

A diagnostic trial that propagated a single automatically selected
same-package related-class anchor worsened the LANG-818 full-candidate
rank from 62 to 67 and context rank from 53 to 55; it was not adopted.
No ranking change was made because the tested heuristic did not improve
the evidence-backed target and no call/dependency relationship exists to
support a non-arbitrary graph score.

The prior targeted report recorded a LANG-818 rank of 53. This rerun
retains 53 as the budgeted-context rank but measures 62 before budget
selection. The prior report does not identify its ranking stage, so the
two full-candidate ranks should not be treated as directly comparable.

## Interpretation and limits

On this five-task pilot, ACOR has higher selected-context Recall@10 and
MRR than the whole-file and semantic-only conditions in this run. Removing
the call-graph signal reduces Recall@10 from 0.8 to 0.6; removing the
dependency signal does not change aggregate Recall@10. Exact selection is
slower and has lower localization metrics than greedy selection on this
sample. These are descriptive results from five patch-labelled tasks,
not evidence of general or statistical superiority. They do not measure
LLM localization accuracy, provider tokens, change-impact precision/recall,
or production behavior.

Earlier saved generation reports and retrieval-only reports remain
unchanged. The JSON and CSV beside this file contain the complete row-level
results and indexing records.
