"""Per-model pricing in USD per 1M tokens.

Source: published OpenAI pricing snapshots. Update as prices change.
"""
from __future__ import annotations

PRICING_PER_MODEL: dict[str, dict[str, float]] = {
    "gpt-5-mini": {
        "input": 0.25, "output": 2.00, "cached_input": 0.025,
    },
    "gpt-5.2-chat-latest": {
        "input": 1.75, "output": 10.00, "cached_input": 0.175,
    },
    "gpt-4.1": {
        "input": 2.00, "output": 8.00, "cached_input": 0.20,
    },
    "_default": {
        "input": 0.25, "output": 2.00, "cached_input": 0.025,
    },
}


def cost_for_node(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
) -> float:
    """Compute USD cost for a single LLM node call."""
    if not model or not input_tokens:
        return 0.0
    prices = PRICING_PER_MODEL.get(model, PRICING_PER_MODEL["_default"])
    cached = cached_input_tokens or 0
    uncached = max(0, input_tokens - cached)
    output = output_tokens or 0
    return (
        uncached * prices["input"] / 1_000_000
        + cached * prices["cached_input"] / 1_000_000
        + output * prices["output"] / 1_000_000
    )


# Voice agent cost — flat constant estimate. HappyRobot doesn't expose voice
# token usage as workflow variables yet (decision log 10.1); both workaround
# paths (backend integration + workflow-internal GET) time out as v1.1 work.
VOICE_AGENT_COST_PER_CALL_USD: float = 0.085


def voice_agent_cost_for_call() -> float:
    return VOICE_AGENT_COST_PER_CALL_USD
