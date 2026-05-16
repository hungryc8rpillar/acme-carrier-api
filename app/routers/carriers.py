import sqlite3
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_key
from app.db import get_db
from app.models import CarrierVerifyDetails, CarrierVerifyResponse
from app.services.fmcsa import verify_carrier

router = APIRouter(prefix="/carriers", tags=["carriers"])


@router.get("/verify", response_model=CarrierVerifyResponse)
def verify(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    mc_number: str = Query(..., min_length=1),
) -> CarrierVerifyResponse:
    try:
        result = verify_carrier(mc_number, conn)
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail={
                "mc_number": mc_number,
                "eligible": False,
                "status_summary": (
                    "I'm having trouble checking that MC with FMCSA right now. "
                    "Could you give us a quick moment, or call back in a bit?"
                ),
                "ineligibility_reasons": ["fmcsa_upstream_error"],
            },
        ) from None
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    details = CarrierVerifyDetails(**result.details) if result.details else None
    response = CarrierVerifyResponse(
        mc_number=result.mc_number,
        eligible=result.eligible,
        legal_name=result.legal_name,
        dba_name=result.dba_name,
        status_summary=result.status_summary,
        details=details,
        ineligibility_reasons=result.ineligibility_reasons,
    )
    if result.not_found:
        raise HTTPException(status_code=404, detail=response.model_dump())
    return response
