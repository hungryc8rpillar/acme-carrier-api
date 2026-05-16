"""US-state expansion in /loads/search: full name <-> abbreviation must both hit
loads whose origin/destination is stored in the other form."""

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


def _ids(body) -> list[str]:
    return [r["load_id"] for r in body["results"]]


def test_full_state_name_matches_abbreviated_load(client):
    # LD-1001 destination is "Dallas, TX" — querying "Texas" must find it.
    r = client.get("/loads/search?destination=Texas&limit=10", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert "LD-1001" in _ids(body)


def test_abbreviation_matches_abbreviated_load(client):
    r = client.get("/loads/search?destination=TX&limit=10", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert "LD-1001" in _ids(body)


def test_city_plus_full_state_name_matches(client):
    # Stored origin is "Atlanta, GA"; carrier may say "Atlanta, Georgia".
    r = client.get(
        "/loads/search?origin=Atlanta, Georgia&limit=10",
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "LD-1001" in _ids(body)
