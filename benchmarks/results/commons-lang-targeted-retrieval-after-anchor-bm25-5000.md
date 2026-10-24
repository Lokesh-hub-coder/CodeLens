# Targeted Commons Lang retrieval result

**Mode:** retrieval only; zero LLM/provider calls  
**Estimated prompt budget:** 5,000 tokens  
**Run provenance:** measurements from the completed targeted run recorded
in this session. The original console output did not retain latency values or
a machine-readable artifact; the result values below are transcribed and
marked accordingly in the adjacent JSON. No missing measurements were
estimated.

| Task | Candidates | Gold covered | Gold rank | Recall@10 | Estimated prompt tokens |
|---|---:|:---:|---:|---:|---:|
| LANG-747 | 94 | yes | 1 | 1 | 4,376 |
| LANG-693 | 98 | yes | 1 | 1 | 4,297 |
| LANG-865 | 55 | yes | 1 | 1 | 4,565 |
| LANG-857 | 64 | yes | 4 | 1 | 4,356 |
| LANG-818 | 161 | yes | 53 | 0 | 3,730 |

Aggregate candidate coverage is **5/5**, Recall@10 is **0.8**, and MRR is
**0.6538**. LANG-818 remains outside the top ten. The previous saved ACOR
baseline in `commons-lang-final.json` has Recall@10 **0.2** and MRR
**0.2182** on the same five-task pilot. The difference is a retrieval-only
measurement after class-anchor expansion and BM25 source scoring; it does
not show a generation-quality improvement or general superiority.

Latency and provider input tokens are blank because this no-LLM run's
recorded output did not preserve them. The reported prompt token values are
estimates, not provider-billed counts.
