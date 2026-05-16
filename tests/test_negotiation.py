"""Decision-table coverage for app.services.negotiation.evaluate."""

import random

from app.services.negotiation import MAX_ROUNDS, ceiling_for, evaluate

LOAD = 2000.0
# TEST-5 is a synthetic load_id whose deterministic ceiling multiplier happens to
# equal the 1.12 base exactly, so the existing decision-table math stays clean.
LOAD_ID = "TEST-5"
assert ceiling_for(LOAD_ID) == 1.12
CEIL = LOAD * ceiling_for(LOAD_ID)  # 2240


def test_parity_accepts_at_any_round():
    for r in (1, 2, 3):
        d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=LOAD, round_num=r)
        assert d.decision == "accept"
        assert d.reason == "parity_with_loadboard"
        assert d.agreed_price == 2000


def test_below_floor_counters_at_loadboard():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=1800, round_num=1)
    assert d.decision == "counter"
    assert d.reason == "below_floor"
    assert d.our_counter == 2000


def test_within_band_round_1_counters_midpoint():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=1)
    assert d.decision == "counter"
    assert d.reason == "round_1_no_instant_accept"
    # midpoint(loadboard=2000, offer=2200) = 2100
    assert d.our_counter == 2100


def test_within_band_round_2_accepts():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=2, our_last_offer=2100)
    assert d.decision == "accept"
    assert d.reason == "within_margin_band"
    assert d.agreed_price == 2200


def test_within_band_round_3_accepts():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2150, round_num=3, our_last_offer=2125)
    assert d.decision == "accept"
    assert d.reason == "within_margin_band"
    assert d.agreed_price == 2150


def test_at_ceiling_round_2_accepts():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=CEIL, round_num=2, our_last_offer=2120)
    assert d.decision == "accept"
    assert d.agreed_price == round(CEIL)


def test_above_ceiling_round_1_counters_capped_at_ceiling():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=1)
    assert d.decision == "counter"
    assert d.reason == "above_ceiling_concede_half"
    # prior=2000, ceiling=2240, prior + 0.5*(2240-2000) = 2120
    assert d.our_counter == 2120


def test_above_ceiling_round_2_uses_prior_offer():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=2, our_last_offer=2120)
    assert d.decision == "counter"
    # prior=2120, ceiling=2240, midpoint = 2180
    assert d.our_counter == 2180


def test_above_ceiling_round_3_rejects():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=3, our_last_offer=2180)
    assert d.decision == "reject"
    assert d.reason == "above_ceiling_max_rounds"
    assert d.rounds_remaining == 0


def test_round_exceeds_max_rejects_defensively():
    d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2100, round_num=4, our_last_offer=2000)
    assert d.decision == "reject"
    assert d.reason == "max_rounds_exceeded"


def test_missing_our_last_offer_on_round_2_defaults_to_loadboard(caplog):
    with caplog.at_level("WARNING"):
        d = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=2, our_last_offer=None)
    # prior defaults to 2000; counter = 2120
    assert d.our_counter == 2120
    assert any("our_last_offer missing" in rec.message for rec in caplog.records)


def test_non_linear_three_round_sequence():
    # Round 1: carrier opens at 2700 (above ceiling=2240) → counter at 2120
    d1 = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2700, round_num=1)
    assert d1.decision == "counter" and d1.our_counter == 2120

    # Round 2: carrier drops to 2300 (still above ceiling) → counter at 2180
    d2 = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2300, round_num=2, our_last_offer=d1.our_counter)
    assert d2.decision == "counter" and d2.our_counter == 2180

    # Round 3: carrier drops to 2200 (within band) → accept
    d3 = evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=3, our_last_offer=d2.our_counter)
    assert d3.decision == "accept" and d3.agreed_price == 2200


def test_max_rounds_constant_matches_spec():
    assert MAX_ROUNDS == 3


def test_accept_hints_confirm_deal_without_announcing_transfer():
    """transfer_to_rep ships its own announcement; the negotiation hint must not duplicate it."""
    accept_decisions = [
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=LOAD, round_num=1),                          # parity
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=2, our_last_offer=2100),     # within_margin_band r2
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2150, round_num=3, our_last_offer=2125),     # within_margin_band r3
    ]
    for d in accept_decisions:
        assert d.decision == "accept"
        hint = d.agent_response_hint.lower()
        assert "transfer" not in hint, f"{d.reason} hint mentions transfer: {d.agent_response_hint!r}"
        # Still confirms the deal terms so the agent has something to say.
        assert "locked in" in hint


def test_no_decision_hint_mentions_transfer():
    """Sweep all decision paths — transfer announcement belongs to the tool, not the hint."""
    cases = [
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=LOAD, round_num=1),                          # accept / parity
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=2, our_last_offer=2100),     # accept / within_band
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=1800, round_num=1),                          # counter / below_floor
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2200, round_num=1),                          # counter / round_1_no_instant_accept
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=1),                          # counter / above_ceiling_concede_half
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2500, round_num=3, our_last_offer=2180),     # reject / above_ceiling_max_rounds
        evaluate(load_id=LOAD_ID, loadboard_rate=LOAD, carrier_offer=2100, round_num=4, our_last_offer=2000),     # reject / max_rounds_exceeded
    ]
    for d in cases:
        assert "transfer" not in d.agent_response_hint.lower(), (
            f"{d.reason} hint mentions transfer: {d.agent_response_hint!r}"
        )


def test_ceiling_for_is_deterministic():
    results = [ceiling_for("LD-1001") for _ in range(10)]
    assert len(set(results)) == 1


def test_ceiling_for_varies_across_loads():
    results = {ceiling_for(f"LD-{1000 + i}") for i in range(1, 21)}
    assert len(results) >= 5


def test_ceiling_for_in_range():
    rng = random.Random(0)
    base, band = 1.12, 0.05
    for _ in range(100):
        lid = f"LD-{rng.randint(0, 10**9)}"
        c = ceiling_for(lid, base=base, band=band)
        assert base - band <= c <= base + band, (lid, c)


def test_ceiling_for_default_band_mean_is_close_to_base():
    rng = random.Random(1)
    values = [ceiling_for(f"LD-{rng.randint(0, 10**9)}") for _ in range(1000)]
    mean = sum(values) / len(values)
    assert abs(mean - 1.12) < 0.01, mean
