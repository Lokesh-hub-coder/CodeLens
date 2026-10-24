# ACOR experiment protocol

## Research question

On a small, patch-labelled Java bug-localization pilot, does the existing
graph- and symbol-aware ACOR retrieval pipeline recover fixed methods
within a shared estimated input-token budget better than whole-file or
semantic-only retrieval? Do ACOR signals or selection strategies change
retrieval or model outcomes?

This is an exploratory protocol, not a claim of benchmark superiority.

## Dataset and labels

The current task file,
[`../benchmarks/commons-lang-defects4j.json`](../benchmarks/commons-lang-defects4j.json),
contains five Apache Commons Lang Defects4J bug tasks:
LANG-747, LANG-693, LANG-865, LANG-857, and LANG-818. Each task is pinned
to a buggy revision and a fix revision. Gold method IDs are anchored to the
upstream fix patch. The current labels support bug localization; they do
not provide independently annotated change-impact gold sets.

This is a five-task pilot. Revisions, labels, and task selection are
potentially correlated; results must be reported per task as well as in
aggregate.

## Conditions

- `whole_file`: rank files lexically and pack whole files.
- `semantic_only`: retrieve semantic method candidates and pack within
  budget.
- `acor`: normal semantic, symbol, call-graph and dependency-graph
  candidate/ranking pipeline.
- `acor_no_semantic`, `acor_no_symbol`, `acor_no_call_graph`,
  `acor_no_dependency`: zero the named score weight and renormalize other
  weights while holding candidate generation fixed.
- `acor_greedy`, `acor_exact`: same ACOR candidates and scores, alternative
  context selection algorithms.

For a fair comparison, use identical task prompt, system prompt, model,
task revision, index, and estimated input budget. Retrieval-only tests
must not call an LLM. Keep provider-reported token counts separate from
estimates. Record model identity per row; do not aggregate model-generated
answers as if models were the same when a fallback was used.

## Outcomes

Report:

- retrieval precision, recall, F1, hit rate, Recall@1/5/10, and MRR against
  the labelled method set;
- model localization hit rate and exact-set accuracy, separately from
  retrieval metrics;
- actual provider input/output tokens and total tokens only when supplied
  by the provider; otherwise report null, not an estimate in that field;
- estimated input tokens, paired token reduction against whole-file,
  retrieval latency, provider latency, total condition latency, and
  failures;
- indexing wall time and unparsed-file counts per repository.

No change-impact precision/recall can be reported until a separate,
independently labelled change-impact dataset exists.

## Saved pilot results

The existing full report is
[`../benchmarks/results/commons-lang-final.md`](../benchmarks/results/commons-lang-final.md)
with row data in its adjacent JSON and CSV files. It contains 45/45
successful task-condition rows. The primary model was
`openai/gpt-oss-120b`; some ACOR ablation generations used
`qwen/qwen3.8-27b` after primary-model quota limits. Main whole-file,
semantic-only, and ACOR rows used the primary model for all five tasks.

The saved aggregate retrieval Recall@10 was 0.4 whole-file, 0.0
semantic-only, and 0.2 ACOR; MRR was 0.267, 0.0, and 0.218, respectively.
This pilot does not show ACOR outperforming whole-file retrieval. The
results are not a replacement for a larger, preregistered Java benchmark.

## Reproduction commands

From repository root, prepare pinned worktrees:

```powershell
python scripts\prepare_commons_lang_benchmark.py --workdir "$env:TEMP\codelens-lang-defects4j"
```

Build the parser and index the five tasks from `backend/`:

```powershell
$env:MAVEN_OPTS = "-Xms32m -Xmx384m -XX:ActiveProcessorCount=2"
mvn -f ..\parser-service\pom.xml -q -T 1 -DforkCount=0 package
python ..\scripts\index_commons_lang_benchmark.py --workdir "$env:TEMP\codelens-lang-defects4j"
```

Run retrieval only before any model call:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --retrieval-only --include-ablations --token-budget 5000 --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --output ..\benchmarks\results\commons-lang-retrieval-only-new.json
```

Only run generation if authorized, correctly configured, and within
provider quota. Save to a new file, not over an existing report:

```powershell
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --include-ablations --token-budget 5000 --request-delay-seconds 60 --storage-dir "$env:TEMP\codelens-lang-defects4j\state" --indexing-results "$env:TEMP\codelens-lang-defects4j\state\indexing-results.json" --output ..\benchmarks\results\commons-lang-groq-new.json
```

Check missing index prerequisites before running. The five-repository
temporary index directory was not present during the current audit. The
previous targeted retrieval-only result after class-anchor/BM25 changes is
preserved at
[`../benchmarks/results/commons-lang-targeted-retrieval-after-anchor-bm25-5000.md`](../benchmarks/results/commons-lang-targeted-retrieval-after-anchor-bm25-5000.md);
no new matrix was run during this audit.
