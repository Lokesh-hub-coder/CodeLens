# Apache Commons Lang ACOR benchmark

- Tasks: 5 pinned Defects4J bug-localization tasks.
- Indexing: 5/5 revisions indexed; 537 Java production source files, 11060 methods, 0 unparsed files.
- Mean indexing time: 71.44 s per revision.
- Retrieval-only: retrieval_only_no_model_requests; no LLM calls.
- Generation: Groq primary `openai/gpt-oss-120b`; models used: openai/gpt-oss-120b, qwen/qwen3.8-27b.
- Input budget: 5000 local-estimator tokens for every condition; provider-reported usage is reported separately.
- Generation outcomes: 45/45 successful rows; 0 failed.

## Results by condition

| Condition | Success | Recall | R@1 | R@5 | R@10 | MRR | Model hit | Exact accuracy | Actual input tokens | Token reduction | Provider latency ms | Retrieval ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| whole_file | 5/5 | 0.400 | 0.200 | 0.400 | 0.400 | 0.267 | 0.400 | 0.400 | 4985.0 | 0.0% | 1901.6 | 1189.0 |
| semantic_only | 5/5 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 4396.4 | 11.9% | 2021.7 | 57.2 |
| acor | 5/5 | 0.400 | 0.200 | 0.200 | 0.200 | 0.218 | 0.400 | 0.400 | 4408.2 | 11.7% | 2624.9 | 88.8 |
| acor_no_semantic | 5/5 | 0.600 | 0.400 | 0.600 | 0.600 | 0.450 | 0.600 | 0.600 | 4837.6 | 2.9% | 2549.1 | 84.5 |
| acor_no_symbol | 5/5 | 0.400 | 0.200 | 0.200 | 0.200 | 0.218 | 0.400 | 0.400 | 4685.2 | 6.1% | 2190.2 | 121.8 |
| acor_no_call_graph | 5/5 | 0.400 | 0.200 | 0.200 | 0.200 | 0.218 | 0.400 | 0.400 | 4569.6 | 8.4% | 2273.6 | 98.6 |
| acor_no_dependency | 5/5 | 0.400 | 0.200 | 0.200 | 0.200 | 0.212 | 0.400 | 0.400 | 4478.6 | 10.3% | 2761.4 | 84.8 |
| acor_greedy | 5/5 | 0.400 | 0.200 | 0.200 | 0.200 | 0.218 | 0.400 | 0.400 | 4478.6 | 10.3% | 2452.7 | 171.0 |
| acor_exact | 5/5 | 0.600 | 0.400 | 0.400 | 0.400 | 0.418 | 0.600 | 0.600 | 4781.2 | 4.1% | 2720.7 | 123.2 |

## Interpretation

These are descriptive measurements on five tasks, not evidence of broad superiority. Retrieval Recall@K/MRR evaluates whether the labelled method was included and ranked; model hit/exact accuracy evaluates the generated answer after resolving unambiguous short class names against methods actually retrieved. The full prompt budget used local estimates. Provider usage exceeded 5,000 tokens in 2 completed rows, demonstrating estimator/provider-tokenizer mismatch.

Six LANG-818 ACOR signal/ranker ablation generations used `qwen/qwen3.8-27b` after the primary Groq model hit its daily token quota. Thus primary baseline conditions use the same model, while LANG-818 ablation generation comparisons are model-confounded. The retrieval-only ablations remain same-system and GPU-free.

The dataset has no change-impact labels, and no statistical significance or generalization claim is supported. Retrieval gains, if present, do not imply model-generation accuracy gains.

## Reproduction

```powershell
cd backend
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --retrieval-only --include-ablations --token-budget 5000 --storage-dir <isolated-state> --indexing-results <index-report> --output ..\benchmarks\results\commons-lang-retrieval-only-5000.json
python ..\scripts\evaluate_retrieval.py --tasks ..\benchmarks\commons-lang-defects4j.json --include-ablations --token-budget 5000 --request-delay-seconds 60 --storage-dir <isolated-state> --indexing-results <index-report> --output ..\benchmarks\results\commons-lang-groq-5000.json
python ..\scripts\summarize_commons_lang_benchmark.py
```

## Output files

- Summary JSON: `C:\Users\singh\Desktop\CodeLens\CodeLens\benchmarks\results\commons-lang-final.json`
- Per-task CSV: `C:\Users\singh\Desktop\CodeLens\CodeLens\benchmarks\results\commons-lang-final-results.csv`
- Condition summary CSV: `C:\Users\singh\Desktop\CodeLens\CodeLens\benchmarks\results\commons-lang-final-conditions.csv`
