"""Seed synthetic call_events for the dashboard demo.

Wipes prior ``seed-*`` rows and re-inserts them on every run so schema or
date-distribution changes in this script always take effect. Organic call
rows (non-``seed-*`` call_ids) are untouched. Designed for the deployed Fly
volume DB (/data/app.db) but works locally too — point ``DB_PATH`` at any
sqlite file.

Usage (deployed):
    fly ssh console -a acme-carrier-api -C "python /app/scripts/seed_demo_calls.py"

Usage (local):
    DB_PATH=./data/app.db python scripts/seed_demo_calls.py

The mix is tuned to exercise both `/attention` rules:
  * 2x declined_price on the same load within 7d  -> floor_too_high
  * 1x booked with frustrated sentiment           -> sentiment_negative_booking
and to spread received_at across multiple days so `/metrics/timeseries`
shows movement rather than a single bucket.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
from datetime import UTC, datetime, timedelta


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _usage_for(call_id: str) -> dict:
    """Realistic per-node token usage with ±12% jitter keyed off call_id.

    Base values come from observed real test-call usage (gpt-5-mini, May 2026).
    Seeding the RNG with call_id keeps re-seeds deterministic for the same row.
    """
    rng = random.Random(call_id)

    def jitter(base: int) -> int:
        return max(0, int(base * (1 + rng.uniform(-0.12, 0.12))))

    return {
        "extract_model": "gpt-5-mini",
        "extract_input_tokens": jitter(1383),
        "extract_output_tokens": jitter(114),
        "extract_reasoning_tokens": 0,
        "extract_cached_input_tokens": 0,
        "outcome_model": "gpt-5-mini",
        "outcome_input_tokens": jitter(903),
        "outcome_output_tokens": jitter(430),
        "outcome_reasoning_tokens": jitter(384),
        "outcome_cached_input_tokens": 0,
        "sentiment_model": "gpt-5-mini",
        "sentiment_input_tokens": jitter(628),
        "sentiment_output_tokens": jitter(431),
        "sentiment_reasoning_tokens": jitter(384),
        "sentiment_cached_input_tokens": 0,
    }


def _rows() -> list[dict]:
    now = datetime.now(UTC)

    def ago(days: float, hour: int = 14) -> datetime:
        d = now - timedelta(days=days)
        return d.replace(hour=hour, minute=0, second=0, microsecond=0)

    # Date layout: 7 today / 2 yesterday / 2 day-before-yesterday.
    # Both LD-1005 declines stay inside the 7-day attention window, and the
    # booked+frustrated row stays on today so Sentiment Mix shows a frustrated.
    base = [
        # --- today (7 rows) ---
        {
            "call_id": "seed-booked-001",
            "received_at": ago(0, 9),
            "started_at": ago(0, 9),
            "duration_seconds": 220,
            "mc_number": "500001",
            "carrier_legal_name": "SUNRISE FREIGHT LLC",
            "carrier_eligible": 1,
            "load_id": "LD-1001",
            "loadboard_rate": 2450.0,
            "final_price": 2575.0,
            "final_carrier_offer": 2700.0,
            "negotiation_rounds": 2,
            "outcome": "booked",
            "outcome_reasoning": "Carrier accepted our second counter at $2,575.",
            "sentiment": "positive",
            "sentiment_reasoning": "Carrier friendly, smooth close.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1001 at $2,575 after 2 rounds. Positive call.",
        },
        {
            "call_id": "seed-booked-frustrated-001",
            "received_at": ago(0, 11),
            "started_at": ago(0, 11),
            "duration_seconds": 410,
            "mc_number": "500002",
            "carrier_legal_name": "GRANITE STATE HAULING INC",
            "carrier_eligible": 1,
            "load_id": "LD-1004",
            "loadboard_rate": 2200.0,
            "final_price": 2310.0,
            "final_carrier_offer": 2600.0,
            "negotiation_rounds": 3,
            "outcome": "booked",
            "outcome_reasoning": "Accepted final counter but pushed back on rate hard.",
            "sentiment": "frustrated",
            "sentiment_reasoning": "Carrier audibly annoyed with ceiling; threatened to walk twice.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1004 but carrier frustrated. Follow up before pickup.",
        },
        {
            "call_id": "seed-declined-price-001",
            "received_at": ago(0, 15),
            "started_at": ago(0, 15),
            "duration_seconds": 280,
            "mc_number": "500003",
            "carrier_legal_name": "BLUE RIDGE TRANSPORT CO",
            "carrier_eligible": 1,
            "load_id": "LD-1005",
            "loadboard_rate": 2800.0,
            "final_price": None,
            "final_carrier_offer": 3400.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Carrier wouldn't come below $3,400; ceiling was $3,080.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Professional but firm.",
            "transfer_attempted": 0,
            "summary": "Walked away on price for LD-1005.",
        },
        {
            "call_id": "seed-booked-002",
            "received_at": ago(0, 10),
            "started_at": ago(0, 10),
            "duration_seconds": 195,
            "mc_number": "500004",
            "carrier_legal_name": "MIDWEST EXPRESS LINES",
            "carrier_eligible": 1,
            "load_id": "LD-1002",
            "loadboard_rate": 1800.0,
            "final_price": 1890.0,
            "final_carrier_offer": 1950.0,
            "negotiation_rounds": 1,
            "outcome": "booked",
            "outcome_reasoning": "Carrier accepted first counter.",
            "sentiment": "positive",
            "sentiment_reasoning": "Easy close.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1002 at $1,890. One-round close.",
        },
        {
            "call_id": "seed-declined-price-002",
            "received_at": ago(0, 16),
            "started_at": ago(0, 16),
            "duration_seconds": 305,
            "mc_number": "500005",
            "carrier_legal_name": "PACIFIC RIM TRUCKING LLC",
            "carrier_eligible": 1,
            "load_id": "LD-1005",
            "loadboard_rate": 2800.0,
            "final_price": None,
            "final_carrier_offer": 3350.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Wouldn't take less than $3,350. Over ceiling.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Polite walk-away.",
            "transfer_attempted": 0,
            "summary": "Second decline on LD-1005 — ceiling may be too low.",
        },
        {
            "call_id": "seed-ineligible-001",
            "received_at": ago(0, 13),
            "started_at": ago(0, 13),
            "duration_seconds": 65,
            "mc_number": "999001",
            "carrier_legal_name": "DORMANT CARRIER INC",
            "carrier_eligible": 0,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "ineligible_carrier",
            "outcome_reasoning": "Operating authority inactive per FMCSA.",
            "ineligibility_reasons": ["operating_authority_inactive"],
            "sentiment": "neutral",
            "sentiment_reasoning": "Carrier understood, didn't push back.",
            "transfer_attempted": 0,
            "summary": "Verification failed: inactive authority.",
        },
        {
            "call_id": "seed-booked-003",
            "received_at": ago(0, 12),
            "started_at": ago(0, 12),
            "duration_seconds": 240,
            "mc_number": "500006",
            "carrier_legal_name": "GULF COAST LOGISTICS",
            "carrier_eligible": 1,
            "load_id": "LD-1003",
            "loadboard_rate": 3200.0,
            "final_price": 3200.0,
            "final_carrier_offer": 3200.0,
            "negotiation_rounds": 1,
            "outcome": "booked",
            "outcome_reasoning": "Accepted loadboard rate immediately.",
            "sentiment": "positive",
            "sentiment_reasoning": "Quick yes.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1003 at loadboard. Zero margin but fast.",
        },

        # --- yesterday (2 rows) ---
        {
            "call_id": "seed-declined-nomatch-001",
            "received_at": ago(1, 14),
            "started_at": ago(1, 14),
            "duration_seconds": 95,
            "mc_number": "500007",
            "carrier_legal_name": "NORTHSTAR FREIGHT GROUP",
            "carrier_eligible": 1,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "declined_no_match",
            "outcome_reasoning": "Carrier wanted MT→ND dry van; none available.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Brief, professional.",
            "transfer_attempted": 0,
            "summary": "No lane match for carrier's preferred origin.",
        },
        {
            "call_id": "seed-ineligible-002",
            "received_at": ago(1, 10),
            "started_at": ago(1, 10),
            "duration_seconds": 50,
            "mc_number": "999002",
            "carrier_legal_name": "OUT-OF-SERVICE EXPRESS",
            "carrier_eligible": 0,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "ineligible_carrier",
            "outcome_reasoning": "Carrier flagged out-of-service.",
            "ineligibility_reasons": ["out_of_service"],
            "sentiment": "negative",
            "sentiment_reasoning": "Argued the flag was outdated.",
            "transfer_attempted": 0,
            "summary": "Verification failed: out-of-service flag.",
        },

        # --- 2 days ago (2 rows) ---
        {
            "call_id": "seed-booked-004",
            "received_at": ago(2, 13),
            "started_at": ago(2, 13),
            "duration_seconds": 175,
            "mc_number": "500008",
            "carrier_legal_name": "DESERT CROSSING TRUCKING",
            "carrier_eligible": 1,
            "load_id": "LD-1006",
            "loadboard_rate": 2100.0,
            "final_price": 2310.0,
            "final_carrier_offer": 2400.0,
            "negotiation_rounds": 2,
            "outcome": "booked",
            "outcome_reasoning": "Met in the middle on round 2.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Normal back-and-forth.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1006 at $2,310.",
        },
        {
            "call_id": "seed-ineligible-003",
            "received_at": ago(2, 12),
            "started_at": ago(2, 12),
            "duration_seconds": 70,
            "mc_number": "999003",
            "carrier_legal_name": "UNINSURED HAULERS LLC",
            "carrier_eligible": 0,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "ineligible_carrier",
            "outcome_reasoning": "No insurance on file with FMCSA.",
            "ineligibility_reasons": ["no_insurance_on_file"],
            "sentiment": "neutral",
            "sentiment_reasoning": "Acknowledged, hung up.",
            "transfer_attempted": 0,
            "summary": "Verification failed: insurance not on file.",
        },
    ]
    for row in base:
        row.update(_usage_for(row["call_id"]))
    return base


_COLS = (
    "call_id", "started_at", "ended_at", "duration_seconds", "mc_number",
    "carrier_legal_name", "carrier_eligible", "load_id", "loadboard_rate",
    "final_price", "final_carrier_offer", "negotiation_rounds",
    "outcome", "outcome_reasoning", "ineligibility_reasons",
    "sentiment", "sentiment_reasoning",
    "transfer_attempted", "transcript_url", "recording_url", "summary",
    "raw_payload", "received_at",
    "extract_model", "extract_input_tokens", "extract_output_tokens",
    "extract_reasoning_tokens", "extract_cached_input_tokens",
    "outcome_model", "outcome_input_tokens", "outcome_output_tokens",
    "outcome_reasoning_tokens", "outcome_cached_input_tokens",
    "sentiment_model", "sentiment_input_tokens", "sentiment_output_tokens",
    "sentiment_reasoning_tokens", "sentiment_cached_input_tokens",
)


def _to_params(row: dict) -> dict:
    p = {c: None for c in _COLS}
    for k, v in row.items():
        if isinstance(v, datetime):
            p[k] = _iso(v)
        elif k == "ineligibility_reasons" and v is not None:
            p[k] = json.dumps(v)
        else:
            p[k] = v
    # raw_payload mirrors what an ingest POST would have stored, so /calls
    # detail views look identical to organic calls.
    payload = {k: (v if not isinstance(v, datetime) else _iso(v)) for k, v in row.items()}
    p["raw_payload"] = json.dumps(payload)
    return p


def main() -> int:
    db_path = os.getenv("DB_PATH", "/data/app.db")
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Wipe prior seed-* rows so a re-seed picks up any new columns or shifted
    # dates from this script. Only touches the seed-* prefix; organic call rows
    # (UUID call_ids from real ingest) are untouched.
    reset = conn.execute("DELETE FROM call_events WHERE call_id LIKE 'seed-%'").rowcount
    conn.commit()
    if reset:
        print(f"  reset: cleared {reset} prior seed-* rows")

    inserted = 0
    skipped = 0
    placeholders = ", ".join(f":{c}" for c in _COLS)
    cols_sql = ", ".join(_COLS)

    for row in _rows():
        existing = conn.execute(
            "SELECT 1 FROM call_events WHERE call_id = ?", (row["call_id"],)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        if row.get("load_id"):
            load_ok = conn.execute(
                "SELECT 1 FROM loads WHERE load_id = ?", (row["load_id"],)
            ).fetchone()
            if not load_ok:
                print(f"  skip {row['call_id']}: load {row['load_id']} not in loads table")
                skipped += 1
                continue
        params = _to_params(row)
        conn.execute(f"INSERT INTO call_events ({cols_sql}) VALUES ({placeholders})", params)
        inserted += 1
        print(f"  + {row['call_id']}  {row['outcome']}  {row.get('sentiment') or '-'}")

    conn.commit()
    conn.close()
    print(f"\nSeed complete: inserted={inserted} skipped={skipped} db={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
