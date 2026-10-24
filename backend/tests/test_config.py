import pytest
from pydantic import ValidationError

from app.config import Settings


def test_configured_acor_weights_are_normalized():
    settings = Settings(
        acor_weight_semantic=2,
        acor_weight_call_graph=1,
        acor_weight_dependency=1,
        acor_weight_symbol=0,
    )

    assert settings.acor_weights.semantic == pytest.approx(0.5)
    assert settings.acor_weights.call_graph == pytest.approx(0.25)
    assert settings.acor_weights.dependency == pytest.approx(0.25)
    assert settings.acor_weights.symbol == 0


@pytest.mark.parametrize(
    "values",
    [
        {"acor_weight_semantic": -1},
        {"acor_weight_symbol": float("nan")},
        {"acor_weight_semantic": 0, "acor_weight_call_graph": 0,
         "acor_weight_dependency": 0, "acor_weight_symbol": 0},
        {"ranker_strategy": "unknown"},
        {"default_token_budget": 0},
        {"parser_max_heap_mb": 0},
        {"parser_active_processor_count": 0},
        {"embedding_backend": "sentence_transformers"},
    ],
)
def test_invalid_ranking_configuration_is_rejected(values):
    with pytest.raises((ValueError, ValidationError)):
        Settings(**values)
