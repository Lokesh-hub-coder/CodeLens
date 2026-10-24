"""
Run with: pytest backend/tests/test_ranker.py -v
"""
from pathlib import Path

import pytest

from app.acor.ranker import _exact_knapsack, _greedy_by_density, rank_and_select
from app.acor.context_builder import build_context
from app.acor.scorer import ScoredCandidate
from app.api.schemas import ScoreBreakdown
from app.config import Settings
from app.retrieval.tokenizer import count_tokens


def _scored(method_id: str, composite: float, token_count: int) -> ScoredCandidate:
    return ScoredCandidate(
        method_id=method_id,
        class_id="com.foo.Bar",
        name=method_id,
        kind="method",
        file_path="com/foo/Bar.java",
        start_line=1,
        end_line=5,
        snippet="// body",
        token_count=token_count,
        raw_semantic_score=0.5,
        score=ScoreBreakdown(semantic=0.5, call_graph=0.0, dependency=0.0, symbol=0.0, composite=composite),
    )


def test_greedy_prefers_higher_density_when_it_must_choose():
    # A: score 10, 100 tokens -> density 0.10
    # B: score  6,  20 tokens -> density 0.30
    # Budget only fits one of them.
    a = _scored("A", composite=10.0, token_count=100)
    b = _scored("B", composite=6.0, token_count=20)
    selected = _greedy_by_density([a, b], token_budget=50)
    assert selected == {"B"}


def test_greedy_keeps_highest_scoring_candidate_when_it_fits():
    # Density picks B, C, and D first, but A has the strongest relevance score.
    a = _scored("A", composite=10.0, token_count=40)
    b = _scored("B", composite=6.0, token_count=10)
    c = _scored("C", composite=5.0, token_count=10)
    d = _scored("D", composite=4.0, token_count=10)

    selected = _greedy_by_density([a, b, c, d], token_budget=50)

    assert "A" in selected
    assert sum(
        candidate.token_count
        for candidate in [a, b, c, d]
        if candidate.method_id in selected
    ) <= 50


def test_greedy_never_exceeds_budget():
    candidates = [_scored(f"C{i}", composite=float(i + 1), token_count=30) for i in range(10)]
    selected = _greedy_by_density(candidates, token_budget=95)
    total_tokens = sum(c.token_count for c in candidates if c.method_id in selected)
    assert total_tokens <= 95
    assert len(selected) == 3  # floor(95 / 30)


def test_greedy_handles_zero_token_candidate_without_division_error():
    zero = _scored("Z", composite=5.0, token_count=0)
    selected = _greedy_by_density([zero], token_budget=10)
    assert selected == {"Z"}


def test_exact_knapsack_never_scores_worse_than_greedy():
    """DP is optimal by construction, so on *any* instance its total value
    must be >= greedy's. This is the property that actually matters --
    checked here on the same instance test_exact_knapsack_beats_greedy_...
    below uses, plus a handful of random-ish instances, rather than
    hard-coding one specific expected subset that happens to coincide with
    what greedy also finds (which was a bug in an earlier version of this
    test: it asserted a specific answer that both algorithms agreed on,
    so it never actually exercised the case where they *should* differ).
    """
    instances = [
        [("heavy", 6.0, 50), ("light1", 5.0, 25), ("light2", 5.0, 25)],
        [("a", 3.0, 12), ("b", 4.0, 9), ("c", 2.5, 7), ("d", 5.0, 20)],
        [("solo", 6.0, 30), ("lite_a", 3.5, 16), ("lite_b", 3.5, 16)],
    ]
    for raw in instances:
        candidates = [_scored(mid, score, tokens) for mid, score, tokens in raw]
        budget = 30
        greedy_selected = _greedy_by_density(candidates, budget)
        exact_selected = _exact_knapsack(candidates, budget)

        greedy_value = sum(c.score.composite for c in candidates if c.method_id in greedy_selected)
        exact_value = sum(c.score.composite for c in candidates if c.method_id in exact_selected)
        exact_tokens = sum(c.token_count for c in candidates if c.method_id in exact_selected)

        assert exact_tokens <= budget
        assert exact_value >= greedy_value - 1e-9


def test_exact_knapsack_beats_greedy_on_a_constructed_adversarial_case():
    """A genuine (verified) gap when the top-scoring item cannot fit.

    Greedy takes one of the two higher-density light items, while exact
    selection finds the fitting solo item with the greater total value.
    """
    oversized = _scored("oversized", composite=20.0, token_count=31)
    solo = _scored("solo", composite=6.0, token_count=30)     # density 0.200
    lite_a = _scored("lite_a", composite=3.5, token_count=16)  # density 0.219
    lite_b = _scored("lite_b", composite=3.5, token_count=16)  # density 0.219
    candidates = [oversized, solo, lite_a, lite_b]
    budget = 30

    greedy_selected = _greedy_by_density(candidates, budget)
    exact_selected = _exact_knapsack(candidates, budget)

    greedy_value = sum(c.score.composite for c in candidates if c.method_id in greedy_selected)
    exact_value = sum(c.score.composite for c in candidates if c.method_id in exact_selected)

    assert greedy_selected == {"lite_a"}  # picks the denser item first, then lite_b (16) doesn't fit in the remaining 14
    assert exact_selected == {"solo"}
    assert exact_value > greedy_value
    assert exact_value == pytest.approx(6.0)
    assert greedy_value == pytest.approx(3.5)


def test_exact_knapsack_respects_the_budget():
    candidates = [_scored(f"C{i}", composite=float(i + 1), token_count=17) for i in range(5)]
    selected = _exact_knapsack(candidates, token_budget=40)
    total_tokens = sum(c.token_count for c in candidates if c.method_id in selected)
    assert total_tokens <= 40


def test_exact_knapsack_table_is_bounded_by_total_weight_not_just_budget():
    """When every candidate fits comfortably under the budget, the DP
    should take all of them (and not, e.g., choke on a huge budget value
    -- effective_budget is capped at the candidates' total weight)."""
    candidates = [_scored(f"C{i}", composite=float(i + 1), token_count=5) for i in range(4)]
    selected = _exact_knapsack(candidates, token_budget=10_000)
    assert selected == {c.method_id for c in candidates}


def test_rank_and_select_reports_selected_and_dropped_disjointly():
    settings = Settings(ranker_strategy="greedy")
    candidates = [_scored(f"C{i}", composite=float(10 - i), token_count=40) for i in range(5)]
    result = rank_and_select(candidates, token_budget=100, settings=settings)

    selected_ids = {c.method_id for c in result.selected}
    dropped_ids = {c.method_id for c in result.dropped}
    assert selected_ids.isdisjoint(dropped_ids)
    assert selected_ids | dropped_ids == {c.method_id for c in candidates}
    assert result.total_tokens <= 100
    # Highest-scoring candidates should be the ones selected under a tight budget.
    assert "C0" in selected_ids  # composite=10, the best


def test_rank_and_select_rejects_non_positive_budget():
    settings = Settings()
    with pytest.raises(ValueError):
        rank_and_select([_scored("A", 1.0, 10)], token_budget=0, settings=settings)


def test_rank_and_select_deduplicates_and_budgets_formatted_context():
    settings = Settings(ranker_strategy="exact")
    first = _scored("A", composite=0.9, token_count=2)
    first.snippet = "important method body"
    duplicate = _scored("A", composite=0.8, token_count=2)
    duplicate.snippet = "duplicate stale version"
    other = _scored("B", composite=0.7, token_count=2)
    other.file_path = "com/foo/Other.java"
    other.snippet = "another method body"

    candidates = [first, duplicate, other]
    result = rank_and_select(candidates, token_budget=45, settings=settings)
    formatted = build_context(result.selected, Path("."))

    assert len({item.method_id for item in result.selected}) == len(result.selected)
    assert [item.method_id for item in result.selected].count("A") == 1
    assert result.total_tokens == count_tokens(formatted)
    assert result.total_tokens <= 45
    assert result.selected[0].method_id == "A"