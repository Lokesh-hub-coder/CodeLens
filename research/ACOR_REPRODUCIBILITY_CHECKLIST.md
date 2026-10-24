# ACOR reproducibility checklist

## Before indexing

- [ ] Use the exact task file and pinned buggy revisions.
- [ ] Verify each checkout's HEAD matches its task record.
- [ ] Use a clean, isolated work directory; do not reuse another project
  clone.
- [ ] Record Java, Maven, Python, parser JAR, parser classpath, embedding
  model, and relevant CodeLens revision.
- [ ] Ensure all required parser source/classpath entries exist. Treat
  parse failures as failed indexing, never as a successful partial index.
- [ ] Save indexing output including file/method/edge counts, duration,
  unparsed paths, and fingerprint.

## Retrieval-only

- [ ] Run retrieval-only first; verify it makes no provider calls.
- [ ] Apply the same task and local estimated prompt-token budget to every
  condition.
- [ ] Count system prompt, task framing/metadata, headers, and context in
  the estimate; retain any provider tokenizer mismatch as a limitation.
- [ ] Preserve per-task gold method IDs, candidate coverage, ranks, metrics,
  context size, and latency.
- [ ] Save outputs to a new JSON/CSV path and verify the row count and
  report schema before summary generation.

## Model generation

- [ ] Use the same model, system/user prompt, temperature, and output
  allowance for all compared conditions.
- [ ] Do not switch providers. If an authorized same-provider fallback is
  necessary, write its exact model ID per row and separate those rows from
  paired primary-model comparisons.
- [ ] Capture provider-returned input/output/total tokens and latency when
  present. Leave actual usage null when not provided; do not fill it from
  local estimates.
- [ ] Save each result immediately. Resume only missing/failed rows; do not
  rerun completed rows.
- [ ] Record every error, rate limit, retry, cancellation, and failed
  condition; do not silently omit failures.

## Reporting

- [ ] Report retrieval metrics and model answer accuracy as distinct
  measures.
- [ ] Show per-task results, sample size, failures, and model mix alongside
  aggregate values.
- [ ] Call out model-confounded ablations, budget-estimator overruns, and
  missing ground truth.
- [ ] Avoid significance, generalization, production-readiness, or
  superiority claims unless the design and evidence support them.
- [ ] Preserve existing report files; write new runs under new names.

## Current workspace

- Existing full pilot: `benchmarks/results/commons-lang-final.*`, 45/45
  task-condition rows; see [`../docs/evaluation.md`](../docs/evaluation.md).
- The five pinned checkouts matched their expected buggy revisions. Five
  isolated worker re-indexes completed successfully: 537 Java source files,
  11,060 methods, 0 unparsed files, and 81.55 seconds mean indexing time.
  Earlier temporary worker failures (ONNX allocation and Qdrant write
  timeout) did not recur; their original cause was not established.
- The recovered combined state is outside the repository at
  `%TEMP%\codelens-final-index-recovery\combined-state`. Its five Qdrant
  collections and index pickles are verified; each vector point count
  matched its source collection during assembly.
- A fresh retrieval-only run completed 45/45 rows at the 5,000 estimated
  token budget with no provider calls. Results are saved under
  `benchmarks/results/commons-lang-final-completion-retrieval-only-5000.*`.
  ACOR measured selected-context Recall@10 0.8 and MRR 0.6538; this five-task
  pilot does not establish general superiority. See the Markdown report for
  ablations, per-task ranks, token estimates, and measured local latency.

## Recreate the indexes and retrieval-only report

From the repository root, after Java, Maven, Python dependencies, and the
parser JAR are available:

```powershell
$work = Join-Path $env:TEMP "codelens-lang-defects4j"
$state = Join-Path $env:TEMP "codelens-lang-final-reproduction\state"
python scripts\prepare_commons_lang_benchmark.py --workdir $work
python scripts\index_commons_lang_benchmark.py `
  --workdir $work `
  --state-dir $state `
  --tasks benchmarks\commons-lang-defects4j.json `
  --report (Join-Path $state "indexing-results.json")
python scripts\evaluate_retrieval.py `
  --tasks benchmarks\commons-lang-defects4j.json `
  --retrieval-only `
  --include-ablations `
  --token-budget 5000 `
  --storage-dir $state `
  --indexing-results (Join-Path $state "indexing-results.json") `
  --output benchmarks\results\commons-lang-reproduction-retrieval-only.json `
  --csv-output benchmarks\results\commons-lang-reproduction-retrieval-only.csv
```

The index script processes repositories serially and persists an indexing
report after each worker. Verify all five task IDs have `status: success`,
`unparsed_files: []`, and the expected revision before evaluation. The
reproduction filenames above are separate from the saved original and
final-completion reports; choose another new output basename if they
already exist. The five revision hashes and label sources are in
[`../benchmarks/commons-lang-defects4j.json`](../benchmarks/commons-lang-defects4j.json).
