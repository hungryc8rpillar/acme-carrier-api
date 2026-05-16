import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import require_api_key
from app.db import get_db
from app.models import (
    CallDetail,
    CallEvent,
    CallEventStored,
    CallListItem,
    CallListResponse,
)

router = APIRouter(tags=["calls"])


# Columns writable from the request body. Order mirrors the INSERT column list.
_WRITABLE_COLS = (
    "started_at", "ended_at", "duration_seconds", "mc_number",
    "carrier_legal_name", "carrier_eligible", "load_id", "loadboard_rate",
    "final_price", "final_carrier_offer", "negotiation_rounds",
    "outcome", "outcome_reasoning", "ineligibility_reasons",
    "sentiment", "sentiment_reasoning",
    "transfer_attempted", "transcript_url", "recording_url", "summary",
    # Per-node LLM usage from post-call AI nodes. Grouped at the end so the
    # operational fields above stay visually distinct (decision log 5.16i).
    "extract_model", "extract_input_tokens", "extract_output_tokens",
    "extract_reasoning_tokens", "extract_cached_input_tokens",
    "outcome_model", "outcome_input_tokens", "outcome_output_tokens",
    "outcome_reasoning_tokens", "outcome_cached_input_tokens",
    "sentiment_model", "sentiment_input_tokens", "sentiment_output_tokens",
    "sentiment_reasoning_tokens", "sentiment_cached_input_tokens",
)


def _column_value(event: CallEvent, col: str):
    val = getattr(event, col)
    if col in ("started_at", "ended_at"):
        return val.isoformat() if val else None
    if col in ("carrier_eligible", "transfer_attempted"):
        return int(val) if val is not None else None
    if col == "ineligibility_reasons":
        return json.dumps(val) if val is not None else None
    return val


@router.post("/calls/events", response_model=CallEventStored)
def ingest_call_event(
    event: CallEvent,
    response: Response,
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> CallEventStored:
    # Two-writes pattern: HappyRobot writes a partial event mid-call (booking
    # signal for the dashboard) and a full event post-call (timing, transcript,
    # AI-classified outcome/sentiment). The second write must merge, not skip.
    raw_payload = json.dumps(event.model_dump(mode="json"))
    now = datetime.now(UTC).isoformat()
    explicit = event.model_fields_set

    existing = conn.execute(
        "SELECT 1 FROM call_events WHERE call_id = ?", (event.call_id,)
    ).fetchone()

    if existing:
        # Only overwrite columns the client explicitly sent — avoids nulling out
        # values populated by the mid-call write. Always refresh raw_payload and
        # received_at so the row reflects the latest authoritative POST.
        updates = {c: _column_value(event, c) for c in _WRITABLE_COLS if c in explicit}
        updates["raw_payload"] = raw_payload
        updates["received_at"] = now
        set_clause = ", ".join(f"{c} = :{c}" for c in updates)
        conn.execute(
            f"UPDATE call_events SET {set_clause} WHERE call_id = :call_id",
            {**updates, "call_id": event.call_id},
        )
        response.status_code = status.HTTP_200_OK
        return CallEventStored(call_id=event.call_id, stored=False, already_exists=True)

    insert_cols = ("call_id", *_WRITABLE_COLS, "raw_payload", "received_at")
    insert_params = {c: _column_value(event, c) for c in _WRITABLE_COLS}
    insert_params["call_id"] = event.call_id
    insert_params["raw_payload"] = raw_payload
    insert_params["received_at"] = now
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    conn.execute(
        f"INSERT INTO call_events ({', '.join(insert_cols)}) VALUES ({placeholders})",
        insert_params,
    )
    response.status_code = status.HTTP_201_CREATED
    return CallEventStored(call_id=event.call_id, stored=True, already_exists=False)


@router.get("/calls", response_model=CallListResponse)
def list_calls(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    outcome: str | None = Query(default=None),
    sentiment: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> CallListResponse:
    where: list[str] = []
    params: list = []
    if outcome:
        where.append("outcome = ?")
        params.append(outcome)
    if sentiment:
        where.append("sentiment = ?")
        params.append(sentiment)
    if since:
        where.append("received_at >= ?")
        params.append(since)
    sql = (
        "SELECT call_id, started_at, mc_number, carrier_legal_name, "
        "load_id, loadboard_rate, final_price, negotiation_rounds, "
        "outcome, sentiment FROM call_events"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY received_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return CallListResponse(
        count=len(rows),
        results=[CallListItem(**dict(r)) for r in rows],
    )


@router.get("/calls/{call_id}", response_model=CallDetail)
def get_call(
    call_id: str,
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> CallDetail:
    row = conn.execute(
        "SELECT * FROM call_events WHERE call_id = ?",
        (call_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    d = dict(row)
    if d.get("raw_payload"):
        try:
            d["raw_payload"] = json.loads(d["raw_payload"])
        except (ValueError, TypeError):
            d["raw_payload"] = None
    if d.get("ineligibility_reasons"):
        try:
            d["ineligibility_reasons"] = json.loads(d["ineligibility_reasons"])
        except (ValueError, TypeError):
            d["ineligibility_reasons"] = None
    # Derive ended_at when missing but reconstructable from started_at + duration.
    # Avoids forcing the dashboard to do this arithmetic on its side.
    if not d.get("ended_at") and d.get("started_at") and d.get("duration_seconds") is not None:
        try:
            started = datetime.fromisoformat(d["started_at"].replace("Z", "+00:00"))
            d["ended_at"] = (started + timedelta(seconds=int(d["duration_seconds"]))).isoformat()
        except (ValueError, TypeError):
            pass
    if d.get("carrier_eligible") is not None:
        d["carrier_eligible"] = bool(d["carrier_eligible"])
    if d.get("transfer_attempted") is not None:
        d["transfer_attempted"] = bool(d["transfer_attempted"])
    return CallDetail(**d)
