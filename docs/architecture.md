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
2. The query is embedded and searched against the repository's Qdrant
   collection, which
   was populated with one vector per method at indexing time. This
   narrows the *whole* repository down to `top_k_candidates` (default 50)
   before the more expensive graph/symbol scoring runs.
3. `get_candidates()` reads each candidate's exact source snippet
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
| Repository registry is process-local, while graphs/symbol data are pickled and vectors persist in Qdrant | `api/routes.py`, `core/repo_index.py` | Use a shared registry/lock and deployment-wide Qdrant/index storage before running multiple backend workers. |
| Live sync reparses the full Java repository after a debounced file event to preserve cross-file symbol resolution | `watcher/live_sync.py`, `parsing/java_bridge.py` | Replace with a parser that supports incremental updates only after preserving equivalent cross-file call and dependency resolution. |
| Retrieval granularity is one chunk per method | `api/routes.py` indexing handler | Add class-level and file-level chunks for queries that are about a whole subsystem rather than one method; requires extending the vector store metadata schema and `prepare_candidates()`. |
| Query intent classification is rule-based (keyword/regex) | `retrieval/query_understanding.py` | Once real usage logs exist, replace with a small trained classifier -- the `QueryAnalysis` return type does not need to change, only how `classify_intent()` computes its answer. |
| ACOR context budgeting uses `tiktoken`'s `cl100k_base`; the rendered context (including file and method headers) is measured before it is returned | `acor/tokenizer.py`, `acor/ranker.py` | This is a strict budget for the formatted repository excerpt, not the full system/query prompt and not an exact count for every provider. Benchmark input-token counts use provider usage when returned. |
| No authentication/multi-tenancy on the API | `api/main.py`, `api/routes.py` | Out of scope for a single-evaluator demo; would matter before any real deployment -- see the README section "Extra to think about" for the fuller list of production concerns the synopsis' scope doesn't require yet. |

## Structural context and graph signals

`CallGraph.neighbors_within_hops()` and the equivalent on
Call and dependency proximity are scored in ACOR. Directed reverse
call-graph traversal is also used to seed change-impact candidates. The
ranker does not yet add signature-only neighbor stubs. The evaluation
harness holds the ACOR candidate set constant for score-signal ablations;
the semantic-only baseline uses vector retrieval without graph expansion.