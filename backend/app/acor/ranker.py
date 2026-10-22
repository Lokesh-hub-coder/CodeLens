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

from app.acor.scorer import ScoredCandidate
from app.config import Settings


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
    """Select method IDs by descending composite/token_count density."""
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

    # Choose solver
    use_exact = (
        settings.ranker_strategy == "exact"
        or len(candidates) <= settings.exact_knapsack_max_candidates
        and settings.ranker_strategy != "greedy"
    )
    # Prioritize top-scoring primary target candidates
    sorted_by_composite = sorted(candidates, key=lambda c: c.score.composite, reverse=True)
    must_have: set[str] = set()
    budget_for_rest = token_budget
    if sorted_by_composite and sorted_by_composite[0].score.composite >= 0.8:
        primary = sorted_by_composite[0]
        if primary.token_count <= token_budget:
            must_have.add(primary.method_id)
            budget_for_rest -= primary.token_count

    pool = [c for c in candidates if c.method_id not in must_have]

    if settings.ranker_strategy == "exact":
        selected_ids = _exact_knapsack(pool, budget_for_rest) | must_have
    else:
        selected_ids = _greedy_by_density(pool, budget_for_rest) | must_have

    selected = sorted(
        [c for c in candidates if c.method_id in selected_ids],
        key=lambda c: c.score.composite,
        reverse=True,
    )
    dropped = [c for c in candidates if c.method_id not in selected_ids]
    total_tokens = sum(c.token_count for c in selected)

    return RankResult(selected=selected, dropped=dropped, total_tokens=total_tokens)