"""
ACOR step 2/3: pick *which* scored candidates actually make it into the
LLM's context, subject to a hard token budget.

This is exactly the 0/1 knapsack problem: each candidate has a "value"
(its composite relevance score) and a "weight" (its token_count), and we
want the subset maximizing total value with total weight <= budget. Two
strategies are provided because they sit at different points of the
optimality/speed tradeoff, which is itself worth an ablation in the
project's evaluation chapter ("greedy vs. optimal ACOR ranking: does exact
selection meaningfully beat the greedy heuristic in practice?"):

- greedy (default): sort candidates by score-per-token ("density") and take
  them in that order while they still fit. This is the standard
  approximation for knapsack problems, runs in O(n log n), and is what
  the ranker uses whenever the candidate set is large, since exact DP's
  O(n * budget) blows up once budget is in the thousands of tokens.

- exact_knapsack: textbook DP, optimal, but only offered for small
  candidate sets (see EXACT_KNAPSACK_MAX_CANDIDATES) where O(n * budget)
  is still cheap -- run directly on integer token counts, not a coarsened
  approximation of them (see the note on bucketing below).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.acor.scorer import ScoredCandidate
from app.config import Settings


@dataclass(frozen=True)
class RankedSelection:
    selected: list[ScoredCandidate]
    dropped: list[ScoredCandidate]
    total_tokens: int
    strategy_used: str


def rank_and_select(
    scored_candidates: list[ScoredCandidate], token_budget: int, settings: Settings
) -> RankedSelection:
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    strategy = settings.ranker_strategy
    if strategy == "exact_knapsack" and len(scored_candidates) <= settings.exact_knapsack_max_candidates:
        selected_ids = _exact_knapsack(scored_candidates, token_budget)
        used_strategy = "exact_knapsack"
    else:
        selected_ids = _greedy_by_density(scored_candidates, token_budget)
        used_strategy = "greedy" if strategy == "greedy" else "greedy_fallback"

    selected = [c for c in scored_candidates if c.method_id in selected_ids]
    dropped = [c for c in scored_candidates if c.method_id not in selected_ids]
    # Stable, human-legible ordering for display: highest relevance first.
    selected.sort(key=lambda c: c.score.composite, reverse=True)

    return RankedSelection(
        selected=selected,
        dropped=dropped,
        total_tokens=sum(c.token_count for c in selected),
        strategy_used=used_strategy,
    )


def _greedy_by_density(candidates: list[ScoredCandidate], token_budget: int) -> set[str]:
    """O(n log n) greedy: take candidates in decreasing score/token order
    while they still fit. This is the classic 2-approximation for the
    fractional knapsack relaxation and, empirically, is very close to
    optimal whenever individual item sizes are small relative to the
    budget -- true here, since a single method is rarely more than a few
    hundred tokens against budgets in the thousands. It is *not*
    guaranteed optimal, though -- see test_ranker.py for a constructed
    case where it leaves value on the table that exact_knapsack recovers.
    """
    def density(c: ScoredCandidate) -> float:
        # Guard against zero-token entries (shouldn't happen with real
        # source, but a candidate spanning a truly empty range would
        # otherwise divide by zero).
        return c.score.composite / max(c.token_count, 1)

    ordered = sorted(candidates, key=density, reverse=True)
    selected: set[str] = set()
    remaining = token_budget
    for candidate in ordered:
        if candidate.token_count <= remaining:
            selected.add(candidate.method_id)
            remaining -= candidate.token_count
    return selected


def _exact_knapsack(candidates: list[ScoredCandidate], token_budget: int) -> set[str]:
    """Standard 0/1 knapsack DP over exact integer token counts. Returns
    the method_ids of the value-maximizing subset that fits the budget.

    An earlier version of this function coarsened each candidate's
    token_count into ~10-token "buckets" before running the DP, purely to
    shrink the table. That was a real bug, not just an approximation:
    ceiling-rounding each item's weight *independently* and then summing
    the rounded weights overstates the combined cost of a group of items
    (e.g. two 25-token items each round up to 30-token-equivalent
    buckets, making a pair that truly fits in a 50-token budget look like
    it needs 60) -- so the "exact" solver could reject genuinely feasible,
    higher-value combinations and return something *worse* than the
    greedy heuristic it's supposed to be an upgrade over. Confirmed via a
    standalone reproduction before this fix landed.

    The fix is to just not bucket: run the DP on real token counts,
    bounded by whichever is smaller, the requested budget or the total
    weight actually on the table (if every candidate fits, the DP table
    only needs to be as wide as their sum, not the full budget).
    """
    effective_budget = min(token_budget, sum(c.token_count for c in candidates))
    weights = [c.token_count for c in candidates]
    # Scale scores to integers for a stable DP comparison (float addition
    # order can otherwise make two subsets compare inconsistently at the
    # margins); 1000x keeps three decimal digits of the composite score.
    values = [round(c.score.composite * 1000) for c in candidates]
    n = len(candidates)

    # dp[i][b] = best achievable value using the first i candidates and a
    # token budget of b.
    dp = [[0] * (effective_budget + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        weight, value = weights[i - 1], values[i - 1]
        for b in range(effective_budget + 1):
            dp[i][b] = dp[i - 1][b]
            if weight <= b:
                dp[i][b] = max(dp[i][b], dp[i - 1][b - weight] + value)

    # Backtrack to recover which items were taken.
    selected: set[str] = set()
    b = effective_budget
    for i in range(n, 0, -1):
        if dp[i][b] != dp[i - 1][b]:
            selected.add(candidates[i - 1].method_id)
            b -= weights[i - 1]
    return selected