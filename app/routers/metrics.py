import sqlite3
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth import require_api_key
from app.db import get_db
from app.models import (
    AttentionItem,
    MetricsToday,
    TimeseriesPoint,
    TimeseriesResponse,
)
from app.services.attention import build_attention_queue
from app.services.pricing import cost_for_node, voice_agent_cost_for_call

router = APIRouter(tags=["metrics"])


def _today_filter() -> str:
    return "date(received_at) = date('now')"


@router.get("/metrics/today", response_model=MetricsToday)
def metrics_today(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> MetricsToday:
    booked = conn.execute(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(final_price), 0) AS revenue,
                   AVG((final_price - loadboard_rate) / loadboard_rate) AS avg_margin
            FROM call_events
            WHERE outcome = 'booked' AND {_today_filter()}"""
    ).fetchone()

    non_abandoned = conn.execute(
        f"""SELECT COUNT(*) AS n FROM call_events
            WHERE outcome IS NOT NULL AND outcome != 'abandoned' AND {_today_filter()}"""
    ).fetchone()["n"]

    avg_rounds_row = conn.execute(
        f"""SELECT AVG(negotiation_rounds) AS r FROM call_events
            WHERE negotiation_rounds IS NOT NULL AND {_today_filter()}"""
    ).fetchone()

    sentiment_rows = conn.execute(
        f"""SELECT sentiment, COUNT(*) AS n FROM call_events
            WHERE sentiment IS NOT NULL AND {_today_filter()}
            GROUP BY sentiment"""
    ).fetchall()

    sentiment_mix = {"positive": 0, "neutral": 0, "negative": 0, "frustrated": 0}
    for r in sentiment_rows:
        sentiment_mix[r["sentiment"]] = r["n"]

    win_rate = (booked["n"] / non_abandoned) if non_abandoned else None

    # Cost rollup: one flat voice charge per call, plus real-token costs from
    # the three post-call AI nodes. We compute in Python (not SQL) because the
    # per-model pricing table lives in app.services.pricing, not the DB.
    cost_rows = conn.execute(
        f"""SELECT extract_model, extract_input_tokens, extract_output_tokens, extract_cached_input_tokens,
                   outcome_model, outcome_input_tokens, outcome_output_tokens, outcome_cached_input_tokens,
                   sentiment_model, sentiment_input_tokens, sentiment_output_tokens, sentiment_cached_input_tokens
            FROM call_events
            WHERE {_today_filter()}"""
    ).fetchall()
    voice_total = voice_agent_cost_for_call() * len(cost_rows)
    enrichment_total = 0.0
    for r in cost_rows:
        enrichment_total += cost_for_node(
            r["extract_model"], r["extract_input_tokens"],
            r["extract_output_tokens"], r["extract_cached_input_tokens"],
        )
        enrichment_total += cost_for_node(
            r["outcome_model"], r["outcome_input_tokens"],
            r["outcome_output_tokens"], r["outcome_cached_input_tokens"],
        )
        enrichment_total += cost_for_node(
            r["sentiment_model"], r["sentiment_input_tokens"],
            r["sentiment_output_tokens"], r["sentiment_cached_input_tokens"],
        )

    return MetricsToday(
        date=date.today().isoformat(),
        loads_booked=booked["n"],
        revenue=float(booked["revenue"] or 0),
        avg_margin_vs_loadboard_pct=(
            round(float(booked["avg_margin"]) * 100, 2) if booked["avg_margin"] is not None else None
        ),
        win_rate=round(win_rate, 4) if win_rate is not None else None,
        avg_rounds=round(float(avg_rounds_row["r"]), 2) if avg_rounds_row["r"] is not None else None,
        sentiment_mix=sentiment_mix,
        est_ai_cost_voice_today_usd=round(voice_total, 6),
        est_ai_cost_enrichment_today_usd=round(enrichment_total, 6),
        est_ai_cost_today_usd=round(voice_total + enrichment_total, 6),
    )


@router.get("/metrics/timeseries", response_model=TimeseriesResponse)
def metrics_timeseries(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    days: int = Query(default=14, ge=1, le=90),
) -> TimeseriesResponse:
    rows = conn.execute(
        f"""SELECT date(received_at) AS d,
                   SUM(CASE WHEN outcome = 'booked' THEN 1 ELSE 0 END) AS booked,
                   COALESCE(SUM(CASE WHEN outcome = 'booked' THEN final_price ELSE 0 END), 0) AS revenue,
                   AVG(CASE WHEN outcome = 'booked'
                            THEN (final_price - loadboard_rate) / loadboard_rate END) AS avg_margin,
                   SUM(CASE WHEN outcome IS NOT NULL AND outcome != 'abandoned' THEN 1 ELSE 0 END) AS non_abandoned
            FROM call_events
            WHERE received_at >= datetime('now', '-{days} days')
            GROUP BY d
            ORDER BY d ASC"""
    ).fetchall()
    points = []
    for r in rows:
        non_abandoned = r["non_abandoned"]
        booked = r["booked"]
        win = (booked / non_abandoned) if non_abandoned else None
        points.append(
            TimeseriesPoint(
                date=r["d"],
                loads_booked=booked,
                revenue=float(r["revenue"] or 0),
                avg_margin_vs_loadboard_pct=(
                    round(float(r["avg_margin"]) * 100, 2) if r["avg_margin"] is not None else None
                ),
                win_rate=round(win, 4) if win is not None else None,
            )
        )
    return TimeseriesResponse(days=days, points=points)


@router.get("/attention", response_model=list[AttentionItem])
def attention(
    _: Annotated[str, Depends(require_api_key)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[AttentionItem]:
    items = build_attention_queue(conn)
    return [AttentionItem(**item) for item in items]
