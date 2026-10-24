# Related work

This short list is verified against the linked paper records. It is
context for a preliminary study, not an exhaustive literature review.

1. **RepoBench: Benchmarking Repository-Level Code Auto-Completion
   Systems.** Liu et al., arXiv:2306.03091 (2023). RepoBench separates
   retrieval, completion, and pipeline evaluation, and includes Java as
   well as Python. It motivates evaluating retrieval separately from the
   final generation stage. [Paper](https://arxiv.org/abs/2306.03091)

2. **SWE-bench: Can Language Models Resolve Real-World GitHub Issues?**
   Jimenez et al., arXiv:2310.06770 (2023; ICLR 2024). SWE-bench evaluates
   issue resolution against real repository tasks and emphasizes
   multi-file reasoning. CodeLens's five method-localization labels are
   narrower: they are not patch-resolution tasks. [Paper](https://arxiv.org/abs/2310.06770)

3. **CodeRAG-Bench: Can Retrieval Augment Code Generation?** Wang et al.,
   arXiv:2406.14497 (2024, revised 2025). The benchmark spans multiple
   code-generation settings and reports that useful retrieval under
   limited lexical overlap remains challenging. This supports measuring
   retrieval quality directly; it does not establish that a graph-aware
   method will improve it. [Paper](https://arxiv.org/abs/2406.14497)

4. **Agentless: Demystifying LLM-based Software Engineering Agents.** Xia
   et al., arXiv:2407.01489 (2024). The work describes a simple
   localization/repair/validation pipeline and discusses dataset label
   quality. It is relevant to CodeLens's localization stage, but its
   reported end-to-end repair outcomes are not directly comparable to
   retrieval metrics. [Paper](https://arxiv.org/abs/2407.01489)

## Scope relative to this work

CodeLens evaluates a deterministic, budget-bounded Java context retrieval
stage with semantic, symbol, call-graph, and dependency signals. The
current five-task pilot is too small to claim novelty, generality, or
superiority over these broader benchmarks and systems. A defensible
follow-up requires a larger Java dataset, patch-grounded labels reviewed
independently, and common provider/model conditions.
