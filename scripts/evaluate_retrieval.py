#!/usr/bin/env python3
"""
Retrieval Evaluation Script for CodeLens -- Stage 2
====================================================

Works with ANY indexed repository (not just Apache Commons Lang).
Queries are auto-generated from the actual symbol index of the loaded repo.

  - Precision@K, Recall@K, F1@K  (K = 5, 10, 20)
  - Mean Reciprocal Rank (MRR)
  - Mean Average Precision (MAP)
  - Hit Rate@K
  - Per-query breakdown table
  - Per-intent aggregate breakdown

Usage:
    python scripts/evaluate_retrieval.py
    python scripts/evaluate_retrieval.py --repo-id commons-lang
    python scripts/evaluate_retrieval.py --index-pkl path/to/repo.pkl
    python scripts/evaluate_retrieval.py --num-queries 30 --seed 99
"""
from __future__ import annotations

import argparse
import pickle
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Dynamic query generation from ANY repo's symbol index
# ---------------------------------------------------------------------------

INTENT_TEMPLATES: dict[str, list[str]] = {
    "FEATURE_LOCATION": [
        "where is {method} implemented?",
        "where is the {method} method in {cls}?",
        "find the implementation of {method}",
        "locate {method} in {cls}",
        "which class implements {method}?",
    ],
    "GENERAL_QA": [
        "how does {cls} handle {method}?",
        "how does {method} work in {cls}?",
        "explain the {method} method",
        "what does {cls}.{method} do?",
        "how is {method} used?",
    ],
    "ROOT_CAUSE_ANALYSIS": [
        "why might {method} fail?",
        "what could cause {method} to break?",
        "debug issue in {method} of {cls}",
        "trace the error in {method}",
        "what is the root cause of failures in {method}?",
    ],
}
INTENTS = list(INTENT_TEMPLATES.keys())


def _to_readable(name: str) -> str:
    """Convert camelCase/PascalCase/snake_case to readable words."""
    s = re.sub(r"([a-z])([ A-Z])", r"\1 \2", name)
    return s.replace("_", " ").lower()


def generate_query_set(symbol_index, num_queries: int = 25, seed: int = 42) -> list[dict]:
    """
    Build a query set from the actual symbol index of whatever repo was indexed.
    Picks random (class, method) pairs and creates natural-language queries.
    Gold labels = the exact method name (matched case-insensitively).
    """
    random.seed(seed)

    pairs: list[tuple[str, str]] = []
    for name, locs in symbol_index._exact.items():
        for loc in locs:
            try:
                from app.graphs.symbol_index import SymbolKind
                if loc.kind != SymbolKind.METHOD:
                    continue
            except Exception:
                pass
            class_part = loc.symbol_id.split("#")[0] if "#" in loc.symbol_id else ""
            class_name = class_part.split(".")[-1] if class_part else "Unknown"
            if class_name and name:
                pairs.append((class_name, name))

    if not pairs:
        print("  [WARN] No method pairs found in symbol index.")
        return []

    pairs = list(set(pairs))
    random.shuffle(pairs)

    query_set: list[dict] = []
    for i, (cls, method) in enumerate(pairs[:num_queries]):
        intent = INTENTS[i % len(INTENTS)]
        template = random.choice(INTENT_TEMPLATES[intent])
        try:
            query_text = template.format(method=_to_readable(method), cls=_to_readable(cls))
        except KeyError:
            query_text = f"find {_to_readable(method)} in {_to_readable(cls)}"
        query_set.append({
            "query": query_text,
            "intent": intent,
            "gold_method_names": {method},
            "gold_class_names":  {cls},
        })

    print(f"  Generated {len(query_set)} queries from {len(pairs)} (class, method) pairs.")
    return query_set


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    query:           str
    intent:          str
    retrieved_ids:   list
    retrieved_names: list
    gold_names:      set
    p_at_5:   float = 0.0
    p_at_10:  float = 0.0
    p_at_20:  float = 0.0
    r_at_5:   float = 0.0
    r_at_10:  float = 0.0
    r_at_20:  float = 0.0
    f1_at_5:  float = 0.0
    f1_at_10: float = 0.0
    f1_at_20: float = 0.0
    mrr:      float = 0.0
    ap:       float = 0.0
    hit_at_5:  bool = False
    hit_at_10: bool = False
    hit_at_20: bool = False
    latency_ms: float = 0.0
    num_candidates: int = 0


def compute_metrics(result: QueryResult) -> QueryResult:
    gold = {g.lower() for g in result.gold_names}
    names_lower = [n.lower() for n in result.retrieved_names]

    def p_r_f1(k: int):
        top = names_lower[:k]
        if not top:
            return 0.0, 0.0, 0.0
        hits = sum(1 for n in top if n in gold)
        p = hits / len(top)
        r = hits / len(gold) if gold else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        return p, r, f1

    result.p_at_5,  result.r_at_5,  result.f1_at_5  = p_r_f1(5)
    result.p_at_10, result.r_at_10, result.f1_at_10 = p_r_f1(10)
    result.p_at_20, result.r_at_20, result.f1_at_20 = p_r_f1(20)

    result.hit_at_5  = any(n in gold for n in names_lower[:5])
    result.hit_at_10 = any(n in gold for n in names_lower[:10])
    result.hit_at_20 = any(n in gold for n in names_lower[:20])

    # MRR
    result.mrr = 0.0
    for rank, name in enumerate(names_lower, start=1):
        if name in gold:
            result.mrr = 1.0 / rank
            break

    # Average Precision
    num_hits, cumulative_p = 0, 0.0
    for rank, name in enumerate(names_lower, start=1):
        if name in gold:
            num_hits += 1
            cumulative_p += num_hits / rank
    result.ap = cumulative_p / len(gold) if gold else 0.0

    return result


# ---------------------------------------------------------------------------
# Index loader + vector-store builder
# ---------------------------------------------------------------------------

def load_index_and_rebuild_vectors(
    pkl_path: Path,
    backend_dir: Path,
    embedding_model_name: str,
    embedding_dim: int,
):
    print(f"\n[1/4] Loading index pickle: {pkl_path}")
    with open(pkl_path, "rb") as f:
        payload = pickle.load(f)

    call_graph       = payload["call_graph"]
    dependency_graph = payload["dependency_graph"]
    symbol_index     = payload["symbol_index"]

    print(
        f"      num_methods={payload['num_methods']}  "
        f"num_classes={payload['num_classes']}  "
        f"num_call_edges={payload['num_call_edges']}"
    )

    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.config import Settings
    from app.retrieval.embeddings import get_embedding_model
    from app.retrieval.vector_store import FaissVectorStore
    from app.graphs.symbol_index import SymbolKind

    settings = Settings(
        embedding_model=embedding_model_name,
        embedding_dim=embedding_dim,
        vector_db_backend="faiss",
    )

    print(f"[2/4] Loading embedding model '{embedding_model_name}' ...")
    t0 = time.perf_counter()
    emb_model = get_embedding_model(settings)
    print(f"      Done in {(time.perf_counter() - t0) * 1000:.0f} ms")

    print("[3/4] Collecting method locations from symbol index ...")
    method_locations = []
    for name, locs in symbol_index._exact.items():
        for loc in locs:
            if loc.kind == SymbolKind.METHOD:
                method_locations.append(loc)
    print(f"      {len(method_locations)} method locations found")

    texts, ids, metadatas = [], [], []
    seen_ids: set = set()
    for loc in method_locations:
        if loc.symbol_id in seen_ids:
            continue
        seen_ids.add(loc.symbol_id)
        class_part = loc.symbol_id.split("#")[0] if "#" in loc.symbol_id else ""
        class_name = class_part.split(".")[-1] if class_part else ""
        readable = _to_readable(loc.name)
        text_repr = f"{class_name} {loc.name} {readable}".strip()
        texts.append(text_repr)
        ids.append(loc.symbol_id)
        metadatas.append({
            "method_id":  loc.symbol_id,
            "class_id":   class_part,
            "name":       loc.name,
            "file_path":  loc.file_path,
            "start_line": loc.start_line,
            "end_line":   loc.end_line,
        })

    print(f"[4/4] Embedding {len(texts)} unique methods into FAISS ...")
    import numpy as np
    BATCH = 64
    t0 = time.perf_counter()
    all_vecs = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i: i + BATCH]
        vecs = emb_model.encode(batch)
        all_vecs.append(vecs)
        done = min(i + BATCH, len(texts))
        if done % 640 == 0 or done == len(texts):
            print(f"      ... {done}/{len(texts)}", end="\r")
    print()
    all_vecs_np = np.vstack(all_vecs)
    embed_ms = (time.perf_counter() - t0) * 1000

    vector_store = FaissVectorStore(dim=embedding_dim)
    vector_store.add(ids, all_vecs_np, metadatas)
    print(f"      Embedded & indexed {vector_store.count()} vectors in {embed_ms:.0f} ms")

    return vector_store, emb_model, settings, call_graph, dependency_graph, symbol_index


# ---------------------------------------------------------------------------
# Single-query retrieval + ACOR scoring
# ---------------------------------------------------------------------------

def run_query(
    query_dict: dict,
    vector_store,
    emb_model,
    settings,
    call_graph,
    dependency_graph,
    symbol_index,
    top_k: int = 50,
) -> QueryResult:
    from app.retrieval.query_understanding import analyze_query
    from app.acor.scorer import score_candidates, Candidate
    from app.config import ACORWeights
    from app.api.schemas import QueryIntentLabel

    raw_query = query_dict["query"]
    t0 = time.perf_counter()

    qa      = analyze_query(raw_query)
    q_vec   = emb_model.encode_one(raw_query)
    results = vector_store.search(q_vec, top_k=top_k)

    candidates = []
    for sr in results:
        meta = sr.metadata
        candidates.append(Candidate(
            method_id=meta["method_id"],
            class_id=meta["class_id"],
            name=meta["name"],
            kind="method",
            file_path=meta["file_path"],
            start_line=meta["start_line"],
            end_line=meta["end_line"],
            snippet=meta["name"],
            token_count=1,
            raw_semantic_score=float(sr.score),
        ))

    num_candidates = len(candidates)

    # Intent-adaptive ACOR weights
    intent = qa.intent
    if intent == QueryIntentLabel.BUG_EXPLANATION:
        weights = ACORWeights(semantic=0.30, call_graph=0.35, dependency=0.20, symbol=0.15)
    elif intent == QueryIntentLabel.ROOT_CAUSE_ANALYSIS:
        weights = ACORWeights(semantic=0.25, call_graph=0.40, dependency=0.20, symbol=0.15)
    elif intent == QueryIntentLabel.FEATURE_LOCATION:
        weights = ACORWeights(semantic=0.35, call_graph=0.15, dependency=0.15, symbol=0.35)
    elif intent == QueryIntentLabel.REFACTORING:
        weights = ACORWeights(semantic=0.30, call_graph=0.25, dependency=0.30, symbol=0.15)
    else:
        weights = ACORWeights(semantic=0.40, call_graph=0.20, dependency=0.15, symbol=0.25)

    scored = score_candidates(
        candidates, qa, call_graph, dependency_graph, symbol_index,
        weights=weights, max_hops=4,
    )

    latency_ms = (time.perf_counter() - t0) * 1000

    result = QueryResult(
        query=raw_query,
        intent=query_dict["intent"],
        retrieved_ids=[c.method_id for c in scored],
        retrieved_names=[c.name for c in scored],
        gold_names=query_dict["gold_method_names"],
        latency_ms=latency_ms,
        num_candidates=num_candidates,
    )
    return compute_metrics(result)


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(results: list, repo_name: str = "unknown") -> None:
    SEP = "=" * 115
    sep = "-" * 115

    def avg(vals):
        return sum(vals) / len(vals) if vals else 0.0

    print("\n" + SEP)
    print("  CodeLens -- RETRIEVAL EVALUATION REPORT")
    print(f"  Repository : {repo_name}   |   Queries: {len(results)}")
    print(SEP)

    print(
        f"\n{'#':>3}  {'Query':<52}  "
        f"{'P@5':>5}  {'P@10':>5}  {'R@5':>5}  {'R@10':>5}  "
        f"{'F1@10':>6}  {'MRR':>5}  {'AP':>5}  "
        f"{'H@5':>4}  {'H@10':>5}  {'ms':>6}"
    )
    print(sep)
    for i, r in enumerate(results, 1):
        q   = (r.query[:50] + "..") if len(r.query) > 52 else r.query
        h5  = "Y" if r.hit_at_5  else "N"
        h10 = "Y" if r.hit_at_10 else "N"
        print(
            f"{i:>3}  {q:<52}  "
            f"{r.p_at_5:>5.3f}  {r.p_at_10:>5.3f}  "
            f"{r.r_at_5:>5.3f}  {r.r_at_10:>5.3f}  "
            f"{r.f1_at_10:>6.3f}  "
            f"{r.mrr:>5.3f}  {r.ap:>5.3f}  "
            f"{h5:>4}  {h10:>5}  {r.latency_ms:>6.1f}"
        )
    print(sep)

    def ml(label, vals, fmt=".4f"):
        print(f"  {label:<42} {avg(vals):{fmt}}")

    print(f"\n  AGGREGATE METRICS\n{'-'*60}")
    ml("Precision@5",  [r.p_at_5  for r in results])
    ml("Precision@10", [r.p_at_10 for r in results])
    ml("Precision@20", [r.p_at_20 for r in results])
    print()
    ml("Recall@5",     [r.r_at_5  for r in results])
    ml("Recall@10",    [r.r_at_10 for r in results])
    ml("Recall@20",    [r.r_at_20 for r in results])
    print()
    ml("F1@5",         [r.f1_at_5  for r in results])
    ml("F1@10",        [r.f1_at_10 for r in results])
    ml("F1@20",        [r.f1_at_20 for r in results])
    print()
    ml("MRR (Mean Reciprocal Rank)",   [r.mrr for r in results])
    ml("MAP (Mean Average Precision)", [r.ap  for r in results])
    print()
    ml("Hit Rate@5",   [float(r.hit_at_5)  for r in results])
    ml("Hit Rate@10",  [float(r.hit_at_10) for r in results])
    ml("Hit Rate@20",  [float(r.hit_at_20) for r in results])
    print()
    ml("Avg retrieval latency (ms)",    [r.latency_ms        for r in results], fmt=".1f")
    ml("Avg candidates before scoring", [float(r.num_candidates) for r in results], fmt=".1f")

    print(f"\n  PER-INTENT BREAKDOWN\n{'-'*60}")
    intents: dict = {}
    for r in results:
        intents.setdefault(r.intent, []).append(r)
    for intent_label, rs in sorted(intents.items()):
        print(
            f"  {intent_label:<28}  n={len(rs):>2}  "
            f"MRR={avg([r.mrr for r in rs]):.3f}  "
            f"P@10={avg([r.p_at_10 for r in rs]):.3f}  "
            f"R@10={avg([r.r_at_10 for r in rs]):.3f}  "
            f"Hit@10={avg([float(r.hit_at_10) for r in rs]):.2f}"
        )

    print(f"\n  TOP-5 RETRIEVED METHODS (first 5 queries)\n{'-'*80}")
    for r in results[:5]:
        gold_lower = {g.lower() for g in r.gold_names}
        top5 = r.retrieved_names[:5]
        print(f"\n  Q: {r.query}")
        print(f"     Gold: {', '.join(sorted(r.gold_names))}")
        for rank, name in enumerate(top5, 1):
            marker = "+" if name.lower() in gold_lower else " "
            print(f"     [{marker}] #{rank}  {name}")

    print("\n" + SEP)
    print("  END OF REPORT")
    print(SEP + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def pick_index_pkl(backend_dir: Path, repo_id: str | None) -> Path:
    """
    Find a .pkl index to evaluate:
    - If --repo-id given: load backend/data/index/<repo_id>.pkl
    - If multiple pkls exist: let the user pick interactively
    - If only one pkl: auto-select it
    """
    index_dir = backend_dir / "data" / "index"
    if not index_dir.is_dir():
        print(f"ERROR: index directory not found: {index_dir}", file=sys.stderr)
        sys.exit(1)

    if repo_id:
        p = index_dir / f"{repo_id}.pkl"
        if not p.is_file():
            print(f"ERROR: no index for repo '{repo_id}' at {p}", file=sys.stderr)
            sys.exit(1)
        return p

    pkls = sorted(index_dir.glob("*.pkl"))
    if not pkls:
        print(f"ERROR: no .pkl index files found in {index_dir}", file=sys.stderr)
        sys.exit(1)

    if len(pkls) == 1:
        print(f"  Auto-selected index: {pkls[0].name}")
        return pkls[0]

    print("\nAvailable indexed repositories:")
    for i, p in enumerate(pkls, 1):
        size_mb = p.stat().st_size / (1024 * 1024)
        print(f"  [{i}] {p.stem}  ({size_mb:.1f} MB)")

    while True:
        try:
            choice = input(f"\nSelect repo [1-{len(pkls)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(pkls):
                return pkls[idx]
        except (ValueError, EOFError):
            pass
        print("  Invalid choice, try again.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--backend-dir", default=None,
                   help="Path to backend/ dir (auto-detected)")
    p.add_argument("--repo-id", default=None,
                   help="Repo ID to evaluate, e.g. 'commons-lang'. "
                        "Loads backend/data/index/<repo-id>.pkl")
    p.add_argument("--index-pkl", default=None,
                   help="Direct path to a .pkl index file (overrides --repo-id)")
    p.add_argument("--num-queries", type=int, default=25,
                   help="Number of auto-generated queries (default: 25)")
    p.add_argument("--top-k", type=int, default=50,
                   help="Candidates per query from vector store (default: 50)")
    p.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--embedding-dim", type=int, default=384)
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for query generation (default: 42)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    script_dir   = Path(__file__).resolve().parent
    project_root = script_dir.parent
    backend_dir  = Path(args.backend_dir) if args.backend_dir else project_root / "backend"

    if not backend_dir.is_dir():
        print(f"ERROR: backend dir not found: {backend_dir}", file=sys.stderr)
        sys.exit(1)

    # Resolve which .pkl to load
    if args.index_pkl:
        pkl_path = Path(args.index_pkl)
        if not pkl_path.is_file():
            print(f"ERROR: index not found: {pkl_path}", file=sys.stderr)
            sys.exit(1)
    else:
        pkl_path = pick_index_pkl(backend_dir, args.repo_id)

    repo_name = pkl_path.stem   # e.g. "commons-lang", "my-project", etc.

    env_file = backend_dir / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        except ImportError:
            pass

    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    (
        vector_store, emb_model, settings,
        call_graph, dependency_graph, symbol_index,
    ) = load_index_and_rebuild_vectors(
        pkl_path, backend_dir, args.embedding_model, args.embedding_dim,
    )

    print(f"\n  Generating queries for repo: '{repo_name}' ...")
    query_set = generate_query_set(
        symbol_index, num_queries=args.num_queries, seed=args.seed
    )

    if not query_set:
        print("ERROR: could not generate any queries from the symbol index.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'---'*20}")
    print(f"  Running {len(query_set)} queries  (top_k={args.top_k})")
    print(f"{'---'*20}")

    results = []
    for i, qd in enumerate(query_set, 1):
        label = qd["query"][:65]
        print(f"  [{i:>2}/{len(query_set)}]  {label:<65}", end="  ...", flush=True)
        r = run_query(
            qd, vector_store, emb_model, settings,
            call_graph, dependency_graph, symbol_index,
            top_k=args.top_k,
        )
        results.append(r)
        mark = "Hit" if r.hit_at_10 else "---"
        print(f"  {mark}@10  MRR={r.mrr:.3f}  {r.latency_ms:.0f}ms")

    print_report(results, repo_name)


if __name__ == "__main__":
    main()
