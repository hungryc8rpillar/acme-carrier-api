import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.db import get_db
from app.models import NegotiationRequest, NegotiationResponse
from app.services.negotiation import evaluate

router = APIRouter(prefix="/negotiations", tags=["negotiations"])


@router.post("/evaluate", response_model=NegotiationResponse)
def evaluate_negotiation(
    body: NegotiationRequest,
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> NegotiationResponse:
    row = conn.execute(
        "SELECT loadboard_rate FROM loads WHERE load_id = ?",
        (body.load_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Load {body.load_id} not found")

    decision = evaluate(
        load_id=body.load_id,
        loadboard_rate=float(row["loadboard_rate"]),
        carrier_offer=body.carrier_offer,
        round_num=body.round,
        our_last_offer=body.our_last_offer,
    )
    return NegotiationResponse(
        decision=decision.decision,
        round=decision.round,
        max_rounds=decision.max_rounds,
        rounds_remaining=decision.rounds_remaining,
        reason=decision.reason,
        agent_response_hint=decision.agent_response_hint,
        agreed_price=decision.agreed_price,
        our_counter=decision.our_counter,
    )
