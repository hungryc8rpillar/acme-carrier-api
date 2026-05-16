"""Dump every call_events row to a timestamped JSON snapshot.

Used to checkpoint demo state before a recording session so we can restore
the exact dashboard view afterward if needed. Pairs with restore_from_snapshot.py.

Usage (deployed):
    fly ssh console -a acme-carrier-api -C "python /app/scripts/export_db_snapshot.py"
    fly ssh sftp get /app/scripts/snapshots/snapshot_pre_recording_<ts>.json ./scripts/snapshots/

Usage (local):
    DB_PATH=./data/app.db python scripts/export_db_snapshot.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB = "/data/app.db"
DEFAULT_OUT_DIR = Path(__file__).parent / "snapshots"


def main() -> int:
    db_path = os.getenv("DB_PATH", DEFAULT_DB)
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}", file=sys.stderr)
        return 1

    out_dir = Path(os.getenv("SNAPSHOT_DIR", str(DEFAULT_OUT_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"snapshot_pre_recording_{ts}.json"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM call_events ORDER BY received_at ASC").fetchall()
    conn.close()

    records = [dict(r) for r in rows]
    out_path.write_text(json.dumps({
        "exported_at": datetime.now(UTC).isoformat(),
        "db_path": db_path,
        "table": "call_events",
        "row_count": len(records),
        "rows": records,
    }, indent=2))

    seed_n = sum(1 for r in records if (r.get("call_id") or "").startswith("seed-"))
    real_n = len(records) - seed_n
    print(f"  seed-*    : {seed_n}")
    print(f"  real UUID : {real_n}")
    print(f"  total     : {len(records)}")
    print(f"\nSnapshot written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
