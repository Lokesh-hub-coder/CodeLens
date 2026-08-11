"""
Metrics for the evaluation the synopsis promises in Section 7 ("retrieval
precision, reasoning accuracy, latency, and token reduction") and Section 3
("Evaluate retrieval quality and reasoning performance" / "Reduce
hallucination, latency, and token consumption").

Retrieval precision/recall need a ground-truth "gold" set of relevant
methods per query -- this module computes the metrics but deliberately
does not decide where gold labels come from; see docs/architecture.md,
"Building an evaluation set", for the concrete options (RepoBench-style
existing labels vs. hand-labeling a query set from the team's own test
repository).

Reasoning-accuracy and hallucination are intentionally *not* implemented
as an automatic metric here: neither has a reliable reference-free
automatic score at this scale of effort (see the README section "Apart
from the papers: what else to plan for" for why this is flagged as a
methodology decision the team needs to make explicitly, e.g. human rubric
scoring or LLM-as-judge with its own caveats, rather than something this
scaffold should silently assume via one function).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    precision_at_k: float
    recall_at_k: float
    f1_at_k: float
    k: int


def precision_recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> RetrievalMetrics:
    """Standard IR precision/recall@k over method (or file) ids.

    retrieved_ids should already be in ranked order (ACOR's output order);
    only the top k are considered "retrieved" for this metric.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    top_k = retrieved_ids[:k]
    if not top_k:
        return RetrievalMetrics(precision_at_k=0.0, recall_at_k=0.0, f1_at_k=0.0, k=k)

    hits = sum(1 for item in top_k if item in relevant_ids)
    precision = hits / len(top_k)
    recall = hits / len(relevant_ids) if relevant_ids else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return RetrievalMetrics(precision_at_k=precision, recall_at_k=recall, f1_at_k=f1, k=k)


def token_reduction_ratio(optimized_tokens: int, naive_baseline_tokens: int) -> float:
    """Fraction of tokens ACOR saved versus a naive baseline (e.g. "send
    every file that shares a keyword with the query"). 0.0 = no savings,
    close to 1.0 = drastic reduction. Guards against a degenerate
    zero-token baseline rather than raising, since that case only arises
    from a misconfigured baseline computation upstream and shouldn't crash
    an evaluation run over it.
    """
    if naive_baseline_tokens <= 0:
        return 0.0
    return max(0.0, 1.0 - (optimized_tokens / naive_baseline_tokens))


@dataclass
class LatencyTracker:
    """Minimal timing helper so retrieval_latency_ms and llm_latency_ms in
    QueryResponse are measured the same way everywhere they're reported,
    rather than each route handler rolling its own time.perf_counter()
    bookkeeping."""

    _start: float | None = None

    def start(self) -> None:
        import time

        self._start = time.perf_counter()

    def stop_ms(self) -> float:
        import time

        if self._start is None:
            raise RuntimeError("LatencyTracker.stop_ms() called before start()")
        elapsed = (time.perf_counter() - self._start) * 1000
        self._start = None
        return elapsed