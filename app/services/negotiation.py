"""Deterministic price negotiation. Decision-table is in the plan file."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


def ceiling_for(load_id: str, base: float = 1.12, band: float = 0.05) -> float:
    """Deterministic per-load ceiling multiplier in [base - band, base + band).

    Same load_id always returns the same value (auditable, replay-safe); different
    load_ids get different ceilings so a repeat carrier can't pattern-match the
    constant 1.12 and bid just under it every time.
    """
    h = int(hashlib.sha256(load_id.encode()).hexdigest(), 16)
    delta = (h % int(band * 200)) / 100.0 - band
    return base + delta


@dataclass
class NegotiationDecision:
    decision: str  # "accept" | "counter" | "reject"
    round: int
    max_rounds: int
    rounds_remaining: int
    reason: str
    agent_response_hint: str
    agreed_price: float | None = None
    our_counter: float | None = None


def _money(x: float) -> float:
    return float(round(x))


def _fmt(x: float) -> str:
    return f"${x:,.0f}"


def evaluate(
    *,
    load_id: str,
    loadboard_rate: float,
    carrier_offer: float,
    round_num: int,
    our_last_offer: float | None = None,
) -> NegotiationDecision:
    floor = loadboard_rate
    ceiling = loadboard_rate * ceiling_for(load_id)
    rounds_remaining = max(0, MAX_ROUNDS - round_num)

    # Defensive: agent shouldn't send round > MAX_ROUNDS, but don't trust upstream.
    if round_num > MAX_ROUNDS:
        return NegotiationDecision(
            decision="reject",
            round=round_num,
            max_rounds=MAX_ROUNDS,
            rounds_remaining=0,
            reason="max_rounds_exceeded",
            agent_response_hint=(
                "I appreciate the back and forth, but I've hit the limit of what I can do on this load."
            ),
        )

    # Parity with posted rate → clean deal.
    if carrier_offer == loadboard_rate:
        agreed = _money(loadboard_rate)
        return NegotiationDecision(
            decision="accept",
            round=round_num,
            max_rounds=MAX_ROUNDS,
            rounds_remaining=rounds_remaining,
            reason="parity_with_loadboard",
            agreed_price=agreed,
            agent_response_hint=(
                f"That works on our side. We're locked in at {_fmt(agreed)}."
            ),
        )

    # Carrier offering below the floor (loadboard rate) → never negotiate down.
    if carrier_offer < floor:
        counter = _money(floor)
        return NegotiationDecision(
            decision="counter",
            round=round_num,
            max_rounds=MAX_ROUNDS,
            rounds_remaining=rounds_remaining,
            reason="below_floor",
            our_counter=counter,
            agent_response_hint=(
                f"I can't go below the posted rate on this one — {_fmt(counter)} is where we'd need to be."
            ),
        )

    # Reference for prior offer; default to loadboard_rate.
    if round_num >= 2 and our_last_offer is None:
        logger.warning("our_last_offer missing on round %s; defaulting to loadboard_rate", round_num)
    prior = our_last_offer if our_last_offer is not None else loadboard_rate

    # Within margin band (loadboard < offer <= ceiling).
    if floor < carrier_offer <= ceiling:
        if round_num == 1:
            target = carrier_offer
            counter = _money((prior + target) / 2)
            return NegotiationDecision(
                decision="counter",
                round=round_num,
                max_rounds=MAX_ROUNDS,
                rounds_remaining=rounds_remaining,
                reason="round_1_no_instant_accept",
                our_counter=counter,
                agent_response_hint=(
                    f"I hear you on {_fmt(carrier_offer)}. Best I can do right now is {_fmt(counter)} — "
                    f"that's already above the posted rate. Can we make that work?"
                ),
            )
        # round 2 or 3
        agreed = _money(carrier_offer)
        return NegotiationDecision(
            decision="accept",
            round=round_num,
            max_rounds=MAX_ROUNDS,
            rounds_remaining=rounds_remaining,
            reason="within_margin_band",
            agreed_price=agreed,
            agent_response_hint=(
                f"Alright, we can make {_fmt(agreed)} work. We're locked in at {_fmt(agreed)}."
            ),
        )

    # carrier_offer > ceiling
    if round_num == MAX_ROUNDS:
        return NegotiationDecision(
            decision="reject",
            round=round_num,
            max_rounds=MAX_ROUNDS,
            rounds_remaining=0,
            reason="above_ceiling_max_rounds",
            agent_response_hint=(
                "I appreciate the back and forth, but I can't go higher than my last offer on this one. "
                "Want me to keep you in mind if a better-paying load comes up on this lane?"
            ),
        )

    # Round 1 or 2, above ceiling → concede half the gap toward ceiling, cap at ceiling.
    counter = _money(min(prior + 0.5 * (ceiling - prior), ceiling))
    return NegotiationDecision(
        decision="counter",
        round=round_num,
        max_rounds=MAX_ROUNDS,
        rounds_remaining=rounds_remaining,
        reason="above_ceiling_concede_half",
        our_counter=counter,
        agent_response_hint=(
            f"{_fmt(carrier_offer)} is more than we can do on this lane. I can stretch to {_fmt(counter)} — "
            f"that's already {_fmt(counter - loadboard_rate)} above the posted rate. How does that land?"
        ),
    )
