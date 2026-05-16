"""Delete seed-* rows and the retired Capital Transportation faked call.

Keeps the B Marron Logistics real-recorded call live so the dashboard isn't
empty before a re-recording session. Pair with export_db_snapshot.py — always
take a snapshot first.

Usage (deployed):
    fly ssh console -a acme-carrier-api -C "python /app/scripts/wipe_demo_rows.py"

Usage (local):
    DB_PATH=./data/app.db python scripts/wipe_demo_rows.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

# Retired: faked-ineligible Capital Transportation call. The new recording
# session will use real FMCSA-ineligible MCs (MC658432, MC689517), so this
# env-override demo row is no longer needed.
CAPITAL_TRANSPORTATION_CALL_ID = "1ead7ad9-2473-4942-859d-fa1b606cc9d7"

# Keep: B Marron Logistics real recorded call. Dashboard needs at least one
# row so /metrics/today and /calls aren't empty between sessions.
B_MARRON_CALL_ID = "88b36169-c07c-4619-8282-c60b75aa89e7"


def main() -> int:
    db_path = os.getenv("DB_PATH", "/data/app.db")
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    seed_deleted = conn.execute(
        "DELETE FROM call_events WHERE call_id LIKE 'seed-%'"
    ).rowcount
    capital_deleted = conn.execute(
        "DELETE FROM call_events WHERE call_id = ?",
        (CAPITAL_TRANSPORTATION_CALL_ID,),
    ).rowcount
    conn.commit()

    remaining = conn.execute(
        "SELECT call_id, carrier_legal_name, outcome FROM call_events ORDER BY received_at ASC"
    ).fetchall()
    conn.close()

    print(f"  deleted seed-*                          : {seed_deleted}")
    print(f"  deleted Capital Transportation ({CAPITAL_TRANSPORTATION_CALL_ID}) : {capital_deleted}")
    print(f"\n  remaining rows: {len(remaining)}")
    for r in remaining:
        print(f"    - {r['call_id']}  {r['carrier_legal_name'] or '-'}  {r['outcome'] or '-'}")

    if not any(r["call_id"] == B_MARRON_CALL_ID for r in remaining):
        print(f"\nWARNING: expected B Marron call ({B_MARRON_CALL_ID}) is not present", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
