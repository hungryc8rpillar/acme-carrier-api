"""Seed 5 synthetic May 16 call_events to break up today's outcome clustering.

Goal: today's call log reads like a live broker desk — interleaved outcomes,
repeat callers, current activity. Not "all wins clustered then all losses
clustered." Inserts an early no-match, a fresh booking, an ineligible
carrier, a repeat-caller booking, and a closing positive booking.

Constraints honored from the spec:
  * All MCs are FMCSA-verified (probed live earlier)
  * All load_ids exist in seed_loads.json
  * Token telemetry stays null — AI cost tile not inflated
  * call_id prefix `seed-today-` makes these identifiable for cleanup

Wipes prior seed-today-* rows on each run so re-runs reflect edits.
Organic call rows and other seed-* rows are untouched.

Usage (deployed):
    fly machine exec <machine_id> -a acme-carrier-api "python /app/scripts/seed_today_calls.py"

Usage (local):
    DB_PATH=./data/app.db python scripts/seed_today_calls.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime


def _iso(dt: datetime) -> str:
    # Space-format, naive UTC. Matches SQLite's datetime() output so these
    # rows sort string-wise alongside any rows that were shifted via
    # `datetime(col, '+N hours')`. ISO-T-with-tz format would sort AFTER
    # space-format rows (T > space) and cluster oddly in the call log.
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


# ─── synthetic seed rows ─────────────────────────────────────────────────────
# All outcome_reasoning / sentiment_reasoning strings below are SYNTHETIC,
# written to look like what the post-call Classify nodes would have produced.
def _rows() -> list[dict]:
    return [
        # Row 1 — Morning no-match (breaks the booking streak at the start)
        {
            "call_id": "seed-today-001",
            "started_at": _utc(2026, 5, 16, 7, 18),
            "ended_at": _utc(2026, 5, 16, 7, 21),
            "received_at": _utc(2026, 5, 16, 7, 21),
            "duration_seconds": 180,
            "mc_number": "268135",
            "carrier_legal_name": "LECLAIRE SERVICE CORPORATION",
            "carrier_eligible": 1,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": 0,
            "outcome": "declined_no_match",
            "outcome_reasoning": (
                "Carrier requested a lane (Portland flatbed) not in current "
                "inventory. Search returned zero matches; agent provided polite "
                "decline and captured callback."
            ),
            "sentiment": "neutral",
            "sentiment_reasoning": (
                "Carrier was professional and brief, accepted the no-match "
                "outcome without pushback. Asked for a callback. Transactional."
            ),
            "transfer_attempted": 0,
            "summary": (
                "Carrier called from Pacific Northwest looking for Portland-out "
                "flatbed. Searched inventory — no flatbed lanes out of Portland "
                "today. Polite decline, carrier accepted callback offer."
            ),
        },
        # Row 2 — ZLI Freight books mid-morning (interrupts booking pattern)
        {
            "call_id": "seed-today-002",
            "started_at": _utc(2026, 5, 16, 8, 14),
            "ended_at": _utc(2026, 5, 16, 8, 20),
            "received_at": _utc(2026, 5, 16, 8, 20),
            "duration_seconds": 360,
            "mc_number": "64938",
            "carrier_legal_name": "ZLI FREIGHT MANAGEMENT INC",
            "carrier_eligible": 1,
            "load_id": "LD-1009",
            "loadboard_rate": 2100.0,
            "final_price": 2210.0,
            "final_carrier_offer": 2250.0,
            "negotiation_rounds": 2,
            "outcome": "booked",
            "outcome_reasoning": (
                "Successful negotiation in 2 rounds. Carrier accepted the "
                "agent's second counter at $2,210 (5% above loadboard, within "
                "ceiling). Transfer to rep confirmed."
            ),
            "sentiment": "positive",
            "sentiment_reasoning": (
                "Friendly tone, agreed quickly, expressed appreciation. "
                "'Sounds good, send the rate con.' No friction."
            ),
            "transfer_attempted": 1,
            "summary": (
                "ZLI Freight called about Minneapolis-out step deck. Agent "
                "pitched LD-1009 (MSP -> KC, step deck, $2,100). Carrier "
                "countered at $2,300; agent counter at $2,210 accepted on "
                "round 2. Transferred to rep for booking."
            ),
        },
        # Row 3 — Brian J Ness ineligible (breaks the booking streak)
        # ineligibility_reason substituted from spec's "insurance_below_threshold"
        # to "no_insurance_on_file" (the canonical code in app/services/fmcsa.py).
        {
            "call_id": "seed-today-003",
            "started_at": _utc(2026, 5, 16, 8, 22),
            "ended_at": _utc(2026, 5, 16, 8, 25),
            "received_at": _utc(2026, 5, 16, 8, 25),
            "duration_seconds": 180,
            "mc_number": "308415",
            "carrier_legal_name": "BRIAN J NESS",
            "carrier_eligible": 0,
            "load_id": None,
            "loadboard_rate": None,
            "final_price": None,
            "final_carrier_offer": None,
            "negotiation_rounds": 0,
            "outcome": "ineligible_carrier",
            "outcome_reasoning": (
                "Carrier MC verified but failed eligibility check — insurance "
                "amount below $1M minimum required by Acme. Agent declined to "
                "dispatch loads and offered callback once insurance is updated."
            ),
            "ineligibility_reasons": ["no_insurance_on_file"],
            "sentiment": "negative",
            "sentiment_reasoning": (
                "Carrier was disappointed, said 'I just renewed last week, "
                "I'll have to check with my agent.' Resigned tone, accepted "
                "callback. Not annoyed at agent, frustrated at outcome."
            ),
            "transfer_attempted": 0,
            "summary": (
                "Brian J Ness called about Midwest lanes. FMCSA verification "
                "showed insurance below regulatory threshold. Agent informed "
                "carrier and captured callback for insurance update."
            ),
        },
        # Row 3.5 — Votre Choix mid-morning booking (breaks up the 08:35→09:10
        # run of three declines/no-matches with a clean 3-round close).
        {
            "call_id": "seed-today-006",
            "started_at": _utc(2026, 5, 16, 8, 55),
            "ended_at": _utc(2026, 5, 16, 9, 2),
            "received_at": _utc(2026, 5, 16, 9, 2),
            "duration_seconds": 420,
            "mc_number": "142385",
            "carrier_legal_name": "VOTRE CHOIX TRANSPORT INC",
            "carrier_eligible": 1,
            "load_id": "LD-1002",
            "loadboard_rate": 1150.0,
            "final_price": 1220.0,
            "final_carrier_offer": 1290.0,
            "negotiation_rounds": 3,
            "outcome": "booked",
            "outcome_reasoning": (
                "Three-round close. Carrier opened at $1,290; agent countered "
                "$1,200, carrier held $1,260, settled at $1,220 on round 3 "
                "(~6% above loadboard, comfortably under ceiling)."
            ),
            "sentiment": "positive",
            "sentiment_reasoning": (
                "Engaged but firm throughout; happy with final rate. Confirmed "
                "pickup details before transfer."
            ),
            "transfer_attempted": 1,
            "summary": (
                "Votre Choix Transport booked LD-1002 (LA -> Phoenix DV, "
                "$1,150) at $1,220 after 3 rounds. Transferred to rep."
            ),
        },
        # Row 4 — Gravytrain calls back (repeat caller, books second time)
        {
            "call_id": "seed-today-004",
            "started_at": _utc(2026, 5, 16, 10, 12),
            "ended_at": _utc(2026, 5, 16, 10, 17),
            "received_at": _utc(2026, 5, 16, 10, 17),
            "duration_seconds": 300,
            "mc_number": "491638",
            "carrier_legal_name": "GRAVYTRAIN EXPRESS INC",
            "carrier_eligible": 1,
            "load_id": "LD-1010",
            "loadboard_rate": 680.0,
            "final_price": 735.0,
            "final_carrier_offer": 760.0,
            "negotiation_rounds": 2,
            "outcome": "booked",
            "outcome_reasoning": (
                "Repeat caller from earlier today. Different lane this time; "
                "matched LD-1010 inventory. Settled at $735 (~8% above "
                "loadboard, within ceiling). Transfer to rep confirmed."
            ),
            "sentiment": "neutral",
            "sentiment_reasoning": (
                "Businesslike, focused, agreed without much back and forth. "
                "'OK, send it.' Transactional."
            ),
            "transfer_attempted": 1,
            "summary": (
                "Gravytrain Express called back (earlier 09:10 call was a "
                "no-match). Took LD-1010 (Phoenix -> Vegas dry van, $680). "
                "Negotiated to $735 in 2 rounds. Transferred to rep."
            ),
        },
        # Row 5 — PWGEXPRESS books (NEWEST row, "current" activity, success)
        {
            "call_id": "seed-today-005",
            "started_at": _utc(2026, 5, 16, 10, 38),
            "ended_at": _utc(2026, 5, 16, 10, 42),
            "received_at": _utc(2026, 5, 16, 10, 42),
            "duration_seconds": 240,
            "mc_number": "154029",
            "carrier_legal_name": "PWGEXPRESS INC",
            "carrier_eligible": 1,
            "load_id": "LD-1013",
            "loadboard_rate": 540.0,
            "final_price": 590.0,
            "final_carrier_offer": 650.0,
            "negotiation_rounds": 1,
            "outcome": "booked",
            "outcome_reasoning": (
                "Quick negotiation, single round. $590 final (~9% above "
                "loadboard, within ceiling). Short haul lane, low risk."
            ),
            "sentiment": "positive",
            "sentiment_reasoning": (
                "Friendly, agreed quickly. 'Perfect, talk soon.' Positive close."
            ),
            "transfer_attempted": 1,
            "summary": (
                "PWGEXPRESS called about short hauls. Agent pitched LD-1013 "
                "(Savannah -> Jacksonville dry van, $540). Carrier countered "
                "$650; agent settled at $590 round 1. Transferred to rep."
            ),
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
        "DELETE FROM call_events WHERE call_id LIKE 'seed-today-%'"
    ).rowcount
    conn.commit()
    if reset:
        print(f"  reset: cleared {reset} prior seed-today-* rows")

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
