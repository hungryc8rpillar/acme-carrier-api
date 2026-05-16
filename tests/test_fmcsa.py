"""Mocked FMCSA tests: eligible, multiple ineligibility paths, not-found, cache hit."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.services import fmcsa


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_file = tmp_path / "test.db"
    c = sqlite3.connect(db_file, isolation_level=None)
    c.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parent.parent / "app" / "schema.sql"
    c.executescript(schema.read_text())
    yield c
    c.close()


def _payload(**overrides) -> dict:
    base = {
        "allowedToOperate": "Y",
        "dotNumber": 3431040,
        "legalName": "Sunrise Trucking LLC",
        "dbaName": "Sunrise Express",
        "oosDate": None,
        "statusCode": "A",
        "totalDrivers": 14,
        "totalPowerUnits": 12,
        "phyState": "GA",
    }
    base.update(overrides)
    return {"content": [{"carrier": base}]}


class _MockTransport(httpx.MockTransport):
    pass


def _client_returning(payload, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)
    return httpx.Client(transport=_MockTransport(handler))


def test_eligible_carrier(conn):
    client = _client_returning(_payload())
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert result.eligible is True
    assert result.legal_name == "Sunrise Trucking LLC"
    assert result.ineligibility_reasons is None
    assert "active" in result.status_summary.lower()
    assert result.details["operating_authority_status"] == "ACTIVE"


def test_inactive_authority(conn):
    client = _client_returning(_payload(allowedToOperate="N", statusCode="I"))
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert result.eligible is False
    assert "operating_authority_inactive" in result.ineligibility_reasons
    assert "inactive" in result.status_summary.lower()


def test_out_of_service(conn):
    client = _client_returning(_payload(oosDate="2026-04-15"))
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert result.eligible is False
    assert "out_of_service" in result.ineligibility_reasons


def test_no_insurance_proxy(conn):
    # allowedToOperate=N also flags as inactive in our model, but the test
    # specifically checks the no_insurance_on_file reason is present.
    client = _client_returning(_payload(allowedToOperate="N", statusCode="I"))
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert "no_insurance_on_file" in result.ineligibility_reasons


def test_not_found_when_content_empty(conn):
    client = _client_returning({"content": []})
    result = fmcsa.verify_carrier("MC999999", conn, api_key="test", client=client)
    assert result.not_found is True
    assert result.eligible is False
    assert "wasn't able to find" in result.status_summary


def test_cache_prevents_second_fetch(conn):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert calls["n"] == 1  # second call served from cache


def test_cache_expires_after_ttl(conn, monkeypatch):
    # Insert a stale cache row manually.
    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    conn.execute(
        "INSERT INTO fmcsa_cache (mc_number, payload, fetched_at) VALUES (?, ?, ?)",
        ("123456", json.dumps(_payload()), stale),
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_payload(legalName="Refreshed Co"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert calls["n"] == 1
    assert result.legal_name == "Refreshed Co"


def test_demo_override_forces_ineligible(conn, monkeypatch):
    monkeypatch.setenv("DEMO_INELIGIBLE_MCS", "400003,500004")
    client = _client_returning(_payload())  # FMCSA says eligible
    result = fmcsa.verify_carrier("MC400003", conn, api_key="test", client=client)
    assert result.eligible is False
    assert result.ineligibility_reasons == ["operating_authority_inactive"]
    assert "inactive" in result.status_summary.lower()
    assert result.details["operating_authority_status"] == "INACTIVE"
    # Real-carrier fields preserved — agent still says the legal name correctly.
    assert result.legal_name == "Sunrise Trucking LLC"


def test_demo_override_does_not_affect_unlisted_mcs(conn, monkeypatch):
    monkeypatch.setenv("DEMO_INELIGIBLE_MCS", "400003")
    client = _client_returning(_payload())
    result = fmcsa.verify_carrier("MC123456", conn, api_key="test", client=client)
    assert result.eligible is True


def test_mc_normalization():
    assert fmcsa.normalize_mc("MC123456") == "123456"
    assert fmcsa.normalize_mc("mc 123456") == "123456"
    assert fmcsa.normalize_mc("00123456") == "123456"
    assert fmcsa.normalize_mc("MC-123456") == "123456"
