# CodeLens — Full Codebase Audit Report

---

## 1. Executive Summary

CodeLens has a **well-designed skeleton with a critical execution gap**: the leaf-level modules (parsing, graphs, embeddings, tokenizer, LLM clients) are real, thoughtfully written code. The documentation is excellent. The tests are well-structured.

**But the system cannot run at all in its current state.**

The two directories that hold the integration glue — `backend/app/acor/` and `backend/app/api/` — are **completely empty**. There is no `config.py`. There is no `routes.py`. There are no `schemas.py` models. There are no `scorer.py`, `ranker.py`, or `context_builder.py` files. All three test files import modules that do not exist.

Additionally, every existing Python file uses PascalCase filenames (`Callgraph.py`, `Symbolindex.py`, etc.) while all imports — in tests, docs, and `main.py` — reference `snake_case` module names (`app.graphs.call_graph`, `app.graphs.symbol_index`). This is a **systematic naming mismatch** that causes `ImportError` on every import even if the missing modules existed.

**What works:** The Java parser-service (`RepoParser.java`) is real and functional. The graph data structures, embeddings, vector store, tokenizer, query understanding, and LLM providers are real code. The architecture design and documentation are genuinely good.

**What's missing:** ~40% of the backend — the entire ACOR layer (scorer, ranker, context builder), the entire API layer (routes, schemas, config), and `__init__.py` files for every package.

---

## 2. Actual Current Architecture

```
                ┌──────────────────────────────────┐
                │  parser-service/ (Java, REAL ✅)  │
                │  RepoParser.java + CodeEntities   │
                │  JavaParser + symbol solver       │
                └──────────────────────────────────┘
                              │ subprocess JSON
                              ▼
    parsing/JavaBridge.py   ✅  (bridge + dataclasses)
    parsing/Repowalker.py   ✅  (file walking + fingerprint)
                              │
                              ▼
    graphs/Callgraph.py       ✅  (NetworkX DiGraph, BFS proximity)
    graphs/Dependencygraph.py ✅  (class-level import graph)
    graphs/Symbolindex.py     ✅  (exact + Jaccard symbol lookup)
                              │
                              ▼
    retrieval/Embeddings.py          ✅  (sentence-transformers, cached)
    retrieval/Vectorstore.py         ✅  (FAISS + Chroma abstraction)
    retrieval/Tokenizer.py           ✅  (tiktoken / char fallback)
    retrieval/QueryUnderstanding.py  ✅  (intent + anchor extraction)
                              │
                              ▼
    ╔══════════════════════════════════════╗
    ║  app/acor/   ← ENTIRE DIR MISSING   ║
    ║  scorer.py          ✗  NOT EXIST    ║
    ║  ranker.py          ✗  NOT EXIST    ║
    ║  context_builder.py ✗  NOT EXIST    ║
    ╚══════════════════════════════════════╝
                              │
                              ▼
    llm/Providers.py   ✅  (OpenAI/Llama/Qwen unified client)
    llm/Prompts.py     ✅  (system + user prompt templates)
                              │
                              ▼
    ╔══════════════════════════════════════╗
    ║  app/api/    ← ENTIRE DIR MISSING   ║
    ║  routes.py      ✗  NOT EXIST        ║
    ║  schemas.py     ✗  NOT EXIST        ║
    ╚══════════════════════════════════════╝
    app/config.py    ✗  NOT EXIST
    app/main.py      ✅  exists, but imports will crash

    frontend/src/
      App.jsx               ✅  (2-panel shell)
      Querypanel.jsx        ✅  (index + query forms)
      Contextviewer.jsx     ✅  (answer + budget meter)
      api.js                ✅  (fetch wrappers)

    evaluation/metrics.py   ✅  (precision/recall@k, token_reduction_ratio)
    tests/test_call_graph.py ✅  (logic correct, import naming mismatch)
    tests/test_ranker.py    ✗  (imports app.acor.ranker — does not exist)
    tests/test_scorer.py    ✗  (imports app.acor.scorer — does not exist)
```

---

## 3. File-by-File Audit

### A. Parser Service (Java)

#### ✅ KEEP — [`Repoparser.java`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/parser-service/src/main/java/com/codelensai/parser/Repoparser.java)
- Two-pass AST walk using JavaParser + symbol solver. Pass 1: parse all `.java` files into CompilationUnits. Pass 2a: register all classes. Pass 2b: collect methods, call edges, import edges. Writes JSON to a temp file.
- **Symbol resolution:** `JavaParserTypeSolver` (cross-file) + `ReflectionTypeSolver` (JDK). Skips external/JDK calls correctly.
- **Line ranges:** Exact 1-indexed inclusive `start_line`/`end_line` per class and method.
- **Limitations (known/acceptable):** Skips anonymous class methods, field initializer calls, lambdas, method references silently.
- **Status:** ✅ Correct and functional. Must be built with `mvn package`.

#### ✅ KEEP — [`Codeentities.java`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/parser-service/src/main/java/com/codelensai/parser/model/Codeentities.java)
- Plain POJOs serialized by Jackson. Field names exactly mirror Python dataclasses. The `package`/`packageName` `@JsonIgnore` + `@JsonProperty` trick is correct.

---

### B. Parsing (Python)

#### ✅ KEEP — [`JavaBridge.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/parsing/JavaBridge.py)
- Runs the parser-service JAR as subprocess, reads JSON, deserializes into `ParsedRepo`.
- Error handling: `JavaParserError` wraps timeout, non-zero exit, missing output, bad JSON.
- **Broken:** imports `from app.config import Settings` — `config.py` doesn't exist.
- **Naming mismatch:** File is `JavaBridge.py`, imports use `app.parsing.java_bridge`.

#### ✅ KEEP — [`Repowalker.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/parsing/Repowalker.py)
- `list_java_files()` (recursive walk, prunes build/VCS dirs), `read_snippet()` (1-indexed line-range reader), `compute_repo_fingerprint()` (path+size+mtime hash).
- No external dependencies. Fully self-contained and correct.
- **Naming mismatch:** File is `Repowalker.py`, docs reference `repo_walker.py`.

---

### C. Graphs

#### ✅ KEEP — [`Callgraph.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/graphs/Callgraph.py)
- Wraps NetworkX `DiGraph`. Nodes = method IDs. Edges = caller→callee. Skips edges where either node is not in graph (no dangling JDK nodes). `proximity()`: undirected BFS, linear decay `1 - (dist/max_hops)`. `neighbors_within_hops()` exists for stub expansion.
- **Status:** ✅ Correct. All proximity edge cases tested.
- **Naming mismatch:** File is `Callgraph.py`, imports use `app.graphs.call_graph`.

#### ✅ KEEP — [`Dependencygraph.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/graphs/Dependencygraph.py)
- Same shape as CallGraph at class level via import edges.
- **Status:** ✅ Correct.
- **Naming mismatch:** `Dependencygraph.py` vs `app.graphs.dependency_graph`.

#### ✅ KEEP — [`Symbolindex.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/graphs/Symbolindex.py)
- Two-level index: `_exact` (name → locations) and `_tokens` (symbol_id → subtoken set). `match_score()`: 1.0 for exact name match, else Jaccard overlap of camelCase-split subtokens.
- **Status:** ✅ Correct.
- **Naming mismatch:** `Symbolindex.py` vs `app.graphs.symbol_index`.

---

### D. Retrieval

#### ✅ KEEP — [`QueryUnderstanding.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/retrieval/QueryUnderstanding.py)
- `classify_intent()` (keyword/phrase matching, first-match wins), `extract_anchor_symbols()` (regex + case-structure + dot-qualifier detection), `analyze_query()` (combined `QueryAnalysis`).
- **Broken:** imports `from app.api.schemas import QueryIntentLabel` (schemas don't exist) and `from app.graphs.symbol_index import tokenize_identifier` (naming mismatch).
- **Logic:** ✅. Cannot run due to broken imports.

#### ✅ KEEP — [`Embeddings.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/retrieval/Embeddings.py)
- `EmbeddingModel` wraps `SentenceTransformer`. Returns L2-normalized float32 arrays. `get_embedding_model()` is LRU-cached. Lazy import of sentence-transformers.
- **Broken:** imports `from app.config import Settings`.

#### ✅ KEEP — [`Vectorstore.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/retrieval/Vectorstore.py)
- Abstract `VectorStore` with `FaissVectorStore` (exact IP, in-memory) and `ChromaVectorStore` (persistent). FAISS: `IndexFlatIP`, pre-normalized vectors → dot = cosine. Chroma: converts distance back to similarity.
- **Broken:** imports `from app.config import Settings`.

#### ✅ KEEP — [`Tokenizer.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/retrieval/Tokenizer.py)
- `count_tokens()`: tiktoken `cl100k_base` if available, else `ceil(len(text)/4)`. Conservative fallback.
- **Status:** ✅ Correct. No broken imports.

---

### E. ACOR Layer — ALL MISSING

#### ✗ MISSING — `backend/app/acor/scorer.py`
- **Should implement:** `score_candidates()`, `_min_max_normalize()`, `_resolve_anchors()`, dataclasses `Candidate` and `ScoredCandidate`.
- **Full spec defined in:** [`test_scorer.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/tests/test_scorer.py) and [`ACOR_algorithm.md`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/docs/ACOR_algorithm.md).

#### ✗ MISSING — `backend/app/acor/ranker.py`
- **Should implement:** `rank_and_select()`, `_greedy_by_density()`, `_exact_knapsack()`.
- **Full spec defined in:** [`test_ranker.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/tests/test_ranker.py) and [`ACOR_algorithm.md`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/docs/ACOR_algorithm.md).

#### ✗ MISSING — `backend/app/acor/context_builder.py`
- **Should implement:** `build_context()` — group selected candidates by file, call `read_snippet()`, render with file+line headers.
- **Referenced by:** [`architecture.md`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/docs/architecture.md) step 6, `Repowalker.py` docstring.

---

### F. API Layer — ALL MISSING

#### ✗ MISSING — `backend/app/api/routes.py`
- **Should implement:** `POST /repositories/index`, `POST /repositories/{repo_id}/query`, `GET /repositories/{repo_id}/status`. In-memory `dict[str, RepoIndex]` state.
- `main.py` crashes on startup because `from app.api.routes import router` fails.

#### ✗ MISSING — `backend/app/api/schemas.py`
- **Should define:** `QueryIntentLabel` enum, `IndexRepositoryRequest`, `IndexRepositoryResponse`, `QueryRequest`, `QueryResponse`, `ScoreBreakdown`, `RetrievedContextItem`.
- Multiple existing modules break at import time because of this.

#### ✗ MISSING — `backend/app/config.py`
- **Should define:** `ACORWeights` dataclass, `Settings` pydantic-settings `BaseSettings`, `get_settings()` cached factory.
- Referenced by `main.py`, `JavaBridge.py`, `Embeddings.py`, `Vectorstore.py`, `Providers.py`, all tests.

---

### G. LLM

#### ✅ KEEP — [`Providers.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/llm/Providers.py)
- Shared `_OpenAICompatibleClient` → `OpenAIClient`, `LlamaClient`, `QwenClient`. `get_llm_client()` factory. Returns `LLMResponse`.
- **Broken:** imports `from app.config import Settings`.

#### ✅ KEEP — [`Prompts.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/llm/Prompts.py)
- `SYSTEM_PROMPT` + `build_user_prompt()`. Intent-aware instruction injection. Tells LLM context is partial.
- **Broken:** imports `from app.api.schemas import QueryIntentLabel`.

---

### H. Evaluation

#### ✅ KEEP — [`metrics.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/app/evaluation/metrics.py)
- `precision_recall_at_k()`, `token_reduction_ratio()`, `LatencyTracker`. Standalone, no broken imports. ✅

---

### I. Tests

| File | Logic | Imports |
|---|---|---|
| [`test_call_graph.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/tests/test_call_graph.py) | ✅ Correct | Naming mismatch (case-insensitive OS hides this) |
| [`test_ranker.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/tests/test_ranker.py) | ✅ Correct | ✗ `app.acor.ranker`, `app.api.schemas`, `app.config` don't exist |
| [`test_scorer.py`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/backend/tests/test_scorer.py) | ✅ Correct | ✗ `app.acor.scorer`, `app.config` don't exist |

**The test files are the authoritative specification** for scorer.py and ranker.py. They define exact function signatures, types, and behavioral contracts.

---

### J. Frontend

#### ✅ KEEP — All frontend files complete and correct

- [`App.jsx`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/frontend/src/App.jsx): 2-panel shell
- [`Querypanel.jsx`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/frontend/src/components/Querypanel.jsx): Index + query forms, loading/error states
- [`Contextviewer.jsx`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/frontend/src/components/Contextviewer.jsx): Answer, intent badge, `BudgetMeter`, per-candidate score+snippet
- [`api.js`](file:///c:/Users/singh/OneDrive/Desktop/CodeLens/frontend/src/api.js): `indexRepository()`, `queryRepository()`, `repositoryStatus()`

Frontend will work as soon as the backend API exists and returns the expected schema.

**Minor risk:** `App.jsx` imports `QueryPanel.jsx` and `ContextViewer.jsx` with PascalCase names but files are `Querypanel.jsx` and `Contextviewer.jsx`. Silently works on Windows/macOS (case-insensitive FS), breaks on Linux/Docker.

---

## 4. Data Flows

### 4.1 Indexing Flow

```
POST /repositories/index  { repo_path, repo_id?, force_reindex? }
         │
         ▼  [routes.py ✗ MISSING — orchestrates everything below]
  1. compute_repo_fingerprint(repo_path)     ← repo_walker.py ✅
  2. Skip if fingerprint matches & !force_reindex
  3. parse_repository(repo_path, settings)   ← java_bridge.py ✅
     → subprocess: parser-service JAR        ← RepoParser.java ✅
     → ParsedRepo: classes, methods, call_edges, import_edges
  4. CallGraph.build(methods, call_edges)    ← call_graph.py ✅
  5. DependencyGraph.build(classes, edges)   ← dependency_graph.py ✅
  6. SymbolIndex.build(classes, methods)     ← symbol_index.py ✅
  7. For each method:
       snippet = read_snippet(...)           ← repo_walker.py ✅
       vector = EmbeddingModel.encode_one()  ← embeddings.py ✅
  8. VectorStore.add(ids, vectors, metadata) ← vector_store.py ✅
  9. Store RepoIndex in dict[repo_id]        ← routes.py ✗ MISSING
 10. Return IndexRepositoryResponse          ← schemas.py ✗ MISSING

Broken at steps 9–10. Steps 1–8 have working implementations.
```

### 4.2 Query Flow

```
POST /repositories/{repo_id}/query  { query, token_budget? }
         │
         ▼  [routes.py ✗ MISSING]
  1. analyze_query(query)                    ← query_understanding.py ✅
     → QueryAnalysis { intent, anchor_symbols, query_tokens }
  2. encode_one(query)                       ← embeddings.py ✅
  3. vector_store.search(vector, top_k=50)   ← vector_store.py ✅
  4. prepare_candidates():
       read_snippet() + count_tokens()       ← repo_walker.py ✅, tokenizer.py ✅
       → list[Candidate]
  5. score_candidates(...)                   ← acor/scorer.py ✗ MISSING
     → list[ScoredCandidate]
  6. rank_and_select(scored, token_budget)   ← acor/ranker.py ✗ MISSING
     → RankResult { selected, dropped, total_tokens }
  7. build_context(selected, repo_root)      ← acor/context_builder.py ✗ MISSING
     → formatted_context string
  8. llm_client.generate(system, user)       ← providers.py ✅, prompts.py ✅
     → LLMResponse
  9. Return QueryResponse                    ← schemas.py ✗ MISSING

Broken at steps 5–9. Steps 1–4 and 8 have working implementations.
```

### 4.3 Frontend Flow

```
User fills repoPath → "Index"
  → api.js indexRepository() → POST /repositories/index
  → Querypanel stores repoId from response

User types query + token_budget → "Ask CodeLens"
  → api.js queryRepository() → POST /repositories/{repoId}/query
  → ContextViewer renders: answer, intent, BudgetMeter, candidate snippets
```

---

## 5. Parsing Findings

| Property | Status |
|---|---|
| Parser type | **AST-based** via JavaParser (JVM). NOT regex/text. |
| Symbol resolution | ✅ Real — `JavaSymbolSolver` with cross-file + JDK type solvers |
| Classes | ✅ id (FQN), name, package, file_path, start_line, end_line, imports |
| Methods | ✅ id (FQN#signature), class_id, name, signature, file_path, start_line, end_line |
| Source locations | ✅ Exact 1-indexed line numbers |
| Call edges | ✅ Method-level, repo-source-only |
| Import edges | ✅ Class-level, repo-classes-only |
| Source code stored | ✗ Intentionally fetched at runtime (memory-bounded design) |
| Interfaces | ✗ Not separately modeled; included as ClassOrInterfaceDeclaration |
| Enum/annotation types | ✗ Not handled |
| Language level | JAVA_17 pinned — parses older code fine |
| Multi-root Maven repos | ✅ Registers `src/main/java` as additional type solver root |

**Tree-sitter verdict:** NOT needed. JavaParser's symbol solver is superior. Tree-sitter gives syntax but not cross-file symbol resolution, which is what the call graph requires.

---

## 6. Graph Findings

| Property | Status |
|---|---|
| Call graph | Directed `nx.DiGraph`, method-level |
| Proximity | Undirected BFS (correct — caller ≈ callee relevance), linear decay, bounded by `max_hops` |
| External call filtering | ✅ Both Java and Python sides filter to repo-source-only |
| Dependency graph | Directed `nx.DiGraph`, class-level via import edges |
| Graph persistence | ✗ In-memory only. Lost on server restart. |
| Neighbor stub expansion | `neighbors_within_hops()` exists but **not wired into any pipeline step** |

---

## 7. ACOR Audit

| Stage | Documented Spec | Code Status |
|---|---|---|
| Candidate generation | Top-K semantic search | `Vectorstore.search()` ✅ |
| Scoring — semantic | Min-max normalized cosine similarity | `acor/scorer.py` ✗ MISSING |
| Scoring — call_graph | BFS proximity from anchor methods | `acor/scorer.py` ✗ MISSING |
| Scoring — dependency | BFS proximity from anchor classes | `acor/scorer.py` ✗ MISSING |
| Scoring — symbol | Exact + Jaccard match | `acor/scorer.py` ✗ MISSING |
| Anchor resolution | anchor_symbols → method IDs + class IDs | `acor/scorer.py` ✗ MISSING |
| Ranking / selection | 0/1 knapsack (greedy or exact DP) | `acor/ranker.py` ✗ MISSING |
| Diversity | Not described in any doc | ✗ Not planned |
| Context assembly | Group by file, file+line headers | `acor/context_builder.py` ✗ MISSING |
| Intent-adaptive weights | Different `ACORWeights` per intent | Referenced in tests, not implemented |

---

## 8. Token Budget Findings

| Concept | Status |
|---|---|
| Token counting | ✅ `Tokenizer.count_tokens()` — single shared source |
| Per-candidate token count | ✅ Part of `Candidate` dataclass |
| Token budget as hard constraint | ✅ `rank_and_select(candidates, token_budget)` — max for selected context |
| `selected_context_tokens <= token_budget` | ✅ Guaranteed by ranker design (verified in tests) |
| Full repository token count | ✗ Not computed |
| Candidate pool token count | ✗ Not tracked separately |
| `naive_baseline_tokens` | ✅ Mentioned in `QueryResponse`, `ContextViewer.jsx` renders it — needs implementation in routes.py |
| Token reduction metric | ✅ `metrics.py::token_reduction_ratio()` — correct implementation |

**The token budget concept is correctly understood.** Budget = max for ACOR's selected context. Not the repo total. Not savings. This is correct.

---

## 9. Broken / Incomplete Code

### Critical Blockers

| Problem | Files affected |
|---|---|
| `app/config.py` missing | `main.py`, `JavaBridge.py`, `Embeddings.py`, `Vectorstore.py`, `Providers.py`, all tests |
| `app/api/routes.py` missing | `main.py` crashes on startup |
| `app/api/schemas.py` missing | `QueryUnderstanding.py`, `Prompts.py`, both test files |
| `app/acor/scorer.py` missing | `test_scorer.py` |
| `app/acor/ranker.py` missing | `test_ranker.py` |
| `app/acor/context_builder.py` missing | Referenced in docs and `Repowalker.py` docstring |

### Import Naming Mismatch (PascalCase files, snake_case imports)

| File on disk | Expected import |
|---|---|
| `graphs/Callgraph.py` | `app.graphs.call_graph` |
| `graphs/Dependencygraph.py` | `app.graphs.dependency_graph` |
| `graphs/Symbolindex.py` | `app.graphs.symbol_index` |
| `parsing/JavaBridge.py` | `app.parsing.java_bridge` |
| `parsing/Repowalker.py` | `app.parsing.repo_walker` |
| `retrieval/QueryUnderstanding.py` | `app.retrieval.query_understanding` |
| `retrieval/Embeddings.py` | `app.retrieval.embeddings` |
| `retrieval/Vectorstore.py` | `app.retrieval.vector_store` |
| `retrieval/Tokenizer.py` | `app.retrieval.tokenizer` |
| `llm/Providers.py` | `app.llm.providers` |
| `llm/Prompts.py` | `app.llm.prompts` |

### Missing `__init__.py` files
Every subdirectory (`acor/`, `api/`, `graphs/`, `parsing/`, `retrieval/`, `llm/`, `evaluation/`) lacks `__init__.py`.

---

## 10. Architectural Problems

1. **The glue layer is entirely absent.** `routes.py` is where every implemented component gets wired together. Without it you have correct modules that form no functioning system.
2. **File naming convention is inconsistent.** PascalCase filenames (Java convention) in a Python codebase breaks import conventions.
3. **In-memory-only state.** Every server restart loses all indexed data. Fine for demo; breaks evaluation runs.
4. **Parser JAR not pre-built.** Requires `mvn package` as a setup step. No pre-built JAR in repo.
5. **No `pyproject.toml` or `setup.py`.** Tests require careful `PYTHONPATH` management.
6. **Frontend component import case mismatch.** `App.jsx` imports `QueryPanel` / `ContextViewer` (capital) but files are `Querypanel.jsx` / `Contextviewer.jsx`. Fails on Linux/Docker.

---

## 11. What Already Works Well

1. **Java parser-service is genuinely good.** Real AST + symbol resolution, not regex. Two-pass design. Correct call-edge filtering. Well-commented.
2. **Graph data structures are correct and tested.** BFS proximity is the right algorithm. Undirected view is correct. `neighbors_within_hops()` ready for stub expansion.
3. **Scoring formula design is sound.** 4-term weighted sum with min-max normalization and intent-adaptive weights. Separation of scorer from ranker enables independent evaluation of retrieval quality vs. budgeting quality.
4. **Knapsack framing is correct.** Adversarial counterexample test proves greedy's failure mode concretely — sophisticated and honest.
5. **Token budget concept is correctly understood.** Budget = max for selected context. `naive_baseline_tokens` comparison is correctly separated for evaluation.
6. **Frontend is complete.** Per-candidate scores, file locations, budget meter, savings display — needs no changes once API exists.
7. **LLM abstraction is clean.** One shared client for all providers. Prompt tells LLM context is partial — reduces hallucination.
8. **`evaluation/metrics.py` is ready.** `precision_recall_at_k()` and `token_reduction_ratio()` are correct and immediately usable.
9. **Documentation is honest.** `ACOR_algorithm.md` honestly situates the project relative to prior work. Architecture doc's simplification table is accurate.

---

## 12. Gaps Against Intended Architecture

```
Component             Intended    Actual
──────────────────────────────────────────────
Parser                ✅          ✅ Java parser functional
Graphs (all 3)        ✅          ✅ All three implemented
Embeddings            ✅          ✅ + both vector store backends
Query Understanding   ✅          ✅ (import fix needed)
ACOR Scorer           ✅          ✗  MISSING
ACOR Ranker           ✅          ✗  MISSING
Context Builder       ✅          ✗  MISSING
Config                ✅          ✗  MISSING
API Schemas           ✅          ✗  MISSING
API Routes            ✅          ✗  MISSING
LLM integration       ✅          ✅ All 3 providers
Frontend              ✅          ✅ Complete
Tests                 ✅          ✅ Logic correct, imports broken
Evaluation metrics    ✅          ✅ metrics.py complete
Persistence           MVP: memory ✗  Not relevant yet (no routes)
MCP                   Future      ✗  Correctly deferred
```

---

## 13. REQUIRED NOW

*Necessary to make the pipeline correct and evaluatable.*

### Step 1 — Rename Python files to snake_case

| Old | New |
|---|---|
| `graphs/Callgraph.py` | `graphs/call_graph.py` |
| `graphs/Dependencygraph.py` | `graphs/dependency_graph.py` |
| `graphs/Symbolindex.py` | `graphs/symbol_index.py` |
| `parsing/JavaBridge.py` | `parsing/java_bridge.py` |
| `parsing/Repowalker.py` | `parsing/repo_walker.py` |
| `retrieval/QueryUnderstanding.py` | `retrieval/query_understanding.py` |
| `retrieval/Embeddings.py` | `retrieval/embeddings.py` |
| `retrieval/Vectorstore.py` | `retrieval/vector_store.py` |
| `retrieval/Tokenizer.py` | `retrieval/tokenizer.py` |
| `llm/Providers.py` | `llm/providers.py` |
| `llm/Prompts.py` | `llm/prompts.py` |

Also fix frontend: rename `Querypanel.jsx` → `QueryPanel.jsx`, `Contextviewer.jsx` → `ContextViewer.jsx`.

### Step 2 — Add `__init__.py` to all packages
`app/`, `app/acor/`, `app/api/`, `app/graphs/`, `app/parsing/`, `app/retrieval/`, `app/llm/`, `app/evaluation/`

### Step 3 — Create `backend/app/config.py`
- `ACORWeights` dataclass (semantic, call_graph, dependency, symbol)
- `Settings` pydantic-settings `BaseSettings` with all env vars (visible in docker-compose + referenced across codebase)
- `get_settings()` cached factory

### Step 4 — Create `backend/app/api/schemas.py`
- `QueryIntentLabel` enum (5 values from `QueryUnderstanding.py`)
- `IndexRepositoryRequest`, `IndexRepositoryResponse`
- `QueryRequest`, `QueryResponse`
- `ScoreBreakdown`, `RetrievedContextItem`
- Derive fields from: `scripts/index_repository.py` output parsing + `Contextviewer.jsx` field accesses + test files

### Step 5 — Create `backend/app/acor/scorer.py`
- `Candidate` dataclass, `ScoredCandidate` dataclass
- `_min_max_normalize(values: list[float]) -> list[float]`
- `_resolve_anchors(anchor_symbols, symbol_index) -> tuple[set[str], set[str]]`
- `score_candidates(candidates, query, call_graph, dep_graph, symbol_index, weights, max_hops) -> list[ScoredCandidate]`
- **Full spec defined by** `test_scorer.py` and `ACOR_algorithm.md`

### Step 6 — Create `backend/app/acor/ranker.py`
- `_greedy_by_density(candidates, token_budget) -> set[str]`
- `_exact_knapsack(candidates, token_budget) -> set[str]`
- `rank_and_select(candidates, token_budget, settings) -> RankResult`
- **Full spec defined by** `test_ranker.py` and `ACOR_algorithm.md`

### Step 7 — Create `backend/app/acor/context_builder.py`
- `build_context(selected: list[ScoredCandidate], repo_root: Path) -> str`
- Group by file, call `read_snippet()`, prepend `// file_path:start_line-end_line` headers

### Step 8 — Create `backend/app/api/routes.py`
- FastAPI router
- `POST /repositories/index` — parse → build graphs → embed → store → return `IndexRepositoryResponse`
- `POST /repositories/{repo_id}/query` — retrieve → prepare → score → rank → build_context → LLM → return `QueryResponse`
- `GET /repositories/{repo_id}/status`
- In-memory `dict[str, RepoIndex]` state (MVP)
- Compute and include `naive_baseline_tokens` in response

---

## 14. USEFUL NEXT

1. Run `pytest backend/tests/ -v` — all 3 test files should pass after REQUIRED NOW is complete.
2. Build parser JAR (`cd parser-service && mvn -q package`) and smoke-test with `scripts/index_repository.py`.
3. Defects4J pilot — index 5–8 bugs, query against known fault locations, compute `precision_recall_at_k()`.
4. Intent-adaptive weight tuning — test different `ACORWeights` per intent, record P@k per intent type.
5. Ablation experiments — zero each weight; greedy vs. exact knapsack comparison.
6. Index persistence — pickle graphs + symbol index; Chroma already persists vectors.
7. Neighbor stub expansion — wire `CallGraph.neighbors_within_hops()` as post-selection step, ablation flag.

---

## 15. LATER

1. **MCP integration** — thin layer wrapping existing pipeline. No duplication.
   ```python
   # mcp/tools.py
   def find_root_cause_context(repo_id, query, token_budget):
       # analyze_query → search → score → rank → build_context
   def find_change_impact_context(repo_id, changed_symbol, token_budget, hop_limit):
       # CallGraph.neighbors_within_hops → score (graph-weighted) → rank
   ```
2. Iterative retrieval (RepoCoder-style) — Week 12+ extension.
3. Multi-language support — Java + Defects4J is enough for semester evaluation.
4. Parser-service persistent process — only if JVM startup latency is a measured problem.
5. Authentication / multi-tenancy — out of scope.

---

## 16. NOT NEEDED / Avoid

1. **Do not rewrite the Java parser.** It is the strongest part of the codebase. Tree-sitter would be a regression.
2. **Do not merge scorer.py and ranker.py.** Separation enables independent evaluation of retrieval quality vs. budgeting quality.
3. **Do not add a trained GNN reranker.** Needs evaluation evidence first.
4. **Do not build a full SWE-bench harness.** Use Defects4J + hand-labeled queries.
5. **Do not add Redis/database backend yet.** In-memory is correct for pilot evaluation.
6. **Do not pre-embed at file/class level yet.** Method granularity is correct for current task.

---

## 17. Execution Plan

```
Step  Task                              Complexity  Time Est.
──────────────────────────────────────────────────────────────
  1   Rename Python files to snake_case    Low       ~15 min
  2   Add __init__.py to all packages      Low       ~5 min
  3   Create app/config.py                 Low       ~1 hr
  4   Create app/api/schemas.py            Low       ~1 hr
  5   Create app/acor/scorer.py            Medium    ~2 hr (spec = tests + docs)
  6   Create app/acor/ranker.py            Medium    ~2 hr (spec = tests + docs)
  7   Create app/acor/context_builder.py   Low       ~1 hr
  8   Create app/api/routes.py             High      ~4 hr (main integration)
  9   pytest backend/tests/ -v             Low       verify all pass
 10   Build parser JAR + smoke test        Medium    ~2 hr (incl. Java setup)
 11   End-to-end test with real Java repo  Medium    ~2 hr
```

**Total: ~14 hours of focused work** to reach a functional, fully testable system.

---

## 18. Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Parser JAR build fails (Maven/Java version issues) | High | Medium | Document exact Java 17 + Maven 3.x requirements; consider committing a pre-built JAR |
| Symbol solver resolution rate low on Defects4J (sparse call graph) | Medium | Medium | Log resolution rates per repo; ACOR degrades gracefully (unresolved calls → 0 call-graph score) |
| Embedding model too slow for large repo indexing | Medium | Low | Model is LRU-cached; batch_size=32; FAISS is fast. Profile before optimizing. |
| In-memory state lost during long evaluation runs | Medium | Medium | Document; implement persistence before multi-hour evaluation sessions |
| tiktoken proxy counts diverge >15% from actual LLM billing | Low | Low | Acceptable for ranking/budgeting; not a correctness issue |
| File naming case collision breaks Docker (Linux FS) | **High** | **High if unfixed** | **Fix in Step 1 before anything else** |
| Frontend import case mismatch breaks Vite on Linux | Medium | High if unfixed | Fix in Step 1 alongside Python files |
| Defects4J pilot shows low precision | Medium | Medium | Expected for first run; ablations will diagnose which ACOR component is weak |
