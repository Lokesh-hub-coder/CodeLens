# CodeLens

**Adaptive Context Optimization for Repository-Scale AI Code Understanding**

CodeLens takes a natural-language developer query about a large Java
repository (a bug report, "why does X fail," "where is Y implemented") and
builds the smallest, most relevant slice of the repository's actual source
-- under a hard token budget -- before handing it to an LLM for bug
explanation, root-cause analysis, or a suggested fix. The core contribution
is **ACOR (Adaptive Context Optimization and Ranking)**: a scoring and
budget-constrained selection algorithm that fuses semantic similarity with
call-graph proximity, dependency-graph proximity, and symbol matching,
instead of relying on embedding similarity alone.

This repository is a working scaffold, not a finished product: every
pipeline stage is implemented with real logic (not stubs), but a handful
of MVP simplifications are deliberately called out in
[`docs/architecture.md`](docs/architecture.md) rather than hidden, since
knowing exactly what's simplified -- and why -- is what turns this from
"AI-generated boilerplate" into something you can defend in a viva.

## Start here

- [`docs/architecture.md`](docs/architecture.md) -- how every module maps
  to the pipeline diagram, and the explicit list of MVP simplifications
  with their upgrade paths.
- [`docs/ACOR_algorithm.md`](docs/ACOR_algorithm.md) -- the scoring
  formula, the knapsack-based ranker, and a detailed positioning of ACOR
  against all six papers in the original synopsis plus two closely
  related papers (RepoHyper, GraphCoder) worth reading before your
  literature review is finalized.

## Repository layout

```
codelens-ai/
├── backend/                  FastAPI service -- the pipeline orchestrator
│   ├── app/
│   │   ├── main.py           App factory, CORS, health check
│   │   ├── config.py         All tunables (ACOR weights, budget, model choice) in one place
│   │   ├── api/               Request/response schemas + route handlers
│   │   ├── parsing/           Repo file walking + the subprocess bridge to parser-service
│   │   ├── graphs/             Call graph, dependency graph, symbol index
│   │   ├── retrieval/          Embeddings, vector store (FAISS/Chroma), query understanding
│   │   ├── acor/                scorer.py -> ranker.py -> context_builder.py -- the core algorithm
│   │   ├── llm/                 Provider-agnostic client (OpenAI/Llama/Qwen) + prompt templates
│   │   └── evaluation/          Precision/recall, token-reduction, latency metrics
│   ├── tests/                  pytest unit tests (call graph, scorer, ranker)
│   ├── requirements.txt
│   └── Dockerfile
├── parser-service/           JavaParser-based CLI (JVM) -- the actual AST/symbol resolution
│   ├── pom.xml
│   └── src/main/java/com/codelensai/parser/
│       ├── ParserApplication.java   CLI entry point
│       ├── RepoParser.java          AST walk, call/import edge extraction
│       └── model/CodeEntities.java  JSON output model (contract with java_bridge.py)
├── frontend/                 React + Vite developer console
│   └── src/
│       ├── App.jsx
│       ├── api.js
│       └── components/{QueryPanel,ContextViewer}.jsx
├── docs/
│   ├── architecture.md
│   └── ACOR_algorithm.md
├── scripts/
│   └── index_repository.py   CLI smoke-test against a running backend
├── docker-compose.yml
├── .env.example
└── .gitignore
```

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env   # then edit ../.env -- at minimum set an LLM API key
```

### 2. Parser service (build once, or whenever RepoParser.java changes)

```bash
cd parser-service
mvn -q package
# produces target/parser-service-1.0.0.jar -- path referenced by
# JAVA_PARSER_JAR_PATH in .env
```

### 3. Run the backend

```bash
cd backend
uvicorn app.main:app --reload
# interactive API docs at http://localhost:8000/docs
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173
```

### 5. Or all at once with Docker Compose

```bash
docker compose up --build
```

### 6. Smoke-test without the frontend

```bash
python scripts/index_repository.py --repo /path/to/some/java/repo \
  --query "why does processOrder throw a NullPointerException when the cart is empty?"
```

### Run the tests

```bash
cd backend
pytest -v
```

**A note on this sandbox's build verification:** every Python file in this
scaffold was syntax-checked (`python -m py_compile`) and the core
algorithms (knapsack ranker, graph proximity decay, camelCase symbol
tokenization) were independently verified against hand-built test cases
during development -- but this environment had no network access, so
`pip install`, `npm install`, and `mvn package` have **not** actually been
run end-to-end here. Budget time in Week 5 for a first real build to catch
anything a static read-through can't (dependency version conflicts,
JavaParser API drift, etc.) -- see "What to think about beyond the papers"
below.

## Configuration

Every tunable lives in `.env` (see `.env.example` for the full list with
comments): token budget, ACOR component weights, graph hop limit, ranker
strategy, embedding model, vector store backend, and LLM provider/model.
Nothing pipeline-relevant is hardcoded in application code.

## Suggested build order (mapping onto a typical Week 5-14 timeline)

1. **Parser service first, in isolation.** Get `mvn package` producing a
   JAR, and run it by hand against a small real Java repo (2-3 classes) to
   sanity-check the output JSON before wiring up anything else. This is
   the module most likely to need iteration against real-world code (see
   below).
2. **Indexing endpoint end to end** against that same small repo:
   `/repositories/index` should return sane class/method/edge counts.
3. **Query endpoint with a trivial query** ("what does OrderService do")
   to validate the ACOR -> LLM handoff before tuning weights.
4. **Frontend** once the API is stable -- it's a thin client and shouldn't
   block backend iteration.
5. **Evaluation harness** (Section 7 of the synopsis) once the pipeline is
   stable enough to produce consistent results to measure.

## What to think about beyond the six papers

The full write-up is in the chat response this project was generated
from, but the short version, roughly in the order you'll hit it:

1. **JavaParser will fail on some real-world files** -- generics edge
   cases, unresolvable third-party dependencies not on any configured
   classpath, annotation processors, very new language features. The
   `unparsed_files` field exists precisely so a handful of bad files
   degrade gracefully instead of failing the whole index. Budget time to
   look at *which* files fail on your actual test repositories.
2. **Where your evaluation ground truth comes from** is a decision, not a
   given -- see `docs/ACOR_algorithm.md` section 6.
3. **Token counting is a proxy, not an exact bill** for non-OpenAI
   providers -- see `acor/tokenizer.py`'s docstring.
4. **Privacy/licensing** if you test against real open-source repositories
   and send excerpts to a third-party LLM API -- check the repo's license
   and, if this ever handles proprietary code, whether the LLM provider's
   data-retention policy is acceptable.
5. **Cost**, if using a paid API for evaluation at scale -- budget it
   before running a 200-query evaluation sweep against GPT-4-class models.
6. **Latency vs. quality** -- the exact-knapsack ranker, larger
   `top_k_candidates`, and larger embedding models all trade speed for
   possible quality; report where you landed and why.



