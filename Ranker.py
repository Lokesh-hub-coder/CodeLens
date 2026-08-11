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
  is still cheap. Token costs are bucketed to a coarser granularity first
  (see _bucket_tokens) purely to keep the DP table a sane size -- exact
  per-token granularity buys nothing here since the greedy pass already
  handles the fine-grained case well.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.acor.scorer import ScoredCandidate
from app.config import Settings

# DP table cells beyond this are pointless: no single method chunk is
# realistically going to be a fraction of a token wide, so bucketing the
# budget into ~10-token slices barely changes which items fit while cutting
# the DP table size (and thus runtime) by roughly 10x.
_KNAPSACK_BUCKET_SIZE = 10


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
    hundred tokens against budgets in the thousands.
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
    """Standard 0/1 knapsack DP over bucketed token costs. Returns the
    method_ids of the value-maximizing subset that fits the budget exactly.
    """
    bucket_budget = _bucket_tokens(token_budget)
    weights = [_bucket_tokens(c.token_count) for c in candidates]
    # Scale scores to integers for a stable DP comparison (float addition
    # order can otherwise make two subsets compare inconsistently at the
    # margins); 1000x keeps three decimal digits of the composite score.
    values = [round(c.score.composite * 1000) for c in candidates]
    n = len(candidates)

    # dp[i][b] = best achievable value using the first i candidates and a
    # bucketed budget of b.
    dp = [[0] * (bucket_budget + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        weight, value = weights[i - 1], values[i - 1]
        for b in range(bucket_budget + 1):
            dp[i][b] = dp[i - 1][b]
            if weight <= b:
                dp[i][b] = max(dp[i][b], dp[i - 1][b - weight] + value)

    # Backtrack to recover which items were taken.
    selected: set[str] = set()
    b = bucket_budget
    for i in range(n, 0, -1):
        if dp[i][b] != dp[i - 1][b]:
            selected.add(candidates[i - 1].method_id)
            b -= weights[i - 1]
    return selected


def _bucket_tokens(token_count: int) -> int:
    return max(1, -(-token_count // _KNAPSACK_BUCKET_SIZE))  # ceiling division