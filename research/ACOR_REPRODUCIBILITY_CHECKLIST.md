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
- Existing raw indexes available locally include
  `backend/data/index/commons-lang-818-demo.pkl`, but the five-task
  temporary state directory is absent. A new full five-task retrieval
  matrix is therefore blocked until indexing is reproduced.
