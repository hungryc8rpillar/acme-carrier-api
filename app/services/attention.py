"""Rules that surface items into the dashboard's 'needs attention' queue.

Each rule is a pure function: takes a SQLite connection, returns a list of
AttentionItem dicts. The registry below is what `/attention` iterates over.
"""

from __future__ import annotations

import sqlite3

WINDOW_DAYS = 7


def _floor_too_high(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"""SELECT load_id, COUNT(*) AS n,
                   GROUP_CONCAT(call_id) AS call_ids
            FROM call_events
            WHERE outcome = 'declined_price'
              AND received_at >= datetime('now', '-{WINDOW_DAYS} days')
              AND load_id IS NOT NULL
            GROUP BY load_id
            HAVING n >= 2"""
    ).fetchall()
    return [
        {
            "type": "floor_too_high",
            "severity": "warning",
            "title": f"Load {r['load_id']} declined on price {r['n']} times",
            "description": (
                f"Carriers walked away over price on load {r['load_id']} {r['n']} times in the last "
                f"{WINDOW_DAYS} days. Our ceiling may be too tight for this lane."
            ),
            "related_call_ids": (r["call_ids"] or "").split(","),
            "suggested_action": (
                f"Review the ceiling multiplier or repost {r['load_id']} at a higher rate."
            ),
        }
        for r in rows
    ]


def _sentiment_negative_booking(conn: sqlite3.Connection) -> list[dict]:
    # Fires on frustrated only, not negative. Rationale: rep follow-up calls
    # are operator labor; we surface only the calls where a rep can change the
    # outcome (relationship-recovery on frustrated carriers). Negative-sentiment
    # carriers got a fair price they didn't love — an unprompted check-in is
    # more awkward than helpful. The rule type name is kept for stability.
    rows = conn.execute(
        f"""SELECT call_id, carrier_legal_name, load_id, final_price
            FROM call_events
            WHERE outcome = 'booked'
              AND sentiment = 'frustrated'
              AND received_at >= datetime('now', '-{WINDOW_DAYS} days')
            ORDER BY received_at DESC"""
    ).fetchall()
    return [
        {
            "type": "sentiment_negative_booking",
            "severity": "warning",
            "title": f"Booked but frustrated: {r['carrier_legal_name'] or 'unknown carrier'} on {r['load_id']}",
            "description": (
                f"Carrier booked load {r['load_id']} at ${r['final_price']:,.0f} but sentiment was "
                f"frustrated. Worth a follow-up before they're back on the road."
            ),
            "related_call_ids": [r["call_id"]],
            "suggested_action": "Have a rep call the carrier to check in before pickup.",
        }
        for r in rows
    ]


RULES = [_floor_too_high, _sentiment_negative_booking]


def build_attention_queue(conn: sqlite3.Connection) -> list[dict]:
    items: list[dict] = []
    for rule in RULES:
        items.extend(rule(conn))
    return items
