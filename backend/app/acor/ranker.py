"""ACOR Ranker - selects the highest-value subset of scored candidates
that fits within the caller-specified token budget.

Two strategies:
  greedy (default): O(n log n) density sort.  Fast and near-optimal for
      uniformly-sized candidates.
  exact:  0/1 DP knapsack.  Optimal but O(n * budget).  Used only when
      the candidate set is small enough (Settings.exact_knapsack_max_candidates).

The public entry point is rank_and_select(); the two inner functions are
exposed for direct testing by test_ranker.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from app.acor.context_builder import build_context
from app.acor.scorer import ScoredCandidate
from app.config import Settings
from app.retrieval.tokenizer import count_tokens


@dataclass
class RankResult:
    selected:     list[ScoredCandidate]
    dropped:      list[ScoredCandidate]
    total_tokens: int


# ---------------------------------------------------------------------------
# Greedy solver
# ---------------------------------------------------------------------------

def _greedy_by_density(
    candidates:   list[ScoredCandidate],
    token_budget: int,
) -> set[str]:
    """Select by density, while retaining the top-scoring candidate if it fits."""
    def density(c: ScoredCandidate) -> float:
        return c.score.composite / max(c.token_count, 1)

    sorted_c = sorted(candidates, key=density, reverse=True)
    selected: set[str] = set()
    used = 0
    for c in sorted_c:
        if c.token_count == 0:
            selected.add(c.method_id)
            continue
        if used + c.token_count <= token_budget:
            selected.add(c.method_id)
            used += c.token_count

    if candidates:
        highest_scoring = max(candidates, key=lambda c: c.score.composite)
        if (
            highest_scoring.method_id not in selected
            and highest_scoring.token_count <= token_budget
        ):
            for candidate in sorted(
                (c for c in candidates if c.method_id in selected),
                key=lambda c: c.score.composite,
            ):
                if used + highest_scoring.token_count <= token_budget:
                    break
                selected.remove(candidate.method_id)
                used -= candidate.token_count
            if used + highest_scoring.token_count <= token_budget:
                selected.add(highest_scoring.method_id)

    return selected


# ---------------------------------------------------------------------------
# Exact 0/1 knapsack solver
# ---------------------------------------------------------------------------

def _exact_knapsack(
    candidates:   list[ScoredCandidate],
    token_budget: int,
) -> set[str]:
    """Standard 0/1 DP knapsack.  Returns the optimal set of method IDs."""
    total_weight = sum(c.token_count for c in candidates)
    effective_budget = min(token_budget, total_weight)

    n = len(candidates)
    # dp[i][w] = best composite value using first i items, weight <= w
    dp = [[0.0] * (effective_budget + 1) for _ in range(n + 1)]

    for i, c in enumerate(candidates, start=1):
        w = c.token_count
        v = c.score.composite
        for cap in range(effective_budget + 1):
            dp[i][cap] = dp[i - 1][cap]
            if w <= cap:
                take = dp[i - 1][cap - w] + v
                if take > dp[i][cap]:
                    dp[i][cap] = take

    # Backtrack to find which items were selected
    selected: set[str] = set()
    cap = effective_budget
    for i in range(n, 0, -1):
        if dp[i][cap] != dp[i - 1][cap]:
            selected.add(candidates[i - 1].method_id)
            cap -= candidates[i - 1].token_count

    return selected


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def rank_and_select(
    candidates:   list[ScoredCandidate],
    token_budget: int,
    settings:     Settings,
) -> RankResult:
    """Select the best candidates that fit within token_budget.

    Raises ValueError for non-positive budgets (caller error, not a
    graceful degradation case).
    """
    if token_budget <= 0:
        raise ValueError(f"token_budget must be > 0, got {token_budget}")

    if not candidates:
        return RankResult(selected=[], dropped=[], total_tokens=0)

    # Keep one canonical item per method before either solver calculates cost.
    unique_by_id: dict[str, ScoredCandidate] = {}
    for candidate in candidates:
        if candidate.token_count < 0:
            raise ValueError(f"Candidate {candidate.method_id!r} has a negative token count")
        existing = unique_by_id.get(candidate.method_id)
        if existing is None or candidate.score.composite > existing.score.composite:
            unique_by_id[candidate.method_id] = candidate
    unique_candidates = list(unique_by_id.values())

    # Account for file and method headers during ranking. The final formatted
    # context is recounted below because multiple snippets in one file share
    # a file header and tokenization can change at concatenation boundaries.
    selection_items = [
        ScoredCandidate(
            **{
                key: getattr(candidate, key)
                for key in ScoredCandidate.__dataclass_fields__
                if key != "score" and key != "token_count"
            },
            token_count=count_tokens(build_context([candidate], Path("."))),
            score=candidate.score,
        )
        for candidate in unique_candidates
    ]

    if settings.ranker_strategy == "exact" and len(selection_items) <= settings.exact_knapsack_max_candidates:
        selected_ids = _exact_knapsack(selection_items, token_budget)
    else:
        selected_ids = _greedy_by_density(selection_items, token_budget)

    selected = sorted(
        [c for c in unique_candidates if c.method_id in selected_ids],
        key=lambda c: c.score.composite,
        reverse=True,
    )
    while selected and count_tokens(build_context(selected, Path("."))) > token_budget:
        selected.pop()

    selected_ids = {c.method_id for c in selected}
    dropped = [c for c in unique_candidates if c.method_id not in selected_ids]
    total_tokens = count_tokens(build_context(selected, Path("."))) if selected else 0

    return RankResult(selected=selected, dropped=dropped, total_tokens=total_tokens)