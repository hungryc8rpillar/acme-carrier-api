"""Idempotency: replaying the same call_id does not double-count."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("ENABLE_DOCS", "false")
    # Importing after env is set so the module reads our values.
    if "app.main" in os.sys.modules:
        del os.sys.modules["app.main"]
    if "app.db" in os.sys.modules:
        del os.sys.modules["app.db"]
    from app.main import app  # noqa: E402

    with TestClient(app) as c:
        yield c


SAMPLE = {
    "call_id": "hr_call_test_001",
    "started_at": "2026-05-13T14:22:01Z",
    "ended_at": "2026-05-13T14:25:48Z",
    "duration_seconds": 227,
    "mc_number": "MC123456",
    "carrier_legal_name": "Sunrise Trucking LLC",
    "carrier_eligible": True,
    "load_id": "LD-1001",
    "loadboard_rate": 2450.00,
    "final_price": 2575.00,
    "negotiation_rounds": 2,
    "outcome": "booked",
    "sentiment": "positive",
    "transfer_attempted": True,
    "transcript_url": "https://example.com/t/1",
    "recording_url": "https://example.com/r/1",
    "summary": "Booked at $2,575 in round 2.",
}


def _post(client, body):
    return client.post("/calls/events", json=body, headers={"X-API-Key": "test-key"})


def test_first_post_stores_and_returns_201(client):
    r = _post(client, SAMPLE)
    assert r.status_code == 201
    body = r.json()
    assert body["call_id"] == SAMPLE["call_id"]
    assert body["stored"] is True
    assert body["already_exists"] is False


def test_duplicate_post_returns_200_with_already_exists(client):
    r1 = _post(client, SAMPLE)
    assert r1.status_code == 201
    r2 = _post(client, SAMPLE)
    assert r2.status_code == 200
    body = r2.json()
    assert body["stored"] is False
    assert body["already_exists"] is True


def test_duplicate_does_not_double_count(client):
    _post(client, SAMPLE)
    _post(client, SAMPLE)
    r = client.get("/calls", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_get_call_returns_full_detail(client):
    _post(client, SAMPLE)
    r = client.get(f"/calls/{SAMPLE['call_id']}", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "booked"
    assert body["raw_payload"]["summary"] == SAMPLE["summary"]
    assert body["carrier_eligible"] is True


def test_get_unknown_call_returns_404(client):
    r = client.get("/calls/nope", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404


def test_missing_api_key_returns_401(client):
    r = client.post("/calls/events", json=SAMPLE)
    assert r.status_code == 401


def test_outcome_aliases_are_normalized(client):
    body = {**SAMPLE, "call_id": "alias-1", "outcome": "Booking_Confirmed", "sentiment": "Happy"}
    r = _post(client, body)
    assert r.status_code == 201
    detail = client.get("/calls/alias-1", headers={"X-API-Key": "test-key"}).json()
    assert detail["outcome"] == "booked"
    assert detail["sentiment"] == "positive"


def test_unknown_outcome_falls_back_to_other(client):
    body = {**SAMPLE, "call_id": "alias-2", "outcome": "wat_is_this", "sentiment": "what"}
    r = _post(client, body)
    assert r.status_code == 201
    detail = client.get("/calls/alias-2", headers={"X-API-Key": "test-key"}).json()
    assert detail["outcome"] == "other"
    assert detail["sentiment"] == "neutral"


def test_mid_call_post_without_started_at_defaults_to_now(client):
    """HappyRobot mid-call tool call has no started_at variable; we default it."""
    body = {
        "call_id": "hr_run_mid_1",
        "mc_number": "MC123456",
        "load_id": "LD-1001",
        "final_price": 2700,
        "outcome": "booked",
        "transfer_attempted": True,
    }
    r = _post(client, body)
    assert r.status_code == 201
    assert r.json()["stored"] is True

    detail = client.get("/calls/hr_run_mid_1", headers={"X-API-Key": "test-key"}).json()
    assert detail["final_price"] == 2700
    assert detail["outcome"] == "booked"
    assert detail["transfer_attempted"] is True
    # Defaulted server-side; not null.
    assert detail["started_at"] is not None
    assert "T" in detail["started_at"]


def test_mc_number_int_is_coerced_and_normalized(client):
    """HappyRobot's 'Preserve data types' may send mc_number as int — must accept."""
    body = {
        "call_id": "test-coercion-int",
        "mc_number": 123456,  # integer, not string
        "load_id": "LD-1001",
        "final_price": 2700,
        "outcome": "booked",
        "transfer_attempted": True,
    }
    r = _post(client, body)
    assert r.status_code == 201
    assert r.json()["stored"] is True

    detail = client.get("/calls/test-coercion-int", headers={"X-API-Key": "test-key"}).json()
    assert detail["mc_number"] == "123456"


def test_mc_number_string_with_mc_prefix_still_works(client):
    """Backward compat: 'MC123456' still accepted, normalized to '123456'."""
    body = {**SAMPLE, "call_id": "test-coercion-str", "mc_number": "MC123456"}
    r = _post(client, body)
    assert r.status_code == 201
    detail = client.get("/calls/test-coercion-str", headers={"X-API-Key": "test-key"}).json()
    assert detail["mc_number"] == "123456"


def test_final_carrier_offer_persists_on_enrichment(client):
    """Post-call enrichment writes final_carrier_offer (carrier's last counter,
    distinct from final_price the agreed price). Must not be dropped silently."""
    mid_call = {
        "call_id": "hr_final_offer",
        "mc_number": 123456,
        "load_id": "LD-1001",
        "final_price": 2700,
        "outcome": "booked",
        "transfer_attempted": True,
    }
    r1 = _post(client, mid_call)
    assert r1.status_code == 201

    enrichment = {
        "call_id": "hr_final_offer",
        "final_carrier_offer": 2800,  # last carrier counter before agreement at 2700
        "negotiation_rounds": 3,
    }
    r2 = _post(client, enrichment)
    assert r2.status_code == 200

    detail = client.get("/calls/hr_final_offer", headers={"X-API-Key": "test-key"}).json()
    assert detail["final_carrier_offer"] == 2800.0
    assert detail["final_price"] == 2700.0  # mid-call value preserved
    assert detail["negotiation_rounds"] == 3


def test_empty_strings_on_string_fields_become_none(client):
    """Regression: ineligible-carrier post-call POST was sending load_id="" and
    other string fields as "" (HappyRobot's Preserve-data-types behavior).
    Empty load_id was hitting the FK constraint loads(load_id) and returning 500.
    All optional string fields must coerce "" → None at the validator boundary."""
    body = {
        "call_id": "hr_all_empty_strings",
        "load_id": "",
        "mc_number": "",
        "carrier_legal_name": "",
        "outcome": "",
        "sentiment": "",
        "summary": "",
        "outcome_reasoning": "",
        "sentiment_reasoning": "",
        "transcript_url": "",
        "recording_url": "",
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text  # not 500 (FK) and not 422

    detail = client.get(
        "/calls/hr_all_empty_strings", headers={"X-API-Key": "test-key"}
    ).json()
    for field in (
        "load_id",
        "mc_number",
        "carrier_legal_name",
        "outcome",
        "sentiment",
        "summary",
        "outcome_reasoning",
        "sentiment_reasoning",
        "transcript_url",
        "recording_url",
    ):
        assert detail[field] is None, f"{field} should be None, got {detail[field]!r}"


def test_ended_at_derived_when_null_but_reconstructable(client):
    """When ended_at is missing, /calls/{id} synthesizes it from started_at + duration_seconds."""
    body = {
        "call_id": "hr_derive_ended",
        "load_id": "LD-1001",
        "started_at": "2026-05-13T14:22:01Z",
        "duration_seconds": 227,
        # ended_at omitted entirely
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/hr_derive_ended", headers={"X-API-Key": "test-key"}).json()
    # 14:22:01 + 227s = 14:25:48
    assert detail["ended_at"] is not None
    assert detail["ended_at"].startswith("2026-05-13T14:25:48")


def test_stored_ended_at_wins_over_derived(client):
    """If ended_at is explicitly stored, the read path does not overwrite it."""
    body = {
        "call_id": "hr_stored_ended",
        "load_id": "LD-1001",
        "started_at": "2026-05-13T14:22:01Z",
        "duration_seconds": 99999,  # would compute a different ended_at
        "ended_at": "2026-05-13T14:25:48Z",
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/hr_stored_ended", headers={"X-API-Key": "test-key"}).json()
    assert detail["ended_at"].startswith("2026-05-13T14:25:48")


def test_junk_datetime_coerces_to_none_and_derives(client):
    """Boundary-type defense: int 1 or '' on ended_at must not parse as Unix epoch."""
    body = {
        "call_id": "hr_junk_ended",
        "load_id": "LD-1001",
        "started_at": "2026-05-13T14:22:01Z",
        "duration_seconds": 227,
        "ended_at": 1,  # would otherwise parse to 1970-01-01T00:00:01
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/hr_junk_ended", headers={"X-API-Key": "test-key"}).json()
    # ended_at junk dropped → derived from started_at + duration_seconds
    assert detail["ended_at"].startswith("2026-05-13T14:25:48")


def test_started_at_junk_falls_back_to_default(client):
    """Junk on the non-nullable started_at field falls back to default (now), not 422."""
    body = {
        "call_id": "hr_junk_started",
        "load_id": "LD-1001",
        "started_at": "",  # empty string, would otherwise 422
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/hr_junk_started", headers={"X-API-Key": "test-key"}).json()
    # Server-side default fired; we can't assert an exact timestamp, only that it's populated.
    assert detail["started_at"] is not None
    assert "T" in detail["started_at"]


def test_empty_string_numerics_become_none(client):
    """Regression: HappyRobot's Preserve-data-types serializes unset numeric/
    datetime fields as '' instead of null. Pydantic previously 422'd on those,
    losing the enriched event on a real ineligible-carrier call."""
    body = {
        "call_id": "hr_empty_numerics",
        "load_id": "LD-1001",
        "outcome": "ineligible_carrier",
        "loadboard_rate": "",
        "final_price": "",
        "final_carrier_offer": "",
        "negotiation_rounds": "",
        "duration_seconds": "",
        "ended_at": "",
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/hr_empty_numerics", headers={"X-API-Key": "test-key"}).json()
    for field in (
        "loadboard_rate",
        "final_price",
        "final_carrier_offer",
        "negotiation_rounds",
        "duration_seconds",
        "ended_at",
    ):
        assert detail[field] is None, f"{field} should be None, got {detail[field]!r}"


def test_ineligibility_reasons_list_round_trip(client):
    """Canonical shape: a JSON array of reason codes from verify_carrier."""
    body = {
        "call_id": "inelig-list",
        "load_id": "LD-1001",
        "outcome": "ineligible_carrier",
        "ineligibility_reasons": ["operating_authority_inactive", "out_of_service"],
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/inelig-list", headers={"X-API-Key": "test-key"}).json()
    assert detail["ineligibility_reasons"] == [
        "operating_authority_inactive",
        "out_of_service",
    ]


def test_ineligibility_reasons_accepts_json_string(client):
    """Defensive: HappyRobot's Preserve-data-types may stringify arrays."""
    body = {
        "call_id": "inelig-json-str",
        "load_id": "LD-1001",
        "outcome": "ineligible_carrier",
        "ineligibility_reasons": '["operating_authority_inactive"]',  # stringified array
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text

    detail = client.get("/calls/inelig-json-str", headers={"X-API-Key": "test-key"}).json()
    # Stored and served as a real array, not the wire string.
    assert detail["ineligibility_reasons"] == ["operating_authority_inactive"]
    assert isinstance(detail["ineligibility_reasons"], list)


def test_reasoning_fields_persist_on_enrichment(client):
    """Classify-node rationale strings round-trip without normalization."""
    mid_call = {
        "call_id": "hr_reasoning",
        "mc_number": 123456,
        "load_id": "LD-1001",
        "final_price": 2700,
        "outcome": "booked",
    }
    r1 = _post(client, mid_call)
    assert r1.status_code == 201

    enrichment = {
        "call_id": "hr_reasoning",
        "outcome_reasoning": "Carrier accepted our counter at $2,700 on round 2; transfer succeeded.",
        "sentiment": "positive",
        "sentiment_reasoning": "Carrier tone was upbeat and cooperative throughout; no friction on rate.",
    }
    r2 = _post(client, enrichment)
    assert r2.status_code == 200

    detail = client.get("/calls/hr_reasoning", headers={"X-API-Key": "test-key"}).json()
    assert detail["outcome"] == "booked"  # not nulled out
    assert detail["sentiment"] == "positive"
    assert detail["outcome_reasoning"] == enrichment["outcome_reasoning"]
    assert detail["sentiment_reasoning"] == enrichment["sentiment_reasoning"]


def test_final_carrier_offer_accepts_string_numeric(client):
    """Same Postel's-law tolerance HappyRobot's body may carry. Pydantic v2 default
    coercion handles '2800' → 2800.0 for plain float fields (no custom validator)."""
    body = {
        "call_id": "hr_final_offer_str",
        "load_id": "LD-1001",
        "final_carrier_offer": "2800",
    }
    r = _post(client, body)
    assert r.status_code == 201, r.text
    detail = client.get("/calls/hr_final_offer_str", headers={"X-API-Key": "test-key"}).json()
    assert detail["final_carrier_offer"] == 2800.0


def test_load_id_int_coerces_at_model_layer():
    """Symmetric coercion for load_id: numeric input becomes string before storage.
    Tested at the model layer to avoid the loads FK constraint."""
    from app.models import CallEvent

    e = CallEvent(call_id="x", load_id=1001, mc_number=123456)
    assert e.load_id == "1001"
    assert e.mc_number == "123456"


def test_partial_then_enriched_upserts_same_row(client):
    """Mid-call partial write then post-call enrichment merge into one row."""
    mid_call = {
        "call_id": "hr_run_enrich",
        "mc_number": "MC123456",
        "load_id": "LD-1001",
        "final_price": 2700,
        "outcome": "booked",
        "transfer_attempted": True,
    }
    r1 = _post(client, mid_call)
    assert r1.status_code == 201

    enrichment = {
        "call_id": "hr_run_enrich",
        "started_at": "2026-05-13T14:22:01Z",
        "ended_at": "2026-05-13T14:25:48Z",
        "duration_seconds": 227,
        "transcript_url": "https://example.com/t/enrich",
        "recording_url": "https://example.com/r/enrich",
        "sentiment": "positive",
        "summary": "Booked at $2,700 with positive sentiment.",
        "negotiation_rounds": 2,
    }
    r2 = _post(client, enrichment)
    assert r2.status_code == 200
    assert r2.json() == {"call_id": "hr_run_enrich", "stored": False, "already_exists": True}

    # No duplicate row.
    assert client.get("/calls", headers={"X-API-Key": "test-key"}).json()["count"] == 1

    detail = client.get("/calls/hr_run_enrich", headers={"X-API-Key": "test-key"}).json()
    # mid-call fields preserved (not nulled by enrichment)
    assert detail["final_price"] == 2700
    assert detail["outcome"] == "booked"
    assert detail["mc_number"] == "123456"  # normalized on receipt (MC prefix stripped)
    assert detail["load_id"] == "LD-1001"
    assert detail["transfer_attempted"] is True
    # enrichment fields applied
    assert detail["started_at"].startswith("2026-05-13T14:22:01")
    assert detail["ended_at"].startswith("2026-05-13T14:25:48")
    assert detail["duration_seconds"] == 227
    assert detail["transcript_url"] == "https://example.com/t/enrich"
    assert detail["recording_url"] == "https://example.com/r/enrich"
    assert detail["sentiment"] == "positive"
    assert detail["summary"] == "Booked at $2,700 with positive sentiment."
    assert detail["negotiation_rounds"] == 2


def test_carrier_eligible_empty_string_coerces_to_none(client):
    """HappyRobot's Preserve-data-types serializes an unset bool as "" rather
    than null — must not 422. Same shape as the numeric / datetime fix."""
    payload = {"call_id": "test_bool_empty", "carrier_eligible": ""}
    r = _post(client, payload)
    assert r.status_code == 201, r.text
    row = client.get(f"/calls/{payload['call_id']}", headers={"X-API-Key": "test-key"}).json()
    assert row["carrier_eligible"] is None


def test_transfer_attempted_empty_string_coerces_to_none(client):
    payload = {"call_id": "test_transfer_empty", "transfer_attempted": ""}
    r = _post(client, payload)
    assert r.status_code == 201, r.text
    row = client.get(f"/calls/{payload['call_id']}", headers={"X-API-Key": "test-key"}).json()
    assert row["transfer_attempted"] is None
