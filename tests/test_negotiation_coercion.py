"""Postel's-law coercion on /negotiations/evaluate.

HappyRobot's Preserve-data-types toggle can send numerics as strings (and unset
fields as ""). The model must accept both and produce the same decisions as
typed input.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("ENABLE_DOCS", "false")
    for mod in ("app.main", "app.db"):
        if mod in os.sys.modules:
            del os.sys.modules[mod]
    from app.main import app

    with TestClient(app) as c:
        yield c


def _post(client, body):
    return client.post("/negotiations/evaluate", json=body, headers={"X-API-Key": "test-key"})


def test_our_last_offer_empty_string_becomes_none(client):
    """Empty string from HappyRobot for an unset numeric must not 422."""
    body = {"load_id": "LD-1001", "carrier_offer": 2800, "round": 1, "our_last_offer": ""}
    r = _post(client, body)
    assert r.status_code == 200, r.text
    payload = r.json()
    # LD-1001 loadboard=2450, ceiling=2744; 2800 > ceiling on round 1 → counter
    assert payload["decision"] == "counter"
    assert payload["reason"] == "above_ceiling_concede_half"


def test_carrier_offer_as_string_coerces(client):
    """carrier_offer arriving as '2800' must yield the same decision as 2800."""
    body_str = {"load_id": "LD-1001", "carrier_offer": "2800", "round": "1", "our_last_offer": ""}
    body_num = {"load_id": "LD-1001", "carrier_offer": 2800, "round": 1}
    r_str = _post(client, body_str)
    r_num = _post(client, body_num)
    assert r_str.status_code == 200, r_str.text
    assert r_num.status_code == 200, r_num.text
    # Same load, same offer, same round → identical decision/counter.
    a, b = r_str.json(), r_num.json()
    assert a["decision"] == b["decision"]
    assert a["reason"] == b["reason"]
    assert a["our_counter"] == b["our_counter"]


def test_round_as_string_coerces(client):
    """round arriving as '2' must be treated as round 2."""
    # LD-1001 loadboard=2450, ceiling=2744. carrier_offer=2500 sits within band;
    # on round 2 within-band → accept at carrier_offer.
    body = {"load_id": "LD-1001", "carrier_offer": 2500, "round": "2"}
    r = _post(client, body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["round"] == 2
    assert payload["decision"] == "accept"
    assert payload["reason"] == "within_margin_band"
    assert payload["agreed_price"] == 2500


def test_exact_failing_body_from_agent_now_succeeds(client):
    """Regression: the exact body that produced 422 in agent testing."""
    body = {"carrier_offer": "2800", "load_id": "LD-1001", "our_last_offer": "", "round": "1"}
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "counter"
