"""One-off: shift call_events timestamps by a fixed offset, preserving format.

Used 2026-05-19 to bring the dashboard's "today" view current after the demo DB
fell 3 days behind. Two timestamp formats coexist in call_events:
  - Real calls:  '2026-05-16T12:00:57.714547+00:00'  (T, micros, offset)
  - Seed rows:   '2026-05-16 10:38:00'               (space, no micros, naive)
We detect by the presence of 'T' and re-render in the same shape.

Usage:
    fly ssh console -a acme-carrier-api -C "python /app/scripts/shift_call_events.py 72"
    DB_PATH=./data/app.db python scripts/shift_call_events.py 72   # local
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

COLS = ("started_at", "ended_at", "received_at")
DEFAULT_DB = "/data/app.db"


def shift(value: str | None, delta: timedelta) -> str | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    shifted = dt + delta
    if "T" in value:
        return shifted.isoformat()
    return shifted.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: shift_call_events.py <hours>", file=sys.stderr)
        return 2
    hours = int(sys.argv[1])
    delta = timedelta(hours=hours)

    db_path = os.getenv("DB_PATH", DEFAULT_DB)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT call_id, {', '.join(COLS)} FROM call_events"
        ).fetchall()
        updates = []
        for call_id, *vals in rows:
            updates.append((*(shift(v, delta) for v in vals), call_id))
        conn.executemany(
            f"UPDATE call_events SET {', '.join(c + ' = ?' for c in COLS)} "
            "WHERE call_id = ?",
            updates,
        )
        conn.commit()
        mn, mx = conn.execute(
            "SELECT MIN(received_at), MAX(received_at) FROM call_events"
        ).fetchone()
        print(f"shifted {len(updates)} rows by {hours}h")
        print(f"received_at range now: {mn}  →  {mx}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
