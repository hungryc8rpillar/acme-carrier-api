import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SEED_PATH = Path(__file__).parent / "seed_loads.json"

# Seed templates use fixed dates anchored to this reference day. On every
# startup we shift them so loads always sit in the near-future of "now".
SEED_REFERENCE_DATE = date(2026, 5, 14)


def _shift_iso(value: str, offset_days: int) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    shifted = dt + timedelta(days=offset_days)
    if shifted.tzinfo is not None:
        shifted = shifted.astimezone(timezone.utc).replace(tzinfo=None)
        return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")
    return shifted.isoformat()


def _db_path() -> Path:
    return Path(os.getenv("DB_PATH", "data/app.db"))


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_call_events(conn: sqlite3.Connection) -> None:
    """Idempotent column adds for the persistent prod DB.

    schema.sql uses CREATE TABLE IF NOT EXISTS, which is a no-op on an existing
    table — new columns won't materialize on the Fly volume's DB without an
    explicit ALTER. Check `PRAGMA table_info` first so we don't re-add on fresh DBs.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(call_events)").fetchall()}
    additions = (
        ("final_carrier_offer", "REAL"),
        ("outcome_reasoning", "TEXT"),
        ("sentiment_reasoning", "TEXT"),
        ("ineligibility_reasons", "TEXT"),  # JSON-encoded list[str]
        # Per-node LLM usage from the three post-call AI nodes (Extract,
        # Classify-outcome, Classify-sentiment). The voice agent node doesn't
        # expose token usage as workflow variables, so it stays a flat constant.
        ("extract_model", "TEXT"),
        ("extract_input_tokens", "INTEGER"),
        ("extract_output_tokens", "INTEGER"),
        ("extract_reasoning_tokens", "INTEGER"),
        ("extract_cached_input_tokens", "INTEGER"),
        ("outcome_model", "TEXT"),
        ("outcome_input_tokens", "INTEGER"),
        ("outcome_output_tokens", "INTEGER"),
        ("outcome_reasoning_tokens", "INTEGER"),
        ("outcome_cached_input_tokens", "INTEGER"),
        ("sentiment_model", "TEXT"),
        ("sentiment_input_tokens", "INTEGER"),
        ("sentiment_output_tokens", "INTEGER"),
        ("sentiment_reasoning_tokens", "INTEGER"),
        ("sentiment_cached_input_tokens", "INTEGER"),
    )
    for name, sql_type in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE call_events ADD COLUMN {name} {sql_type}")


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate_call_events(conn)
        if not SEED_PATH.exists():
            return
        seed = json.loads(SEED_PATH.read_text())
        offset_days = (datetime.now(timezone.utc).date() - SEED_REFERENCE_DATE).days
        keys = (
            "load_id", "origin", "destination", "pickup_datetime", "delivery_datetime",
            "equipment_type", "loadboard_rate", "miles", "weight", "commodity_type",
            "num_of_pieces", "dimensions", "notes", "status",
        )
        normalized = []
        for row in seed:
            entry = {k: row.get(k) for k in keys}
            entry["pickup_datetime"] = _shift_iso(entry["pickup_datetime"], offset_days)
            entry["delivery_datetime"] = _shift_iso(entry["delivery_datetime"], offset_days)
            normalized.append(entry)
        conn.execute("BEGIN")
        try:
            # defer FK checks so DELETE doesn't fail on call_events.load_id refs;
            # the matching load_ids are re-inserted below before COMMIT.
            conn.execute("PRAGMA defer_foreign_keys = 1")
            conn.execute("DELETE FROM loads")
            conn.executemany(
                """INSERT INTO loads (
                    load_id, origin, destination, pickup_datetime, delivery_datetime,
                    equipment_type, loadboard_rate, miles, weight, commodity_type,
                    num_of_pieces, dimensions, notes, status
                ) VALUES (
                    :load_id, :origin, :destination, :pickup_datetime, :delivery_datetime,
                    :equipment_type, :loadboard_rate, :miles, :weight, :commodity_type,
                    :num_of_pieces, :dimensions, :notes, COALESCE(:status, 'available')
                )""",
                normalized,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()
