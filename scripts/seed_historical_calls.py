"""Seed synthetic historical call_events spanning the 7 days before today.

Goal: populate the 14-day sparklines with shape and give the demo a
"system has been running" feel. Today's win rate (~58%) should look like
an improvement over the seeded historical baseline (~36%).

Constraints honored from the spec:
  * 11 rows across May 8-14 (Mon busier than Tue, weekend dip)
  * 4 booked / 4 declined_price / 2 declined_no_match / 1 ineligible
  * 2 of the declined_price rows are LD-1004 — combined with today's 2,
    floor_too_high will report "4 times in 7 days"
  * 1 booked-frustrated row so sentiment_negative_booking has a history
  * Mix of repeat callers (today's MCs) and new carriers from the
    untouched FMCSA pool (64938, 154029, 268135, 308415); ineligible
    row uses a real-FMCSA-ineligible MC (658432 MARK COOPER) from our probe
  * Token telemetry stays null so the AI cost tile is not inflated
  * call_id prefix `seed-hist-` makes these identifiable for cleanup

Wipes prior seed-hist-* rows on each run so re-runs reflect script edits.
Organic call rows (non-prefixed) and seed-* demo rows are untouched.

Usage (deployed):
    fly machine exec <machine_id> -a acme-carrier-api "python /app/scripts/seed_historical_calls.py"

Usage (local):
    DB_PATH=./data/app.db python scripts/seed_historical_calls.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _at(days_ago: int, hour: int, minute: int) -> datetime:
    """A timestamp `days_ago` days before today at the given hour/minute UTC."""
    return (datetime.now(UTC) - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


# ─── synthetic seed rows ─────────────────────────────────────────────────────
# All outcome_reasoning / sentiment_reasoning strings below are SYNTHETIC —
# written to look like what the post-call Classify nodes would have produced.
# They are not transcripts and not derived from real calls.
def _rows() -> list[dict]:
    return [
        # ── May 8 (Fri, 7d ago) — 1 row ───────────────────────────────────
        {
            "call_id": "seed-hist-001",
            "started_at": _at(7, 14, 23),
            "received_at": _at(7, 14, 23),
            "duration_seconds": 295,
            "mc_number": "268135",
            "carrier_legal_name": "LECLAIRE SERVICE CORPORATION",
            "carrier_eligible": 1,
            "load_id": "LD-1004",
            "loadboard_rate": 950.0,
            "final_price": None,
            "final_carrier_offer": 1280.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Carrier insisted on $1,280; over ceiling on this lane.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Professional throughout, just firm on rate.",
            "transfer_attempted": 0,
            "summary": "Walked away on price for LD-1004 after 3 rounds.",
        },

        # ── May 10 (Sun, 5d ago) — 1 row (weekend trickle) ────────────────
        {
            "call_id": "seed-hist-002",
            "started_at": _at(5, 11, 8),
            "received_at": _at(5, 11, 8),
            "duration_seconds": 175,
            "mc_number": "64938",
            "carrier_legal_name": "ZLI FREIGHT MANAGEMENT INC",
            "carrier_eligible": 1,
            "load_id": "LD-1002",
            "loadboard_rate": 1150.0,
            "final_price": 1195.0,
            "final_carrier_offer": 1240.0,
            "negotiation_rounds": 1,
            "outcome": "booked",
            "outcome_reasoning": "Carrier took first counter; clean weekend close.",
            "sentiment": "positive",
            "sentiment_reasoning": "Friendly and quick.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1002 at $1,195 in round 1.",
        },

        # ── May 11 (Mon, 4d ago) — 3 rows (Monday spike) ──────────────────
        {
            "call_id": "seed-hist-003",
            "started_at": _at(4, 8, 42),
            "received_at": _at(4, 8, 42),
            "duration_seconds": 110,
            "mc_number": "178293",  # repeat caller (today has LD-1008 booked)
            "carrier_legal_name": "JAMES M CAMPBELL",
            "carrier_eligible": 1,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "declined_no_match",
            "outcome_reasoning": "Carrier wanted MEM-Atlanta dry van; none open.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Brief, professional call.",
            "transfer_attempted": 0,
            "summary": "No matching load for carrier's preferred lane.",
        },
        {
            "call_id": "seed-hist-004",
            "started_at": _at(4, 13, 15),
            "received_at": _at(4, 13, 15),
            "duration_seconds": 240,
            "mc_number": "308415",
            "carrier_legal_name": "BRIAN J NESS",
            "carrier_eligible": 1,
            "load_id": "LD-1009",
            "loadboard_rate": 2100.0,
            "final_price": 2200.0,
            "final_carrier_offer": 2280.0,
            "negotiation_rounds": 2,
            "outcome": "booked",
            "outcome_reasoning": "Met in the middle on round 2.",
            "sentiment": "positive",
            "sentiment_reasoning": "Carrier pleased with the rate.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1009 at $2,200.",
        },
        {
            "call_id": "seed-hist-005",
            "started_at": _at(4, 16, 48),
            "received_at": _at(4, 16, 48),
            "duration_seconds": 265,
            "mc_number": "154029",
            "carrier_legal_name": "PWGEXPRESS INC",
            "carrier_eligible": 1,
            "load_id": "LD-1005",
            "loadboard_rate": 720.0,
            "final_price": None,
            "final_carrier_offer": 850.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Wouldn't come below $850; ceiling was ~$770.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Polite walk-away.",
            "transfer_attempted": 0,
            "summary": "Walked on price for LD-1005.",
        },

        # ── May 12 (Tue, 3d ago) — 2 rows ─────────────────────────────────
        {
            "call_id": "seed-hist-006",
            "started_at": _at(3, 9, 30),
            "received_at": _at(3, 9, 30),
            "duration_seconds": 55,
            "mc_number": "658432",  # real FMCSA-ineligible MC from probe pool
            "carrier_legal_name": "MARK COOPER",
            "carrier_eligible": 0,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "ineligible_carrier",
            "outcome_reasoning": "FMCSA shows operating authority inactive + OOS + no insurance.",
            "ineligibility_reasons": [
                "operating_authority_inactive",
                "out_of_service",
                "no_insurance_on_file",
            ],
            "sentiment": "negative",
            "sentiment_reasoning": "Carrier pushed back, said the flag was outdated.",
            "transfer_attempted": 0,
            "summary": "Verification failed: multiple FMCSA flags.",
        },
        {
            "call_id": "seed-hist-007",
            "started_at": _at(3, 14, 55),
            "received_at": _at(3, 14, 55),
            "duration_seconds": 310,
            "mc_number": "627195",  # repeat caller (today has LD-1007 booked)
            "carrier_legal_name": "LUCKY POLAR BEAR INC",
            "carrier_eligible": 1,
            "load_id": "LD-1004",
            "loadboard_rate": 950.0,
            "final_price": None,
            "final_carrier_offer": 1310.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Held at $1,310 through round 3; over ceiling.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Firm but professional.",
            "transfer_attempted": 0,
            "summary": "Second carrier this week to walk on LD-1004.",
        },

        # ── May 13 (Wed, 2d ago) — 2 rows ─────────────────────────────────
        {
            "call_id": "seed-hist-008",
            "started_at": _at(2, 10, 21),
            "received_at": _at(2, 10, 21),
            "duration_seconds": 420,
            "mc_number": "856291",  # repeat caller (today has LD-1004 declined)
            "carrier_legal_name": "ATF TRUCKING LLC",
            "carrier_eligible": 1,
            "load_id": "LD-1014",
            "loadboard_rate": 1420.0,
            "final_price": 1490.0,
            "final_carrier_offer": 1620.0,
            "negotiation_rounds": 3,
            "outcome": "booked",
            "outcome_reasoning": "Accepted final counter but pushed back hard.",
            "sentiment": "frustrated",
            "sentiment_reasoning": "Carrier audibly annoyed with the ceiling; threatened to walk twice.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1014 at $1,490 but carrier frustrated. Worth a follow-up.",
        },
        {
            "call_id": "seed-hist-009",
            "started_at": _at(2, 15, 40),
            "received_at": _at(2, 15, 40),
            "duration_seconds": 95,
            "mc_number": "215847",  # repeat caller (today has LD-1004 declined)
            "carrier_legal_name": "DAM CO TRUCKING INC",
            "carrier_eligible": 1,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": None,
            "outcome": "declined_no_match",
            "outcome_reasoning": "Carrier wanted Atlanta-Charlotte reefer; none open.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Quick professional call.",
            "transfer_attempted": 0,
            "summary": "No lane match for carrier's preferred origin.",
        },

        # ── May 14 (Thu, 1d ago) — 2 rows ─────────────────────────────────
        {
            "call_id": "seed-hist-010",
            "started_at": _at(1, 11, 12),
            "received_at": _at(1, 11, 12),
            "duration_seconds": 200,
            "mc_number": "287643",  # repeat caller (today has LD-1003 booked)
            "carrier_legal_name": "BENNETT COUNTY COOPERATIVE ASSOCIATION",
            "carrier_eligible": 1,
            "load_id": "LD-1013",
            "loadboard_rate": 540.0,
            "final_price": 565.0,
            "final_carrier_offer": 605.0,
            "negotiation_rounds": 1,
            "outcome": "booked",
            "outcome_reasoning": "Carrier took first counter quickly.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Standard back-and-forth, clean close.",
            "transfer_attempted": 1,
            "summary": "Booked LD-1013 at $565 in round 1.",
        },
        {
            "call_id": "seed-hist-011",
            "started_at": _at(1, 17, 33),
            "received_at": _at(1, 17, 33),
            "duration_seconds": 285,
            "mc_number": "491638",  # repeat caller (today has declined_no_match)
            "carrier_legal_name": "GRAVYTRAIN EXPRESS INC",
            "carrier_eligible": 1,
            "load_id": "LD-1012",
            "loadboard_rate": 590.0,
            "final_price": None,
            "final_carrier_offer": 720.0,
            "negotiation_rounds": 3,
            "outcome": "declined_price",
            "outcome_reasoning": "Held at $720; ceiling was ~$650.",
            "sentiment": "neutral",
            "sentiment_reasoning": "Polite firm walk-away.",
            "transfer_attempted": 0,
            "summary": "Walked on price for LD-1012.",
        },
    ]


# Columns inserted. Token telemetry fields intentionally omitted — spec
# requires them to stay null so the AI cost tile is not inflated.
_COLS = (
    "call_id", "started_at", "ended_at", "duration_seconds", "mc_number",
    "carrier_legal_name", "carrier_eligible", "load_id", "loadboard_rate",
    "final_price", "final_carrier_offer", "negotiation_rounds",
    "outcome", "outcome_reasoning", "ineligibility_reasons",
    "sentiment", "sentiment_reasoning",
    "transfer_attempted", "transcript_url", "recording_url", "summary",
    "raw_payload", "received_at",
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

    reset = conn.execute(
        "DELETE FROM call_events WHERE call_id LIKE 'seed-hist-%'"
    ).rowcount
    conn.commit()
    if reset:
        print(f"  reset: cleared {reset} prior seed-hist-* rows")

    inserted = 0
    skipped = 0
    placeholders = ", ".join(f":{c}" for c in _COLS)
    cols_sql = ", ".join(_COLS)

    for row in _rows():
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
        print(
            f"  + {row['call_id']}  {_iso(row['started_at'])[:16]}  "
            f"MC{row['mc_number']:<7}  {(row.get('load_id') or '—'):<8}  "
            f"{row['outcome']:<19}  {row.get('sentiment') or '-'}"
        )

    conn.commit()
    conn.close()
    print(f"\nSeed complete: inserted={inserted} skipped={skipped} db={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
