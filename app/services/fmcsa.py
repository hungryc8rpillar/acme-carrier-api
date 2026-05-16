"""FMCSA QCMobile client + eligibility rules + agent-readable status summaries."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

FMCSA_BASE = "https://mobile.fmcsa.dot.gov/qc/services"
CACHE_TTL = timedelta(hours=24)

REQUIRED_STATUS = "ACTIVE"


@dataclass
class CarrierVerification:
    mc_number: str
    eligible: bool
    legal_name: str | None
    dba_name: str | None
    status_summary: str
    details: dict | None
    ineligibility_reasons: list[str] | None
    not_found: bool = False


def normalize_mc(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits.lstrip("0") or "0"


def _demo_ineligible_mcs() -> set[str]:
    """Set of MC numbers (normalized, no MC prefix) flagged as ineligible for demo purposes.

    Source of truth is the DEMO_INELIGIBLE_MCS env var, comma-separated, e.g. "400003,500004".
    Read per-call so the set can be changed without restarting (Fly secret changes redeploy anyway).
    """
    raw = os.getenv("DEMO_INELIGIBLE_MCS", "")
    return {normalize_mc(x) for x in raw.split(",") if x.strip()}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _cache_get(conn: sqlite3.Connection, mc: str) -> dict | None:
    row = conn.execute(
        "SELECT payload, fetched_at FROM fmcsa_cache WHERE mc_number = ?",
        (mc,),
    ).fetchone()
    if not row:
        return None
    fetched_at = datetime.fromisoformat(row["fetched_at"])
    if _now() - fetched_at > CACHE_TTL:
        return None
    return json.loads(row["payload"])


def _cache_put(conn: sqlite3.Connection, mc: str, payload: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fmcsa_cache (mc_number, payload, fetched_at) VALUES (?, ?, ?)",
        (mc, json.dumps(payload), _iso(_now())),
    )


class FMCSANotFound(Exception):
    """FMCSA upstream returned 4xx for the lookup; treat as carrier not found."""


def _fetch_fmcsa(mc: str, api_key: str, client: httpx.Client | None = None) -> dict | None:
    """FMCSA's WAF sometimes 403s the first call after an idle period; retry once."""
    url = f"{FMCSA_BASE}/carriers/docket-number/{mc}"
    params = {"webKey": api_key}
    c = client or httpx.Client(timeout=10.0)
    try:
        last_status = None
        for attempt in range(2):
            resp = c.get(url, params=params)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 404):
                if attempt == 0:
                    logger.info("FMCSA %s for MC%s; retrying once", resp.status_code, mc)
                    time.sleep(0.6)
                    continue
                logger.info("FMCSA %s for MC%s after retry; treating as not_found", resp.status_code, mc)
                raise FMCSANotFound(mc)
            resp.raise_for_status()
        logger.error("FMCSA gave unexpected status %s for MC%s", last_status, mc)
        raise FMCSANotFound(mc)
    except FMCSANotFound:
        raise
    except httpx.HTTPError as e:
        logger.error("FMCSA request failed for MC%s: %s", mc, e)
        raise
    finally:
        if client is None:
            c.close()


def _extract_carrier(payload: dict) -> dict | None:
    content = payload.get("content")
    if not content:
        return None
    if isinstance(content, list):
        if not content:
            return None
        item = content[0]
    else:
        item = content
    if isinstance(item, dict) and "carrier" in item:
        return item["carrier"]
    return item if isinstance(item, dict) else None


def _ineligibility_summary(reasons: list[str]) -> str:
    if "operating_authority_inactive" in reasons:
        return "Operating authority is currently inactive — we won't be able to book you on a load today."
    if "out_of_service" in reasons:
        return "FMCSA shows you're currently out of service — I can't book you today."
    if "no_insurance_on_file" in reasons:
        return "FMCSA doesn't show insurance on file — once that's updated, give us a call back."
    return "FMCSA flagged this carrier as ineligible — we won't be able to book a load today."


def evaluate_carrier(carrier: dict, mc_number: str) -> CarrierVerification:
    legal_name = carrier.get("legalName")
    dba_name = carrier.get("dbaName")

    allowed = (carrier.get("allowedToOperate") or "").upper() == "Y"
    status_code = (carrier.get("statusCode") or "").upper()
    operating_authority_status = "ACTIVE" if (allowed or status_code == "A") else "INACTIVE"

    oos_date = carrier.get("oosDate")
    out_of_service = bool(oos_date)

    insurance_on_file = allowed  # proxy until we wire the insurance endpoint

    # Demo affordance: applied before rule evaluation so details + summary stay consistent.
    # Real FMCSA registry currently shows all our demo MCs as eligible; this env override lets us
    # show the ineligible-rejection branch in the Loom without mutating business logic.
    if mc_number in _demo_ineligible_mcs():
        logger.info("DEMO override: forcing MC%s to ineligible (operating_authority_inactive)", mc_number)
        operating_authority_status = "INACTIVE"

    reasons: list[str] = []
    if operating_authority_status != REQUIRED_STATUS:
        reasons.append("operating_authority_inactive")
    if out_of_service:
        reasons.append("out_of_service")
    if not insurance_on_file:
        reasons.append("no_insurance_on_file")

    eligible = not reasons

    phys_addr = carrier.get("phyAddress") or {}
    physical_state = carrier.get("phyState") or (phys_addr.get("state") if isinstance(phys_addr, dict) else None)

    details = {
        "operating_authority_status": operating_authority_status,
        "out_of_service_flag": out_of_service,
        "insurance_on_file": insurance_on_file,
        "physical_address_state": physical_state,
        "power_units": carrier.get("totalPowerUnits"),
        "drivers": carrier.get("totalDrivers"),
    }

    if eligible:
        summary = "Authority active, no out-of-service flags, insurance on file."
    else:
        summary = _ineligibility_summary(reasons)

    return CarrierVerification(
        mc_number=f"MC{mc_number}",
        eligible=eligible,
        legal_name=legal_name,
        dba_name=dba_name,
        status_summary=summary,
        details=details,
        ineligibility_reasons=reasons or None,
    )


def verify_carrier(
    raw_mc: str,
    conn: sqlite3.Connection,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> CarrierVerification:
    mc = normalize_mc(raw_mc)
    key = api_key or os.getenv("FMCSA_API_KEY")
    if not key:
        raise RuntimeError("FMCSA_API_KEY is not configured")

    payload = _cache_get(conn, mc)
    if payload is None:
        try:
            payload = _fetch_fmcsa(mc, key, client=client)
        except FMCSANotFound:
            payload = None
        if payload is not None:
            _cache_put(conn, mc, payload)

    carrier = _extract_carrier(payload or {})
    if not carrier:
        return CarrierVerification(
            mc_number=f"MC{mc}",
            eligible=False,
            legal_name=None,
            dba_name=None,
            status_summary=(
                "I wasn't able to find that MC number in the FMCSA registry. "
                "Could you double-check it for me?"
            ),
            details=None,
            ineligibility_reasons=["not_found"],
            not_found=True,
        )

    return evaluate_carrier(carrier, mc)
