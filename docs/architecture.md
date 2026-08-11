# Architecture

## Pipeline, mapped to code

The synopsis' own diagram is the spec. Every box below names the file(s)
that implement it.

```
Developer Query
      |
      v
Query Understanding ................. backend/app/retrieval/query_understanding.py
      |
      v
Repository Parser .................... parser-service/ (JavaParser, JVM)
      |                                backend/app/parsing/java_bridge.py (subprocess bridge)
      v
Call Graph + Dependency Graph
+ Symbol Index ........................ backend/app/graphs/{call_graph,dependency_graph,symbol_index}.py
      |
      v
ACOR: Adaptive Context
Optimization & Ranking
  1. score  ........................... backend/app/acor/scorer.py
  2. rank & select under budget ....... backend/app/acor/ranker.py
  3. build final context text ......... backend/app/acor/context_builder.py
      |
      v
Optimized Repository Context
      |
      v
LLM (Qwen / Llama / GPT) .............. backend/app/llm/{providers,prompts}.py
      |
      v
Bug Explanation / Root Cause / Suggested Fix .... returned as QueryResponse
```

`backend/app/api/routes.py` is the glue that calls each stage in order for
the two HTTP endpoints (`/repositories/index`, `/repositories/{id}/query`).
If you want to read one file to understand how the whole system fits
together, read that one first, then follow its imports outward.

## Why a separate JVM process for parsing

`JavaParser`'s *symbol solver* -- the part that resolves a call expression
like `helper()` back to the actual class that declares `helper`, including
across files -- is a JVM library with no equivalent-fidelity pure-Python
port. Pure-Python Java grammars (e.g. `javalang`) parse syntax but do not
resolve symbols, and symbol resolution is exactly what the call graph
needs to be more than a bag of unconnected method names. `parser-service/`
is therefore a small, dependency-shaded JAR invoked once per indexing
request via `subprocess`, not a persistent server -- see
`java_bridge.py`'s module docstring for the reasoning and the upgrade path
(turn it into a long-lived process communicating over stdin/stdout once
JVM startup latency actually matters).

## Data flow through one query

1. `analyze_query()` classifies intent (bug / root-cause / feature-location
   / refactor / general) and extracts identifier-shaped anchor tokens from
   the raw query string.
2. The query is embedded and searched against the repository's vector
   store (FAISS or Chroma, whichever `VECTOR_DB_BACKEND` selects), which
   was populated with one vector per method at indexing time. This
   narrows the *whole* repository down to `top_k_candidates` (default 50)
   before the more expensive graph/symbol scoring runs.
3. `prepare_candidates()` reads each candidate's exact source snippet
   once and counts its tokens -- every later stage reuses this in-memory
   copy rather than touching disk again.
4. `score_candidates()` computes the four-term composite score (semantic,
   call-graph proximity, dependency proximity, symbol match) -- see
   `ACOR_algorithm.md` for the formula and the reasoning behind each term.
5. `rank_and_select()` solves the token-budget-constrained selection
   problem (0/1 knapsack; greedy by default, exact DP optionally) to
   decide which scored candidates actually survive.
6. `build_context()` groups the survivors by file and renders the final
   text block, with headers so the LLM (and a human reading the debug
   view) can trace every snippet back to its real location.
7. The LLM client (`llm/providers.py`) calls whichever provider
   `LLM_PROVIDER` names, using the prompt templates in `llm/prompts.py`,
   which explicitly tell the model the context is a curated partial
   excerpt -- not the whole repository -- to reduce confident-sounding
   answers about code the model never actually saw.

## Deliberate MVP simplifications (and their upgrade paths)

Called out here in one place rather than scattered as inline comments,
since a reviewer or evaluator is likely to ask about several of these
directly:

| Simplification | Where | Upgrade path |
|---|---|---|
| Repository state (graphs, symbol index, vector store handle) lives in an in-memory dict keyed by `repo_id` | `api/routes.py` | Persist each `RepoIndex` under `INDEX_STORAGE_DIR` (pickle the graphs/symbol index; Chroma already persists vectors to disk) and lazily reload on lookup. Required before running more than one backend worker process. |
| Parser is invoked as a fresh subprocess per index call (pays JVM startup cost every time) | `parsing/java_bridge.py` | Turn `parser-service` into a persistent process the backend talks to over a socket/stdin-stdout, or add a `--watch` mode that re-parses only changed files using `repo_walker.compute_repo_fingerprint()`. |
| Retrieval granularity is one chunk per method | `api/routes.py` indexing handler | Add class-level and file-level chunks for queries that are about a whole subsystem rather than one method; requires extending the vector store metadata schema and `prepare_candidates()`. |
| Query intent classification is rule-based (keyword/regex) | `retrieval/query_understanding.py` | Once real usage logs exist, replace with a small trained classifier -- the `QueryAnalysis` return type does not need to change, only how `classify_intent()` computes its answer. |
| Token counting uses `tiktoken`'s `cl100k_base` as a proxy for every provider | `acor/tokenizer.py` | Acceptable approximation (~15% typical variance across BPE tokenizers on source code) for ranking/budgeting purposes; get provider-exact counts from each API response's `usage` field (already captured in `LLMResponse`) if exact billing accounting is later required. |
| No authentication/multi-tenancy on the API | `api/main.py`, `api/routes.py` | Out of scope for a single-evaluator demo; would matter before any real deployment -- see the README section "Extra to think about" for the fuller list of production concerns the synopsis' scope doesn't require yet. |

## Extending the ranker: pulling in structural context

`CallGraph.neighbors_within_hops()` and the equivalent on
`DependencyGraph` are provided but not yet wired into the ranker. A
natural Week-11+ extension: once the top-N candidates by score are
selected, also pull in short *signature-only* stubs (not full bodies) of
each selected method's direct callers/callees, so the LLM sees the shape
of the surrounding call chain even when the full body of a neighboring
method didn't score high enough to earn its own budget slot. This is
exactly the kind of ablation ("ACOR with vs. without neighbor stubs")
that makes for a strong evaluation-chapter result.