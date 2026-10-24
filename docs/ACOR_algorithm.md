# ACOR: Adaptive Context Optimization and Ranking

This document is the technical deep-dive that `docs/architecture.md`
points to. It covers the scoring formula, the budget-constrained selection
problem, and -- most importantly for your literature review and viva --
exactly how ACOR relates to the six papers in the synopsis plus the two
closest pieces of prior art that aren't in the synopsis yet but almost
certainly should be.

## 1. The composite score

For a candidate code chunk `c` (one method, by default) and a developer
query `q`:

```
S(c, q) = w_sem * norm(semantic_sim(c, q))
        + w_call * call_graph_proximity(c, q)
        + w_dep  * dependency_proximity(c, q)
        + w_sym  * symbol_match(c, q)
```

- **`semantic_sim`** -- cosine similarity between the query embedding and
  the candidate's embedding (`retrieval/embeddings.py`), min-max
  normalized across the current candidate set (`acor/scorer.py`,
  `_min_max_normalize`) so it isn't drowned out by the other three terms,
  which naturally span a wider range.
- **`call_graph_proximity`** -- how close `c` sits to any method the query
  named explicitly, measured as undirected shortest-path distance in the
  call graph, decaying linearly to zero at `GRAPH_MAX_HOPS` (default 4).
- **`dependency_proximity`** -- the same decay shape, one level up, over
  the class/import dependency graph.
- **`symbol_match`** -- the maximum of method-name matching (exact identifier
  or Jaccard overlap of camelCase-split subtokens), a quarter-weighted,
  candidate-set-normalized BM25 match against method source, and class-name
  overlap for a candidate's enclosing class. Source matching helps distinguish
  overloads whose behavior is described in the method body rather than its
  name; enclosing-class matching supports related nested implementations.

Weights (`ACOR_WEIGHT_*` in `.env`, defaults 0.40 / 0.20 / 0.15 / 0.25) are
renormalized to sum to 1 and can be overridden per repository. Intent
profiles are optional and disabled by default. The benchmark runner can
zero one signal at a time while holding the task set, model, prompt, and
ACOR candidate generation constant.

**Why relevance scoring and budget selection are two separate modules**
(`scorer.py` then `ranker.py`, not one combined function): mixing "how
relevant is this" with "does it fit the budget" into a single number
would make it impossible to later ask, independently, "how good is
ACOR's *ranking*" versus "how good is ACOR's *budgeting*" -- exactly the
kind of question the synopsis' Section 7 evaluation plan (retrieval
precision, separately from token reduction) implies you'll need to
answer.

## 2. Budget-constrained selection is a knapsack problem

Once every candidate has a relevance score (the "value") and a token cost
(the "weight"), picking the subset that maximizes total relevance without
exceeding the token budget is exactly 0/1 knapsack. `acor/ranker.py`
offers two solvers:

- **Greedy by score-density** (default): sort by `score / formatted-token-cost`,
  take candidates in that order while they fit. `O(n log n)`. This is the
  standard approximation for knapsack-shaped problems and is very close
  to optimal whenever individual items are small relative to the budget --
  true here, since one method is rarely more than a few hundred tokens
  against budgets in the thousands.
- **Exact DP knapsack**: textbook `O(n * budget)` dynamic program,
  offered for small candidate sets (`EXACT_KNAPSACK_MAX_CANDIDATES`,
  default 200). Above that candidate limit, exact mode falls back to
  greedy. The solver estimates per-item formatted cost; the final rendered
  context is recounted, and lowest-ranked snippets are removed until the
  formatted context fits. This is a strict excerpt budget, not a budget for
  the system prompt or developer query.

`backend/tests/test_ranker.py` includes a constructed counterexample
where greedy provably picks a worse subset than the exact solver -- worth
walking through in the viva if asked "why offer both?": it's a real,
demonstrable trade-off, not a hedge.

## 3. Embedding model choice

The default, `BAAI/bge-small-en-v1.5`, runs through FastEmbed and is
configured for 384-dimensional vectors. It is a general text-embedding
model, not a code-specialized model; the quality of code retrieval must be
measured on the supplied benchmark tasks rather than inferred from its
name. FastEmbed is the only supported embedding backend.

## 4. How ACOR relates to the papers in your synopsis

**Lewis et al., 2020 (RAG, NeurIPS)** established the retrieve-then-
generate paradigm this whole project is an instance of: a non-parametric
retrieval step supplies a generator with external context it wasn't
trained to memorize. The original formulation retrieves passages from a
dense Wikipedia index for open-domain QA. ACOR is a domain-specialized
descendant: "documents" are code chunks, "retrieval" is not embedding-kNN
alone but a fusion of semantic similarity with two structural graphs and
a symbol index, and selection is additionally constrained by an explicit
token budget that RAG's original formulation does not model.

**Zhang et al., 2023 (RepoCoder, EMNLP)** showed that a single retrieval
pass is often not enough for repository-level tasks: their iterative
loop uses a first-pass BM25 retrieval, generates a draft completion, then
re-retrieves using that draft as the new query, because a plausible draft
completion resembles the true target more closely (lexically) than the
surrounding code context does. ACOR's current design is single-shot
(score once, select once, generate once) -- iterative re-retrieval, in
RepoCoder's style or via an explicit ReAct-style action loop (see below),
is a well-motivated Week 12+ extension, particularly for root-cause
queries where the first-pass context reveals that a *different* method is
actually where the bug originates.

**Liu et al., 2024 (RepoBench, ICLR)** is directly usable as an evaluation
asset, not just a citation: it supports both Python and Java, and its
three sub-tasks map onto exactly the pieces of ACOR you need to validate
separately -- RepoBench-R (retrieval quality) against `acor/scorer.py` +
`ranker.py`, RepoBench-C/P (completion/pipeline) as a secondary,
optional comparison point if you want a number that's directly comparable
to published baselines. Its Java support means you do not have to build
your own retrieval-quality benchmark from scratch, though you likely still
want a small hand-labeled set of *your own* bug-explanation queries, since
RepoBench's tasks are code completion, not the explanation/root-cause
tasks your synopsis targets.

**Jimenez et al., 2024 (SWE-bench, ICLR)** is the closest thing to a
target *task* for the end-to-end system (resolve a real, described issue
against a real repository), but the original benchmark's 2,294 problems
are drawn from 12 Python repositories -- it does not cover Java out of
the box. A "Multi-SWE-bench" extension covering Java, TypeScript, Go, and
Rust has since been reported in the literature; worth a search for its
current availability before committing to build your own Java-issue
dataset from scratch, since replicating SWE-bench's own harness (real
issue + PR pairs + a Docker-based pass/fail test execution environment)
is a substantial undertaking on its own.

**Wang et al., 2025 (CodeRAG-Bench, Findings of NAACL)** is the paper
that most directly motivates ACOR's design, not just its evaluation. Its
central finding is that current retrievers specifically struggle when a
query and its relevant code share little surface-level vocabulary, and
that generation quality does not automatically improve just because more
context is retrieved -- models can fail to make good use of context they
are handed. Follow-on work summarizing CodeRAG-Bench's results has
reported retrieval lifting SWE-bench-style pass rates by an order of
magnitude on complex tasks when the *right* context is found, which is
the strongest available evidence that retrieval quality, not just LLM
capability, is the bottleneck ACOR is aiming at. Practically, this is
your justification for why semantic similarity alone (what CodeRAG-Bench
evaluates most of its retrievers on) is not sufficient, and why ACOR adds
symbol-exact-match and graph-proximity terms specifically to cover the
low-lexical-overlap failure mode that paper documents.

**Yao et al., 2023 (ReAct, ICLR)** is the odd one out in your list in that
it is not about code retrieval at all -- it is about interleaving
reasoning traces with discrete actions (e.g., an API call) so the model's
reasoning can be updated by what an action returns. It is the right
citation for a specific extension, not for the MVP: an agentic version of
CodeLens where, instead of ACOR doing one fixed retrieval pass, the LLM
can explicitly emit an action like "show me the callers of
`processOrder`" mid-reasoning, which the backend executes against
`CallGraph.neighbors_within_hops()` and feeds back in. That is a
meaningfully different (and larger) system than the current pipeline;
flag it explicitly as future work rather than implying the MVP already
does this, since a committee that has actually read ReAct will notice the
difference between "retrieval-then-generate" and "interleaved
reasoning-and-acting" immediately.

## 5. The two papers not in your synopsis that you should read next

Two very recent works do almost exactly what ACOR's "graph + semantic"
framing describes, and a committee familiar with the area is likely to
ask how this project differs from them:

- **Phan et al., 2024, RepoHyper** builds a single unified "Repo-level
  Semantic Graph" and retrieves via a search-then-expand-then-rerank
  pipeline where the reranker is a *trained* GNN link predictor.
- **Liu et al., 2024, GraphCoder** (ASE 2024) retrieves via a
  coarse-to-fine search over a "code context graph."

Both report solid improvements over embedding-only baselines on
RepoBench, which is itself useful evidence that the structural-signal
premise behind ACOR is sound. The honest differences worth stating in
your literature review, not glossing over:

1. **Learned vs. explicit fusion.** Both prior works learn how to combine
   signals (a trained GNN reranker); ACOR combines them via an explicit,
   human-tunable weighted sum. This is a real trade-off, not free: ACOR
   will not automatically discover a better weighting than a trained
   model could, but it needs no training data or GNN infrastructure to
   build in a semester, and every score is individually explainable
   ("this method scored high because of a call-graph hit, not because of
   semantic similarity") -- which matters when the end product is
   supposed to *explain* bugs, not just retrieve context for completion.
2. **Different downstream task.** Both prior works target code
   *completion* (predict the next line/function), evaluated with
   exact-match/CodeBLEU against a held-out ground truth. ACOR targets bug
   explanation, root-cause analysis, and Q&A -- open-ended generation
   with no single correct string to match against, which is why
   `evaluation/metrics.py` deliberately does not offer an automatic
   "reasoning accuracy" metric (see that file's docstring) and instead
   flags it as a methodology decision (human rubric or LLM-as-judge) the
   team must make explicitly.
3. **Token budget as a first-class constraint.** Neither prior paper
   frames its objective around an explicit, queryable token budget the
   way `ranker.py`'s knapsack formulation does; both are more focused on
   retrieval/completion accuracy at a roughly fixed context size. If your
   evaluation chapter reports a token-reduction number alongside
   precision/recall (as the synopsis' Section 7 asks for), that is a
   genuinely distinct axis these two papers don't report on.

Cite Phan et al. (2024) and Liu et al. (2024, GraphCoder) explicitly in
your related-work section with these three distinctions -- it will read
as a considered positioning rather than an omission if a reviewer finds
these papers themselves.

## 6. Controlled evaluation

`scripts/evaluate_retrieval.py` accepts a manually labeled task file and
runs whole-file retrieval, semantic-only retrieval, and ACOR for the same
tasks using the same configured LLM and evaluation prompt.
`--include-ablations` additionally disables each ACOR score signal and compares
greedy/exact ranking. Method IDs and task labels must be supplied by the
benchmark author; the runner does not generate self-confirming labels.

Provider-reported input tokens are stored separately from the local
`tiktoken` estimate. Token reduction is reported only when actual usage is
available for both the whole-file and compared runs. Latency, model
predictions, bug-localization hit/exact match, and change-impact
precision/recall are recorded per task and condition. No empirical
benchmark result is claimed until this runner is executed against a
reviewed labeled dataset and a real model. See `docs/evaluation.md` for
task format, metric definitions, and limitations.