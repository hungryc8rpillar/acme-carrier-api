import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth import require_api_key
from app.db import get_db
from app.models import LoadResult, LoadSearchResponse
from app.services.locations import expand_location_query
from app.services.pitch import pitch_summary

router = APIRouter(prefix="/loads", tags=["loads"])

EQUIPMENT_ALIASES = {
    "van": "Dry Van",
    "dry van": "Dry Van",
    "dryvan": "Dry Van",
    "reefer": "Reefer",
    "refrigerated": "Reefer",
    "flatbed": "Flatbed",
    "flat": "Flatbed",
    "power only": "Power Only",
    "po": "Power Only",
    "step deck": "Step Deck",
    "stepdeck": "Step Deck",
}


def _normalize_equipment(value: str | None) -> str | None:
    if not value:
        return None
    return EQUIPMENT_ALIASES.get(value.strip().lower(), value.strip())


@router.get("/search", response_model=LoadSearchResponse)
def search_loads(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    origin: str | None = Query(default=None),
    destination: str | None = Query(default=None),
    equipment_type: str | None = Query(default=None),
    pickup_after: str | None = Query(default=None),
    pickup_before: str | None = Query(default=None),
    min_rate: float | None = Query(default=None, ge=0),
    limit: int = Query(default=3, ge=1, le=10),
) -> LoadSearchResponse:
    where = ["status = 'available'"]
    params: list = []

    if origin:
        candidates = expand_location_query(origin)
        where.append("(" + " OR ".join(["LOWER(origin) LIKE LOWER(?)"] * len(candidates)) + ")")
        params.extend(f"%{c}%" for c in candidates)
    if destination:
        candidates = expand_location_query(destination)
        where.append("(" + " OR ".join(["LOWER(destination) LIKE LOWER(?)"] * len(candidates)) + ")")
        params.extend(f"%{c}%" for c in candidates)
    if (eq := _normalize_equipment(equipment_type)):
        where.append("equipment_type = ?")
        params.append(eq)
    if pickup_after:
        where.append("pickup_datetime >= ?")
        params.append(pickup_after)
    if pickup_before:
        where.append("pickup_datetime <= ?")
        params.append(pickup_before)
    if min_rate is not None:
        where.append("loadboard_rate >= ?")
        params.append(min_rate)

    sql = (
        "SELECT * FROM loads WHERE "
        + " AND ".join(where)
        + " ORDER BY pickup_datetime ASC LIMIT ?"
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        load = dict(row)
        rpm = round(load["loadboard_rate"] / load["miles"], 2) if load["miles"] else 0.0
        results.append(
            LoadResult(
                load_id=load["load_id"],
                origin=load["origin"],
                destination=load["destination"],
                pickup_datetime=load["pickup_datetime"],
                delivery_datetime=load["delivery_datetime"],
                equipment_type=load["equipment_type"],
                loadboard_rate=load["loadboard_rate"],
                rate_per_mile=rpm,
                miles=load["miles"],
                weight=load.get("weight"),
                commodity_type=load.get("commodity_type"),
                num_of_pieces=load.get("num_of_pieces"),
                dimensions=load.get("dimensions"),
                notes=load.get("notes"),
                pitch_summary=pitch_summary(load),
            )
        )

    message = None if results else "No matching loads found."
    return LoadSearchResponse(count=len(results), results=results, message=message)
