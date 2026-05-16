"""Restore call_events rows from a snapshot JSON written by export_db_snapshot.py.

Safety policy: INSERT-IF-NOT-EXISTS. Rows whose call_id already exists in the
current DB are skipped — never clobber live data. Only columns present in the
current schema are written, so this tolerates additive migrations between
snapshot time and restore time.

Usage (local):
    DB_PATH=./data/app.db python scripts/restore_from_snapshot.py \\
        scripts/snapshots/snapshot_pre_recording_<ts>.json

Usage (deployed, after SFTPing snapshot to /tmp on the machine):
    fly ssh console -a acme-carrier-api -C \\
        "python /app/scripts/restore_from_snapshot.py /tmp/snapshot_<ts>.json"
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: restore_from_snapshot.py <snapshot.json>", file=sys.stderr)
        return 1

    snapshot_path = Path(argv[1])
    if not snapshot_path.exists():
        print(f"Snapshot not found: {snapshot_path}", file=sys.stderr)
        return 1

    db_path = os.getenv("DB_PATH", "/data/app.db")
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}", file=sys.stderr)
        return 1

    snapshot = json.loads(snapshot_path.read_text())
    rows = snapshot.get("rows") or []
    if not rows:
        print("Snapshot contains no rows", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    current_cols = {r["name"] for r in conn.execute("PRAGMA table_info(call_events)").fetchall()}

    restored = 0
    skipped = 0
    for row in rows:
        call_id = row.get("call_id")
        if not call_id:
            skipped += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM call_events WHERE call_id = ?", (call_id,)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        # Restrict to columns the current schema knows about so an older snapshot
        # with a dropped column (or a newer snapshot with not-yet-migrated columns)
        # still inserts cleanly.
        writable = {k: v for k, v in row.items() if k in current_cols}
        cols_sql = ", ".join(writable)
        placeholders = ", ".join(f":{c}" for c in writable)
        conn.execute(
            f"INSERT INTO call_events ({cols_sql}) VALUES ({placeholders})",
            writable,
        )
        restored += 1

    conn.commit()
    conn.close()

    print(f"  restored: {restored}")
    print(f"  skipped (already present or no call_id): {skipped}")
    print(f"  source: {snapshot_path}")
    print(f"  db: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
