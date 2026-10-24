# ACOR: graph-aware, token-budgeted context retrieval for Java bug localization

**Status:** preliminary internal draft; not a publication-ready paper.

## Abstract

Repository question answering requires selecting code from a larger
codebase than can fit in a model context. CodeLens implements ACOR, a
method-level retrieval and ranking pipeline that combines semantic
similarity, symbol overlap, call-graph proximity, and dependency-graph
proximity under a formatted-context token budget. We evaluate the existing
implementation on five patch-labelled Apache Commons Lang Defects4J bug
tasks using whole-file, semantic-only, and ACOR retrieval, plus signal and
selection ablations. The saved pilot reports Recall@10 of 0.4, 0.0, and
0.2, respectively, and MRR of 0.267, 0.0, and 0.218. ACOR therefore does
not outperform whole-file retrieval on these retrieval measures. Its
paired provider-reported input usage is lower than whole-file in this
sample, but estimated budgets did not perfectly predict provider token
counts. With five tasks, some model-confounded ablation generations, and
no change-impact gold labels, these observations are exploratory and do
not support general effectiveness claims.

## Method

The system parses Java source into methods, classes, in-repository calls,
and import-based class dependencies; stores method vectors in Qdrant; and
uses symbol and graph structures alongside semantic retrieval. ACOR
assigns composite scores using configured weights, then selects a set of
formatted method snippets subject to a locally estimated token budget.
Whole-file and semantic-only are comparison conditions. Ablations disable
one ACOR score signal at a time, and `greedy`/`exact` compare selection
strategies.

The current HTTP and MCP query surfaces use a typed JSON answer model,
source evidence derived from selected methods, and provider usage fields
when returned. Model-generated facts are still dependent on the retrieved
excerpt and must not be treated as source-grounded unless the cited
evidence supports them.

## Experimental evidence

The five-task saved pilot uses LANG-747, LANG-693, LANG-865, LANG-857, and
LANG-818, each pinned to buggy and fix revisions with gold methods anchored
to fix patches. All 45 task-condition rows completed in the saved
generation report. The primary model was `openai/gpt-oss-120b`; six
ablation generations used `qwen/qwen3.8-27b` after quota limits. The main
whole-file, semantic-only, and ACOR conditions used the primary model on
all five tasks.

| Condition | Recall@10 | MRR | Mean actual input tokens | Mean provider latency |
|---|---:|---:|---:|---:|
| Whole-file | 0.4 | 0.267 | 4,985 | 1,902 ms |
| Semantic-only | 0.0 | 0.000 | 4,396 | 2,022 ms |
| ACOR | 0.2 | 0.218 | 4,408 | 2,625 ms |

The fixed five-task set yields coarse recall increments of 0.2. ACOR
reduced paired actual input usage by approximately 11.7% relative to
whole-file, but whole-file had higher Recall@10 and MRR, and ACOR's
provider latency was higher in this sample. Groq reported more than the
5,000-token local estimate for two whole-file requests (5,035 and 5,136).
Thus 5,000 was an estimator constraint, not a hard provider-token bound.

The `acor_no_semantic` condition had retrieval Recall@10 0.6 and MRR 0.45;
`acor_exact` had 0.4 and 0.418. Other ablations were lower or tied. These
small-sample observations are not evidence that removing semantic scoring
or using exact selection is generally preferable. Retrieval metrics do not
depend on the generation model, but model answer scores for fallback-model
rows are confounded.

Source: [`../benchmarks/results/commons-lang-final.md`](../benchmarks/results/commons-lang-final.md)
and its JSON/CSV companions.

After targeted class-anchor expansion and BM25 source scoring, a separate
retrieval-only run on the same five tasks at the same estimated budget
measured Recall@10 0.8 and MRR 0.6538, with gold ranks 1, 1, 1, 4, and 53.
This is evidence that the retrieval changes improved top-10 localization
on this specific set, while LANG-818 still ranked outside the top ten.
Per-task retrieval latency was not retained. This run did not call a model
and does not revise the saved generation results above.

## Limitations and next steps

- Five tasks and one gold method per task do not support statistical or
  broad cross-project conclusions.
- There are no independently labelled change-impact targets.
- Full Lang parsing/indexing has encountered memory/time constraints in
  prior attempts; the saved pilot's five successful indexes are evidence
  only for that run's recorded environment.
- The parser's six-edge labelled fixture is a unit-scale measurement, not
  project-wide call-graph recall.
- Local token estimates differ from provider tokenization.
- Provider quota caused a model switch in some ablations.
- No compatible IDE UI session or production deployment was evaluated.

Next work should expand independently annotated Java tasks, publish every
revision/label/index fingerprint, compare with paired model and prompt
conditions, and evaluate token limits using provider-native tokenization
where available.
