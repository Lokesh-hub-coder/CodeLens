# CodeLens — Architecture Gap Analysis & Phase-wise Implementation Plan

---

## Target Architecture vs. Current State

```
TARGET ARCHITECTURE                     CURRENT STATUS
────────────────────────────────────────────────────────────────────────

ONE-TIME / INDEXING
─────────────────────────────────────────
Repository
    ↓ Parse                             ✅  JavaBridge.py + RepoParser.java
    ↓ Extract symbols                   ✅  Symbolindex.py, Callgraph.py, Dependencygraph.py
    ↓ Build/store graphs + indexes      ⚠️  Built in-memory ✅, stored to disk ✗ MISSING

ON EVERY USER QUERY
─────────────────────────────────────────
Query
    ↓ Retrieve candidates               ✅  Vectorstore.search() + QueryUnderstanding.py
    ↓ Task-specific graph expansion     ⚠️  neighbors_within_hops() EXISTS but NOT WIRED
    ↓ ACOR score/rank                   ✗  scorer.py + ranker.py MISSING
    ↓ Token-budget selection            ✗  ranker.py MISSING (Tokenizer.py ✅ exists)
    ↓ Optimized context                 ✗  context_builder.py MISSING

MCP LAYER
─────────────────────────────────────────
Cursor/Copilot/Antigravity
    ↓ MCP tool call                     ✗  MISSING entirely
    ↓ CodeLens MCP Server               ✗  MISSING entirely
    ↓ find_root_cause_context()         ✗  MISSING
    ↓ find_change_impact_context()      ✗  MISSING
    ↓ CodeLens Core                     ✗  routes.py MISSING (core pipeline not wired)

INFRASTRUCTURE
─────────────────────────────────────────
config.py                               ✗  MISSING
api/schemas.py                          ✗  MISSING
api/routes.py                           ✗  MISSING
__init__.py (all packages)              ✗  MISSING
Python file naming (snake_case)         ✗  All files PascalCase — import mismatch
```

---

## What Maps Directly to the Target Architecture

| Target Component | Current Code | Completeness |
|---|---|---|
| Parse | `JavaBridge.py` + `RepoParser.java` | ✅ 100% — needs JAR built |
| Extract symbols (classes + methods) | `Symbolindex.py` | ✅ 100% |
| Build call graph | `Callgraph.py` | ✅ 100% |
| Build dependency graph | `Dependencygraph.py` | ✅ 100% |
| Store embedding index | `Vectorstore.py` (FAISS/Chroma) | ✅ 100% |
| Retrieve candidates | `Vectorstore.search()` | ✅ 100% |
| Query understanding | `QueryUnderstanding.py` | ✅ 100% |
| Graph expansion (proximity) | `Callgraph.proximity()`, `neighbors_within_hops()` | ✅ code exists, ✗ not wired |
| ACOR scoring | `acor/scorer.py` | ✗ 0% — spec in tests+docs |
| ACOR ranking / budget selection | `acor/ranker.py` | ✗ 0% — spec in tests+docs |
| Context assembly | `acor/context_builder.py` | ✗ 0% |
| Root-cause candidate path | — | ✗ 0% — concept only |
| Change-impact candidate path | — | ✗ 0% — concept only |
| Index persistence (disk) | — | ✗ 0% |
| MCP Server | — | ✗ 0% |
| Config + Schemas + Routes | — | ✗ 0% |
| Token counting | `Tokenizer.py` | ✅ 100% |
| LLM integration | `Providers.py` + `Prompts.py` | ✅ 100% |
| Frontend (demo/eval surface) | All `.jsx` + `api.js` | ✅ 100% |
| Evaluation metrics | `metrics.py` | ✅ 100% |

**Overall: ~55% of the leaf-level code exists. 0% of the integration layer exists.**

---

## Two Candidate Generation Paths — What They Mean

The target architecture separates **root-cause** from **change-impact** candidate generation. Here is what each means concretely:

### Root-Cause Path
> "Why does X fail? / Where is the bug?"
- **Primary signal:** Semantic search (embedding similarity) narrows the whole repo to the top-K relevant methods
- **Graph expansion:** BFS from anchor methods named in the query (e.g., a stack trace method) along the call graph — expand neighbors up to `max_hops` hops
- **ACOR weight bias:** Higher weights on `semantic` + `call_graph` + `symbol`
- **Existing code that supports this:** `QueryUnderstanding.analyze_query()` → `Vectorstore.search()` → `Callgraph.proximity()` + `neighbors_within_hops()`

### Change-Impact Path
> "I'm changing X — what else will break?"
- **Primary signal:** Graph traversal from the changed symbol (not semantic search)
- **Graph expansion:** BFS from the changed symbol's method/class ID — collect direct callers, transitive callers, dependent classes
- **ACOR weight bias:** Higher weight on `call_graph` + `dependency`, lower on `semantic`
- **Existing code that supports this:** `Callgraph.neighbors_within_hops()` + `Dependencygraph.proximity()` — just not wired yet

Both paths converge into the same ACOR scoring + ranking pipeline after candidate generation.

---

# Phase-wise Implementation Plan

---

## Phase 0 — Foundation Fixes
**Goal:** Make existing code importable and tests runnable.
**Effort:** ~1 day
**Blocking:** Everything else depends on this.

### Tasks

#### 0.1 Rename Python files to snake_case
Fixes the systematic PascalCase → snake_case import mismatch that blocks every import.

| Rename | To |
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
| `frontend/src/components/Querypanel.jsx` | `QueryPanel.jsx` |
| `frontend/src/components/Contextviewer.jsx` | `ContextViewer.jsx` |

#### 0.2 Add `__init__.py` to all packages
```
backend/app/__init__.py
backend/app/acor/__init__.py
backend/app/api/__init__.py
backend/app/graphs/__init__.py
backend/app/parsing/__init__.py
backend/app/retrieval/__init__.py
backend/app/llm/__init__.py
backend/app/evaluation/__init__.py
```

#### 0.3 Create `backend/app/config.py`
```python
# Key fields (from docker-compose + code references):
class ACORWeights(BaseModel):
    semantic: float = 0.40
    call_graph: float = 0.20
    dependency: float = 0.15
    symbol: float = 0.25

class Settings(BaseSettings):
    # Parser
    java_parser_jar_path: Path
    java_bin: str = "java"
    parser_timeout_seconds: int = 120
    # Embeddings
    embedding_model: str = "flax-sentence-embeddings/st-codesearch-distilroberta-base"
    embedding_dim: int = 768
    # Vector store
    vector_db_backend: str = "faiss"  # or "chroma"
    chroma_persist_dir: Path = Path("./data/chroma")
    # Index persistence
    index_storage_dir: Path = Path("./data/index")
    # Retrieval
    top_k_candidates: int = 50
    graph_max_hops: int = 4
    # Ranking
    ranker_strategy: str = "greedy"  # or "exact"
    exact_knapsack_max_candidates: int = 200
    default_token_budget: int = 8000
    # LLM
    llm_provider: str = "openai"
    llm_model_name: str = "gpt-4o-mini"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llama_base_url: str = "http://localhost:11434"
    llama_model_name: str = "llama3"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    qwen_model_name: str = "qwen-max"
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.2
    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    acor_weights: ACORWeights = ACORWeights()
```

#### 0.4 Create `backend/app/api/schemas.py`
```python
class QueryIntentLabel(str, Enum):
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    BUG_EXPLANATION = "bug_explanation"
    REFACTORING = "refactoring"
    FEATURE_LOCATION = "feature_location"
    GENERAL_QA = "general_qa"

class ScoreBreakdown(BaseModel):
    semantic: float
    call_graph: float
    dependency: float
    symbol: float
    composite: float

class RetrievedContextItem(BaseModel):
    symbol_name: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    token_count: int
    score: ScoreBreakdown

class IndexRepositoryRequest(BaseModel):
    repo_path: str
    repo_id: Optional[str] = None
    force_reindex: bool = False

class IndexRepositoryResponse(BaseModel):
    repo_id: str
    num_files: int
    num_classes: int
    num_methods: int
    num_call_edges: int
    num_import_edges: int
    unparsed_files: list[str]
    index_time_seconds: float

class QueryRequest(BaseModel):
    repo_id: str
    query: str
    token_budget: Optional[int] = None

class QueryResponse(BaseModel):
    repo_id: str
    query: str
    detected_intent: QueryIntentLabel
    answer: str
    retrieved_context: list[RetrievedContextItem]
    candidates_considered: int
    candidates_selected: int
    total_context_tokens: int
    token_budget: int
    naive_baseline_tokens: int
    retrieval_latency_ms: float
    llm_latency_ms: float
```

#### 0.5 Build parser-service JAR
```bash
cd parser-service
mvn -q package -DskipTests
# Output: target/parser-service-1.0.0.jar
```

### Phase 0 Exit Criteria
- [ ] All Python files importable without `ImportError`
- [ ] `python -c "from app.config import get_settings; print(get_settings())"` works
- [ ] `python -c "from app.api.schemas import QueryResponse"` works
- [ ] `parser-service/target/parser-service-1.0.0.jar` exists

---

## Phase 1 — Indexing Pipeline
**Goal:** A repository can be indexed end-to-end. Graphs + symbol index + embeddings are built and persisted to disk.
**Effort:** ~3 days
**Depends on:** Phase 0 complete

### Architecture view — what Phase 1 implements
```
Repository
    ↓ list_java_files()           [repo_walker.py — existing ✅]
    ↓ parse_repository()          [java_bridge.py — existing ✅]
    ↓ CallGraph.build()           [call_graph.py — existing ✅]
    ↓ DependencyGraph.build()     [dependency_graph.py — existing ✅]
    ↓ SymbolIndex.build()         [symbol_index.py — existing ✅]
    ↓ embed each method snippet   [embeddings.py — existing ✅]
    ↓ VectorStore.add()           [vector_store.py — existing ✅]
    ↓ PERSIST to disk             [NEW — RepoIndex serialization]
    ↓ IndexRepositoryResponse     [NEW — routes.py]
```

### Tasks

#### 1.1 Create `backend/app/acor/scorer.py`
The test file `test_scorer.py` fully specifies the required API:

```python
@dataclass
class Candidate:
    method_id: str
    class_id: str
    name: str
    kind: str          # "method" | "class"
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    token_count: int
    raw_semantic_score: float

@dataclass
class ScoredCandidate(Candidate):
    score: ScoreBreakdown

def _min_max_normalize(values: list[float]) -> list[float]:
    # All-equal → return all 1.0 (not 0.0 — everyone gets full credit)
    # Empty → return []

def _resolve_anchors(
    anchor_symbols: set[str],
    symbol_index: SymbolIndex
) -> tuple[set[str], set[str]]:
    # Returns (method_anchor_ids, class_anchor_ids)
    # Uses symbol_index.exact_lookup() for each anchor symbol

def score_candidates(
    candidates: list[Candidate],
    query: QueryAnalysis,
    call_graph: CallGraph,
    dependency_graph: DependencyGraph,
    symbol_index: SymbolIndex,
    weights: ACORWeights,
    max_hops: int
) -> list[ScoredCandidate]:
    # 1. Resolve anchor symbols → method/class anchor IDs
    # 2. Min-max normalize raw_semantic_score across the candidate set
    # 3. For each candidate:
    #    - semantic = normalized semantic score
    #    - call_graph = call_graph.proximity(method_id, method_anchors, max_hops)
    #    - dependency = dependency_graph.proximity(class_id, class_anchors, max_hops)
    #    - symbol = symbol_index.match_score(method_id, name, query.query_tokens)
    #    - composite = w_sem*semantic + w_cg*call_graph + w_dep*dependency + w_sym*symbol
    # 4. Return sorted by composite descending
```

#### 1.2 Create `backend/app/acor/ranker.py`
The test file `test_ranker.py` fully specifies the required API:

```python
@dataclass
class RankResult:
    selected: list[ScoredCandidate]   # within budget, sorted by composite desc
    dropped: list[ScoredCandidate]    # did not make budget
    total_tokens: int                  # sum of selected token_counts

def _greedy_by_density(
    candidates: list[ScoredCandidate],
    token_budget: int
) -> set[str]:
    # Sort by (composite / max(token_count, 1)) descending
    # Greedily add while total_tokens <= token_budget
    # Zero-token candidates included freely

def _exact_knapsack(
    candidates: list[ScoredCandidate],
    token_budget: int
) -> set[str]:
    # Standard 0/1 DP knapsack
    # Cap effective_budget at min(token_budget, sum(all token_counts))
    # Bucket token costs to keep table size reasonable

def rank_and_select(
    candidates: list[ScoredCandidate],
    token_budget: int,
    settings: Settings
) -> RankResult:
    # Validate: token_budget > 0
    # Choose solver: greedy (default) or exact (if len <= EXACT_KNAPSACK_MAX_CANDIDATES)
    # Return RankResult with selected + dropped disjoint, union = all candidates
```

#### 1.3 Create `backend/app/acor/context_builder.py`

```python
def build_context(
    selected: list[ScoredCandidate],
    repo_root: Path
) -> str:
    """
    Groups selected candidates by file, then renders:

    // ─── com/foo/OrderService.java ─────────────────────────
    // Lines 10-25 | OrderService.processOrder | relevance 0.87
    <source code snippet>

    // Lines 30-45 | OrderService.validate | relevance 0.71
    <source code snippet>

    // ─── com/foo/PaymentGateway.java ──────────────────────
    ...
    """
    # Group by file_path
    # Sort files by max composite score in that file (most relevant file first)
    # Within each file, sort by start_line
    # Call repo_walker.read_snippet(repo_root / candidate.file_path, start, end)
    # Assemble formatted string
```

#### 1.4 Create `backend/app/core/repo_index.py` (new file — persistence)

```python
@dataclass
class RepoIndex:
    repo_id: str
    repo_root: Path
    fingerprint: str
    call_graph: CallGraph
    dependency_graph: DependencyGraph
    symbol_index: SymbolIndex
    vector_store: VectorStore   # FAISS (in-memory) or Chroma (persistent)
    # Metadata
    num_files: int
    num_classes: int
    num_methods: int
    num_call_edges: int

    def save(self, storage_dir: Path) -> None:
        """Pickle graphs + symbol index. Chroma persists itself; FAISS saved separately."""

    @classmethod
    def load(cls, repo_id: str, storage_dir: Path, settings: Settings) -> "RepoIndex":
        """Load from disk. Raises FileNotFoundError if not found."""
```

#### 1.5 Create `backend/app/api/routes.py` — indexing endpoint only

```python
router = APIRouter()
_repo_registry: dict[str, RepoIndex] = {}  # in-memory + disk-backed

@router.post("/repositories/index", response_model=IndexRepositoryResponse)
async def index_repository(request: IndexRepositoryRequest, settings=Depends(get_settings)):
    t0 = time.perf_counter()

    repo_path = Path(request.repo_path)
    repo_id = request.repo_id or repo_path.name

    # Fingerprint check
    fingerprint = compute_repo_fingerprint(repo_path)
    if not request.force_reindex and repo_id in _repo_registry:
        if _repo_registry[repo_id].fingerprint == fingerprint:
            raise HTTPException(304, "Already indexed and unchanged")

    # Parse
    parsed = parse_repository(repo_path, settings)

    # Build graphs
    call_graph = CallGraph.build(parsed.methods, parsed.call_edges)
    dep_graph = DependencyGraph.build(parsed.classes, parsed.import_edges)
    symbol_index = SymbolIndex.build(parsed.classes, parsed.methods)

    # Embed + index
    embedding_model = get_embedding_model(settings)
    vector_store = get_vector_store(settings, collection_name=repo_id)
    snippets = [read_snippet(repo_path / m.file_path, m.start_line, m.end_line) for m in parsed.methods]
    vectors = embedding_model.encode(snippets)
    metadatas = [{"method_id": m.id, "class_id": m.class_id, "name": m.name,
                  "file_path": m.file_path, "start_line": m.start_line, "end_line": m.end_line}
                 for m in parsed.methods]
    vector_store.add([m.id for m in parsed.methods], vectors, metadatas)

    # Store
    repo_index = RepoIndex(repo_id=repo_id, repo_root=repo_path, fingerprint=fingerprint,
                           call_graph=call_graph, dependency_graph=dep_graph,
                           symbol_index=symbol_index, vector_store=vector_store, ...)
    repo_index.save(settings.index_storage_dir)
    _repo_registry[repo_id] = repo_index

    return IndexRepositoryResponse(repo_id=repo_id, ..., index_time_seconds=time.perf_counter()-t0)

@router.get("/repositories/{repo_id}/status")
async def repository_status(repo_id: str):
    ...
```

### Phase 1 Exit Criteria
- [ ] `POST /repositories/index` returns `IndexRepositoryResponse` for a real Java repo
- [ ] Graphs + symbol index saved to `INDEX_STORAGE_DIR`
- [ ] Server restart → index reloaded from disk → no re-parsing needed
- [ ] `scripts/index_repository.py --repo /path/to/java-repo` completes successfully

---

## Phase 2 — Query Pipeline (Both Paths)
**Goal:** Both root-cause and change-impact query paths work end-to-end. ACOR scores, ranks, selects, builds context, calls LLM.
**Effort:** ~3 days
**Depends on:** Phase 1 complete

### Architecture view — what Phase 2 implements
```
Query
    ↓ analyze_query()             [query_understanding.py — existing ✅]
    │
    ├──── ROOT-CAUSE PATH ─────────────────────────────────────────
    │     ↓ semantic search top-K   [vector_store.search() ✅]
    │     ↓ graph expansion         [call_graph.neighbors_within_hops() ✅ — WIRE IT]
    │     ↓ ACOR weights: semantic+call_graph+symbol heavy
    │
    └──── CHANGE-IMPACT PATH ──────────────────────────────────────
          ↓ anchor symbol lookup    [symbol_index.exact_lookup() ✅ — WIRE IT]
          ↓ graph BFS from symbol   [call_graph.neighbors_within_hops() ✅ — WIRE IT]
          ↓ semantic search top-K   [vector_store.search() ✅]
          ↓ ACOR weights: call_graph+dependency heavy

Both converge:
    ↓ score_candidates()          [acor/scorer.py — Phase 1]
    ↓ rank_and_select()           [acor/ranker.py — Phase 1]
    ↓ build_context()             [acor/context_builder.py — Phase 1]
    ↓ llm_client.generate()       [providers.py — existing ✅]
    ↓ QueryResponse               [routes.py — Phase 2]
```

### Tasks

#### 2.1 Create `backend/app/core/candidate_pipeline.py`
Encapsulates the two candidate generation paths as separate functions:

```python
def generate_root_cause_candidates(
    query: QueryAnalysis,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings
) -> list[Candidate]:
    """
    1. Embed query → semantic search top-K
    2. For each anchor symbol found: expand call_graph.neighbors_within_hops(hop=2)
       to pull in direct callers/callees of the named method
    3. Deduplicate, read snippets, count tokens
    4. Return list[Candidate]
    """

def generate_change_impact_candidates(
    changed_symbol: str,
    repo_index: RepoIndex,
    embedding_model: EmbeddingModel,
    settings: Settings,
    hop_limit: int = 3
) -> list[Candidate]:
    """
    1. Resolve changed_symbol → method IDs via symbol_index.exact_lookup()
    2. BFS from each resolved method ID via call_graph.neighbors_within_hops(hop_limit)
    3. Also: DependencyGraph neighbors of the changed symbol's class
    4. Supplement with semantic search (lower top-K — e.g., top-20)
    5. Deduplicate, read snippets, count tokens
    6. Return list[Candidate]
    """
```

#### 2.2 Complete `backend/app/api/routes.py` — query endpoint

```python
@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
async def query_repository(repo_id: str, request: QueryRequest, settings=Depends(get_settings)):
    repo_index = _get_repo_index(repo_id, settings)  # load from memory or disk
    query = analyze_query(request.query)
    token_budget = request.token_budget or settings.default_token_budget

    t_retrieval = LatencyTracker(); t_retrieval.start()

    # Choose candidate path based on intent
    if query.intent == QueryIntentLabel.ROOT_CAUSE_ANALYSIS:
        weights = ACORWeights(semantic=0.35, call_graph=0.30, dependency=0.10, symbol=0.25)
        candidates = generate_root_cause_candidates(query, repo_index, embedding_model, settings)
    else:
        weights = settings.acor_weights  # default weights for other intents
        candidates = generate_root_cause_candidates(query, repo_index, embedding_model, settings)

    # naive_baseline = sum of all candidate token counts before budget cut
    naive_baseline_tokens = sum(c.token_count for c in candidates)

    # ACOR
    scored = score_candidates(candidates, query, repo_index.call_graph,
                              repo_index.dependency_graph, repo_index.symbol_index,
                              weights, settings.graph_max_hops)
    rank_result = rank_and_select(scored, token_budget, settings)
    context_text = build_context(rank_result.selected, repo_index.repo_root)
    retrieval_ms = t_retrieval.stop_ms()

    # LLM
    t_llm = LatencyTracker(); t_llm.start()
    llm_response = get_llm_client(settings).generate(SYSTEM_PROMPT,
                       build_user_prompt(request.query, query.intent, context_text))
    llm_ms = t_llm.stop_ms()

    return QueryResponse(
        repo_id=repo_id, query=request.query, detected_intent=query.intent,
        answer=llm_response.text,
        retrieved_context=[...],  # from rank_result.selected
        candidates_considered=len(candidates),
        candidates_selected=len(rank_result.selected),
        total_context_tokens=rank_result.total_tokens,
        token_budget=token_budget,
        naive_baseline_tokens=naive_baseline_tokens,
        retrieval_latency_ms=retrieval_ms,
        llm_latency_ms=llm_ms,
    )
```

#### 2.3 Intent-adaptive ACOR weights
Different intents warrant different weight profiles:

```python
INTENT_WEIGHTS: dict[QueryIntentLabel, ACORWeights] = {
    QueryIntentLabel.ROOT_CAUSE_ANALYSIS:
        ACORWeights(semantic=0.35, call_graph=0.30, dependency=0.10, symbol=0.25),
    QueryIntentLabel.BUG_EXPLANATION:
        ACORWeights(semantic=0.40, call_graph=0.25, dependency=0.10, symbol=0.25),
    QueryIntentLabel.FEATURE_LOCATION:
        ACORWeights(semantic=0.30, call_graph=0.15, dependency=0.15, symbol=0.40),
    QueryIntentLabel.REFACTORING:
        ACORWeights(semantic=0.45, call_graph=0.15, dependency=0.25, symbol=0.15),
    QueryIntentLabel.GENERAL_QA:
        ACORWeights(semantic=0.50, call_graph=0.15, dependency=0.15, symbol=0.20),
}
```
*(These are starting defaults — the Defects4J pilot will reveal which tunings work.)*

### Phase 2 Exit Criteria
- [ ] `POST /repositories/{repo_id}/query` returns `QueryResponse` with actual LLM answer
- [ ] `retrieved_context` contains real code snippets with correct file paths + line numbers
- [ ] `total_context_tokens <= token_budget` always true
- [ ] `naive_baseline_tokens` populated (enables savings display in frontend)
- [ ] Frontend shows answer + budget meter + per-candidate scores
- [ ] `pytest backend/tests/ -v` — all 3 test files pass
- [ ] `scripts/index_repository.py --repo ... --query "..."` produces meaningful output

---

## Phase 3 — Defects4J Evaluation Pilot
**Goal:** Validate the ACOR pipeline against known bugs. Measure real numbers. Run ablations.
**Effort:** ~1 week
**Depends on:** Phase 2 complete

### What Defects4J gives you
Defects4J is a curated dataset of real Java bugs with:
- Exact repo + commit for each bug
- The changed files/methods that fix the bug (ground-truth relevant symbols)
- Test cases that reproduce the bug

This is a perfect gold standard for evaluating ACOR's `precision_recall_at_k()`.

### Tasks

#### 3.1 Setup (1-2 days)
- Install Defects4J: `https://github.com/rjust/defects4j`
- Pick 5-8 bugs from well-known projects: `Chart`, `Lang`, `Math`, `Time`, `Closure`
- For each bug: checkout the buggy version, note the ground-truth fix file + methods

Suggested starter set:
```
Chart-1   (org.jfree.chart)
Lang-6    (org.apache.commons.lang3)
Lang-10
Math-5    (org.apache.commons.math3)
Math-11
Time-4    (org.joda.time)
Closure-62
Closure-73
```

#### 3.2 Index each buggy repo
```bash
python scripts/index_repository.py \
  --repo /defects4j/Chart-1/buggy \
  --repo-id chart-1
```

#### 3.3 Create evaluation script
New file: `backend/app/evaluation/defects4j_pilot.py`

```python
PILOT_BUGS = [
    {
        "repo_id": "chart-1",
        "query": "Why does the chart rendering fail with an ArrayIndexOutOfBoundsException?",
        "gold_methods": {"org.jfree.chart.renderer.category.AbstractCategoryItemRenderer#drawDomainGridline"},
    },
    # ... 7 more
]

def run_pilot():
    for bug in PILOT_BUGS:
        result = query_repo(bug["repo_id"], bug["query"])
        retrieved_ids = [item.symbol_name for item in result.retrieved_context]
        metrics = precision_recall_at_k(retrieved_ids, bug["gold_methods"], k=10)
        token_reduction = token_reduction_ratio(result.total_context_tokens,
                                                result.naive_baseline_tokens)
        print(f"{bug['repo_id']}: P@10={metrics.precision_at_k:.2f} "
              f"R@10={metrics.recall_at_k:.2f} "
              f"token_reduction={token_reduction:.2f}")
```

#### 3.4 Run ablations (compare configurations)

For each bug, run 4 configurations and record P@10, R@10:

| Configuration | Weights |
|---|---|
| Semantic-only baseline | semantic=1.0, rest=0.0 |
| Graph-only baseline | call_graph=0.5, dependency=0.5, rest=0.0 |
| Symbol-only baseline | symbol=1.0, rest=0.0 |
| Full ACOR (all 4 terms) | Default intent-adaptive weights |

Also compare:
- Greedy ranker vs. exact knapsack (same candidates, different selection)
- With vs. without graph expansion in candidate generation

#### 3.5 Report results
The evaluation chapter should contain:
- Table: per-bug P@10, R@10, F1@10, token_reduction for each configuration
- Finding: which ACOR weight combination works best per intent type
- Finding: does graph expansion improve recall?
- Finding: does exact knapsack produce meaningfully better subsets than greedy?

### Phase 3 Exit Criteria
- [ ] 5-8 Defects4J bugs indexed and queryable
- [ ] Pilot script produces a results table
- [ ] Full ACOR outperforms semantic-only baseline on at least 4/8 bugs (P@10)
- [ ] Token reduction consistently >40% vs. naive baseline
- [ ] Ablation table completed

---

## Phase 4 — MCP Server
**Goal:** Cursor / Copilot / Antigravity can call CodeLens as an MCP tool. The MCP layer is thin — it reuses all Phase 1–3 pipeline code.
**Effort:** ~3 days
**Depends on:** Phase 2 complete + Phase 3 validation (at least partial)

> [!IMPORTANT]
> Do NOT implement Phase 4 before Phase 2 is working end-to-end. The MCP layer is thin glue — if the Core pipeline is broken, the MCP server just exposes broken behavior with extra ceremony.

### Architecture

```
Cursor / Copilot / Antigravity
    │
    │  MCP protocol (JSON-RPC over stdio or SSE)
    ▼
backend/mcp/server.py        ← NEW thin MCP server
    │  calls
    ▼
backend/app/core/            ← Phase 1-2 pipeline, unchanged
    candidate_pipeline.py
    repo_index.py
    │
    ▼
backend/app/acor/            ← Phase 1-2 ACOR engine, unchanged
    scorer.py
    ranker.py
    context_builder.py
```

### Tasks

#### 4.1 Create `backend/mcp/server.py`
Use the `mcp` Python SDK (`pip install mcp`):

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("codelens")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="find_root_cause_context",
            description=(
                "Given a developer query about a bug or error in a Java repository, "
                "returns the most relevant code context (methods, classes) selected "
                "by ACOR under the specified token budget. Use this when you need "
                "to understand WHY something fails or WHERE a bug originates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID (indexed via /repositories/index)"},
                    "query": {"type": "string", "description": "Developer question about the bug or error"},
                    "token_budget": {"type": "integer", "default": 8000, "description": "Max tokens of context to return"},
                },
                "required": ["repo_id", "query"],
            },
        ),
        Tool(
            name="find_change_impact_context",
            description=(
                "Given a symbol that is about to be changed (method name, class name), "
                "returns the code context most likely to be affected by that change — "
                "callers, dependents, and related classes — selected under the token budget."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "changed_symbol": {"type": "string", "description": "Method or class name being modified"},
                    "token_budget": {"type": "integer", "default": 8000},
                    "hop_limit": {"type": "integer", "default": 3, "description": "Graph traversal depth"},
                },
                "required": ["repo_id", "changed_symbol"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    settings = get_settings()
    repo_index = _load_repo_index(arguments["repo_id"], settings)
    token_budget = arguments.get("token_budget", settings.default_token_budget)

    if name == "find_root_cause_context":
        query = analyze_query(arguments["query"])
        candidates = generate_root_cause_candidates(query, repo_index, get_embedding_model(settings), settings)
        weights = INTENT_WEIGHTS[query.intent]

    elif name == "find_change_impact_context":
        # Synthesize a QueryAnalysis for the changed symbol
        query = QueryAnalysis(
            raw_query=arguments["changed_symbol"],
            intent=QueryIntentLabel.GENERAL_QA,
            anchor_symbols={arguments["changed_symbol"]},
            query_tokens=tokenize_identifier(arguments["changed_symbol"]),
        )
        candidates = generate_change_impact_candidates(
            arguments["changed_symbol"], repo_index,
            get_embedding_model(settings), settings,
            hop_limit=arguments.get("hop_limit", 3)
        )
        weights = ACORWeights(semantic=0.15, call_graph=0.45, dependency=0.30, symbol=0.10)

    scored = score_candidates(candidates, query, repo_index.call_graph,
                              repo_index.dependency_graph, repo_index.symbol_index,
                              weights, settings.graph_max_hops)
    rank_result = rank_and_select(scored, token_budget, settings)
    context_text = build_context(rank_result.selected, repo_index.repo_root)

    # Return structured context + metadata (no LLM call — the calling AI agent does that)
    return [TextContent(
        type="text",
        text=json.dumps({
            "context": context_text,
            "candidates_selected": len(rank_result.selected),
            "candidates_considered": len(candidates),
            "total_context_tokens": rank_result.total_tokens,
            "token_budget": token_budget,
            "token_reduction_pct": round((1 - rank_result.total_tokens /
                                         max(1, sum(c.token_count for c in candidates))) * 100, 1),
            "retrieved_items": [
                {"symbol": c.name, "file": c.file_path, "lines": f"{c.start_line}-{c.end_line}",
                 "relevance": round(c.score.composite, 3)}
                for c in rank_result.selected
            ],
        }, indent=2)
    )]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

#### 4.2 MCP configuration for Cursor / Antigravity
Create `mcp.json` at project root:
```json
{
  "mcpServers": {
    "codelens": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/CodeLens/backend",
      "env": {
        "JAVA_PARSER_JAR_PATH": "/path/to/parser-service/target/parser-service-1.0.0.jar",
        "INDEX_STORAGE_DIR": "/path/to/data/index",
        "VECTOR_DB_BACKEND": "chroma",
        "CHROMA_PERSIST_DIR": "/path/to/data/chroma"
      }
    }
  }
}
```

#### 4.3 Key design constraint: MCP server has NO LLM
The MCP server returns **optimized context**, not a final answer. The calling AI agent (Cursor / Copilot / Antigravity) performs the final reasoning with that context. This is the correct architecture — CodeLens is a context-intelligence layer, not an LLM replacement.

The existing `Providers.py` + `Prompts.py` remain for the **website/evaluation mode** (when someone uses the frontend directly). They are NOT called from the MCP path.

### Phase 4 Exit Criteria
- [ ] `mcp.json` registered in Cursor or Antigravity
- [ ] `find_root_cause_context("chart-1", "Why does chart rendering fail?", 8000)` returns real code context
- [ ] `find_change_impact_context("chart-1", "drawDomainGridline", 4000)` returns affected callers
- [ ] Tool responses include `context_text` + metadata (selected count, tokens, reduction %)
- [ ] Calling AI agent (Cursor/Antigravity) can use returned context to answer developer questions

---

## Summary Table

| Phase | Goal | New Files | Effort | Unblocks |
|---|---|---|---|---|
| **0** | Foundation | config.py, schemas.py, __init__.py × 8, rename 11 files | 1 day | Everything |
| **1** | Indexing pipeline | scorer.py, ranker.py, context_builder.py, repo_index.py, routes.py (partial) | 3 days | Phase 2 |
| **2** | Query pipeline (both paths) | candidate_pipeline.py, routes.py (complete) | 3 days | Phase 3, 4 |
| **3** | Defects4J evaluation | defects4j_pilot.py, evaluation scripts | 1 week | Phase 4 confidence |
| **4** | MCP server | mcp/server.py, mcp.json | 3 days | Cursor/Copilot integration |

**Total: ~3.5 weeks of focused development** to reach Phase 4 (MCP integration).
**To Phase 2 (full pipeline, no MCP): ~1 week.**

---

## Dependency Graph

```
Phase 0 (Foundation fixes)
    │
    ▼
Phase 1 (Indexing: scorer + ranker + context_builder + routes/index)
    │
    ▼
Phase 2 (Query: candidate_pipeline + routes/query + intent weights)
    │
    ├──────────────────▼
    │             Phase 3 (Evaluation pilot)
    │                  │ validates algorithm
    ▼                  ▼
Phase 4 (MCP server — thin layer on Phase 1-2 core)
```

> [!NOTE]
> Phase 3 (evaluation) and Phase 4 (MCP) can run in parallel once Phase 2 is stable. One team member can start indexing Defects4J repos while another builds the MCP server.
