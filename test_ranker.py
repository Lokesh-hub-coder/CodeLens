"""
Run with: pytest backend/tests/test_ranker.py -v
"""
import pytest

from app.acor.ranker import _bucket_tokens, _exact_knapsack, _greedy_by_density, rank_and_select
from app.acor.scorer import ScoredCandidate
from app.api.schemas import ScoreBreakdown
from app.config import Settings


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


def test_exact_knapsack_beats_or_matches_greedy_on_an_adversarial_case():
    """Classic knapsack counterexample for greedy-by-density: one heavy,
    slightly-better-density item excludes two lighter items that together
    are worth more and fit exactly."""
    heavy = _scored("heavy", composite=6.0, token_count=50)   # density 0.12
    light1 = _scored("light1", composite=5.0, token_count=25)  # density 0.20
    light2 = _scored("light2", composite=5.0, token_count=25)  # density 0.20
    candidates = [heavy, light1, light2]
    budget = 50

    greedy_selected = _greedy_by_density(candidates, budget)
    exact_selected = _exact_knapsack(candidates, budget)

    greedy_value = sum(c.score.composite for c in candidates if c.method_id in greedy_selected)
    exact_value = sum(c.score.composite for c in candidates if c.method_id in exact_selected)

    assert exact_selected == {"light1", "light2"}
    assert exact_value == pytest.approx(10.0)
    assert exact_value >= greedy_value  # DP is optimal by construction


def test_exact_knapsack_respects_bucketed_budget():
    candidates = [_scored(f"C{i}", composite=float(i + 1), token_count=17) for i in range(5)]
    selected = _exact_knapsack(candidates, token_budget=40)
    total_tokens = sum(c.token_count for c in candidates if c.method_id in selected)
    assert total_tokens <= 40


def test_bucket_tokens_is_ceiling_division():
    assert _bucket_tokens(1) == 1
    assert _bucket_tokens(10) == 1
    assert _bucket_tokens(11) == 2
    assert _bucket_tokens(0) == 1  # clamped to at least 1 bucket


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