import pytest

from app.services.pricing import (
    PRICING_PER_MODEL,
    VOICE_AGENT_COST_PER_CALL_USD,
    cost_for_node,
    voice_agent_cost_for_call,
)


def test_cost_for_node_known_model_basic():
    # gpt-5-mini: $0.25/M input, $2.00/M output
    cost = cost_for_node("gpt-5-mini", 1000, 100, 0)
    assert cost == pytest.approx(0.00045, rel=1e-6)


def test_cost_for_node_unknown_model_uses_default():
    cost_unknown = cost_for_node("totally-made-up-model", 1000, 100, 0)
    cost_default = cost_for_node("_default", 1000, 100, 0)
    assert cost_unknown == cost_default


def test_cost_for_node_handles_nulls():
    assert cost_for_node(None, 1000, 100, 0) == 0.0
    assert cost_for_node("gpt-5-mini", None, 100, 0) == 0.0
    assert cost_for_node("gpt-5-mini", 0, 100, 0) == 0.0


def test_cost_for_node_cached_tokens_priced_differently():
    cost = cost_for_node("gpt-5-mini", 1000, 0, 500)
    assert cost == pytest.approx(0.0001375, rel=1e-6)


def test_cost_for_node_handles_cached_exceeds_total():
    cost = cost_for_node("gpt-5-mini", 100, 0, 200)
    expected = 200 * 0.025 / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-6)


def test_voice_agent_cost_is_positive_constant():
    assert VOICE_AGENT_COST_PER_CALL_USD > 0
    assert voice_agent_cost_for_call() == VOICE_AGENT_COST_PER_CALL_USD


def test_pricing_table_has_required_models():
    assert "gpt-5-mini" in PRICING_PER_MODEL
    assert "gpt-5.2-chat-latest" in PRICING_PER_MODEL
    assert "_default" in PRICING_PER_MODEL
    for prices in PRICING_PER_MODEL.values():
        assert "input" in prices
        assert "output" in prices
        assert "cached_input" in prices
