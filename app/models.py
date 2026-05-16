import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.fmcsa import normalize_mc


class LoadResult(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: str
    delivery_datetime: str
    equipment_type: str
    loadboard_rate: float
    rate_per_mile: float
    miles: int
    weight: int | None = None
    commodity_type: str | None = None
    num_of_pieces: int | None = None
    dimensions: str | None = None
    notes: str | None = None
    pitch_summary: str


class LoadSearchResponse(BaseModel):
    count: int
    results: list[LoadResult]
    message: str | None = None


class CarrierVerifyDetails(BaseModel):
    operating_authority_status: str | None = None
    out_of_service_flag: bool | None = None
    insurance_on_file: bool | None = None
    physical_address_state: str | None = None
    power_units: int | None = None
    drivers: int | None = None


class CarrierVerifyResponse(BaseModel):
    mc_number: str
    eligible: bool
    legal_name: str | None = None
    dba_name: str | None = None
    status_summary: str
    details: CarrierVerifyDetails | None = None
    ineligibility_reasons: list[str] | None = None


class NegotiationRequest(BaseModel):
    load_id: str
    carrier_offer: float = Field(gt=0)
    round: int = Field(ge=1)
    our_last_offer: float | None = Field(default=None, gt=0)

    # HappyRobot's Preserve-data-types toggle can serialize unset numeric fields
    # as "" and stringify other numerics if anything in the body breaks inference.
    # Accept str | int | float on the wire; reject only truly garbage values.
    @field_validator("our_last_offer", mode="before")
    @classmethod
    def _v_our_last_offer(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            return float(s)
        return v

    @field_validator("carrier_offer", mode="before")
    @classmethod
    def _v_carrier_offer(cls, v: Any) -> Any:
        if isinstance(v, str):
            return float(v.strip())
        return v

    @field_validator("round", mode="before")
    @classmethod
    def _v_round(cls, v: Any) -> Any:
        if isinstance(v, str):
            return int(v.strip())
        return v


class NegotiationResponse(BaseModel):
    decision: str  # accept | counter | reject
    round: int
    max_rounds: int
    rounds_remaining: int
    reason: str
    agent_response_hint: str
    agreed_price: float | None = None
    our_counter: float | None = None


class Outcome(StrEnum):
    booked = "booked"
    declined_price = "declined_price"
    declined_no_match = "declined_no_match"
    ineligible_carrier = "ineligible_carrier"
    abandoned = "abandoned"
    escalated = "escalated"
    other = "other"


class Sentiment(StrEnum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    frustrated = "frustrated"


_OUTCOME_ALIASES = {
    "booking_confirmed": "booked",
    "booking_made": "booked",
    "no_match": "declined_no_match",
    "no_load_match": "declined_no_match",
    "price_declined": "declined_price",
    "declined": "declined_price",
    "ineligible": "ineligible_carrier",
    "carrier_ineligible": "ineligible_carrier",
    "dropped": "abandoned",
    "hung_up": "abandoned",
    "transferred": "escalated",
    "transfer": "escalated",
}

_SENTIMENT_ALIASES = {
    "happy": "positive",
    "pleased": "positive",
    "okay": "neutral",
    "ok": "neutral",
    "unhappy": "negative",
    "angry": "frustrated",
    "annoyed": "frustrated",
}


def _normalize_outcome(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    # Empty/null-ish input means the Classify node produced nothing — that is
    # not the same as "other" (which means "we saw a value we don't recognize").
    if not v or v in ("null", "none"):
        return None
    if v in {o.value for o in Outcome}:
        return v
    if v in _OUTCOME_ALIASES:
        return _OUTCOME_ALIASES[v]
    return "other"


def _normalize_sentiment(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v or v in ("null", "none"):
        return None
    if v in {s.value for s in Sentiment}:
        return v
    if v in _SENTIMENT_ALIASES:
        return _SENTIMENT_ALIASES[v]
    return "neutral"


class CallEvent(BaseModel):
    call_id: str
    # Mid-call writes from HappyRobot don't expose a call-start timestamp in the
    # variable picker; we default so partial writes succeed. Post-call enrichment
    # overwrites with the accurate value.
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    mc_number: str | None = None
    carrier_legal_name: str | None = None
    carrier_eligible: bool | None = None
    load_id: str | None = None
    loadboard_rate: float | None = None
    final_price: float | None = None
    # Carrier's last counter-offer (distinct from final_price, the agreed price).
    # Pydantic v2 coerces "2700" → 2700.0 for float without a custom validator.
    final_carrier_offer: float | None = None
    negotiation_rounds: int | None = None
    outcome: str | None = None
    # Free-form rationale strings from HappyRobot's Classify node. No enum/
    # normalization — we store and serve verbatim for review on the dashboard.
    outcome_reasoning: str | None = None
    # Structured reason codes mirrored from /carriers/verify (e.g.
    # ["operating_authority_inactive", "out_of_service"]). Stored as JSON text
    # in SQLite; the router does the encode/decode at the boundary.
    ineligibility_reasons: list[str] | None = None
    sentiment: str | None = None
    sentiment_reasoning: str | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def _v_outcome(cls, v: Any) -> str | None:
        return _normalize_outcome(v)

    @field_validator("sentiment", mode="before")
    @classmethod
    def _v_sentiment(cls, v: Any) -> str | None:
        return _normalize_sentiment(v)

    # HappyRobot's "Preserve data types" toggle infers `123456` as an int, not a
    # string. Coerce, then run mc_number through normalize_mc so "MC123456",
    # 123456, and "  00123456 " all land as "123456" in storage. Floats are
    # truncated to int first so `normalize_mc` doesn't fold the fractional part
    # into the digit run (123456.0 must become "123456", not "1234560").
    @field_validator("mc_number", mode="before")
    @classmethod
    def _v_mc_number(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, float):
            v = int(v)
        s = str(v).strip()
        # normalize_mc("") returns "0", which would masquerade as a real MC.
        # Empty-ish inputs (HappyRobot's Preserve-data-types serializes
        # unset fields as "") must coerce to None.
        if not s or s.lower() in ("null", "none"):
            return None
        return normalize_mc(s)

    @field_validator("load_id", mode="before")
    @classmethod
    def _v_load_id(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, float):
            v = int(v)
        s = str(v).strip()
        # Empty load_id would hit the FK constraint loads(load_id) on INSERT.
        if not s or s.lower() in ("null", "none"):
            return None
        return s

    # HappyRobot's Preserve-data-types serializes unset numeric/datetime fields
    # as "" rather than null. Coerce empty-ish strings to None on every nullable
    # numeric/datetime field — Postel's law, same as NegotiationRequest.
    @field_validator("loadboard_rate", "final_price", "final_carrier_offer", mode="before")
    @classmethod
    def _v_optional_float(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return float(s)
        return v

    @field_validator("negotiation_rounds", "duration_seconds", mode="before")
    @classmethod
    def _v_optional_int(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return int(float(s))  # tolerate "2.0" → 2
        return v

    # HappyRobot's Preserve-data-types serializes an unset bool as "" rather
    # than null — same shape as the numeric/datetime fix. Unrecognized strings
    # fall through to None so we never 422 on a quirky transcript value.
    @field_validator("carrier_eligible", "transfer_attempted", mode="before")
    @classmethod
    def _v_optional_bool(cls, v: Any) -> Any:
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if not s or s in ("null", "none"):
                return None
            if s in ("true", "1", "yes", "y", "t"):
                return True
            if s in ("false", "0", "no", "n", "f"):
                return False
            return None
        return v

    # Empty strings on optional free-form string fields are meaningless and
    # would clutter the dashboard / waste storage. Normalize to None.
    @field_validator(
        "carrier_legal_name",
        "summary",
        "outcome_reasoning",
        "sentiment_reasoning",
        "transcript_url",
        "recording_url",
        mode="before",
    )
    @classmethod
    def _v_optional_str(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return s
        return v

    @field_validator("ended_at", mode="before")
    @classmethod
    def _v_optional_datetime(cls, v: Any) -> Any:
        # Treat the wire value as authoritative only if it actually looks like
        # a timestamp. ints/floats would otherwise be parsed as Unix-epoch
        # seconds (e.g. 1 → 1970-01-01T00:00:01); we'd rather drop the field
        # and let the read path derive it from started_at + duration_seconds.
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return s
        return None

    @field_validator("started_at", mode="before")
    @classmethod
    def _v_started_at(cls, v: Any) -> Any:
        # Same boundary-type defense as ended_at, but started_at is non-nullable;
        # junk inputs fall back to the default-factory value instead of None so
        # Pydantic doesn't 422 the whole event.
        if v is None:
            return datetime.now(UTC)
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return datetime.now(UTC)
            return s
        return datetime.now(UTC)

    # Accept list (canonical), JSON-encoded string (HappyRobot's Preserve-data-
    # types may stringify arrays), empty string (treat as no reasons), or a
    # comma-separated string (last-resort fallback for ad-hoc clients).
    @field_validator("ineligibility_reasons", mode="before")
    @classmethod
    def _v_ineligibility_reasons(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if s.startswith("["):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return parsed
                except (ValueError, TypeError):
                    pass
            return [part.strip() for part in s.split(",") if part.strip()]
        return v
    transfer_attempted: bool | None = None
    transcript_url: str | None = None
    recording_url: str | None = None
    summary: str | None = None

    # Per-node LLM usage from HappyRobot's post-call AI nodes. Trusted in-band
    # source — no coercion validators needed (Pydantic v2 handles int|str → int).
    extract_model: str | None = None
    extract_input_tokens: int | None = None
    extract_output_tokens: int | None = None
    extract_reasoning_tokens: int | None = None
    extract_cached_input_tokens: int | None = None
    outcome_model: str | None = None
    outcome_input_tokens: int | None = None
    outcome_output_tokens: int | None = None
    outcome_reasoning_tokens: int | None = None
    outcome_cached_input_tokens: int | None = None
    sentiment_model: str | None = None
    sentiment_input_tokens: int | None = None
    sentiment_output_tokens: int | None = None
    sentiment_reasoning_tokens: int | None = None
    sentiment_cached_input_tokens: int | None = None


class CallEventStored(BaseModel):
    call_id: str
    stored: bool
    already_exists: bool = False


class AttentionItem(BaseModel):
    type: str
    severity: str  # info | warning | critical
    title: str
    description: str
    related_call_ids: list[str] = Field(default_factory=list)
    suggested_action: str


class MetricsToday(BaseModel):
    date: str
    loads_booked: int
    revenue: float
    avg_margin_vs_loadboard_pct: float | None
    win_rate: float | None
    avg_rounds: float | None
    sentiment_mix: dict[str, int]
    est_ai_cost_today_usd: float
    est_ai_cost_voice_today_usd: float
    est_ai_cost_enrichment_today_usd: float


class TimeseriesPoint(BaseModel):
    date: str
    loads_booked: int
    revenue: float
    avg_margin_vs_loadboard_pct: float | None
    win_rate: float | None


class TimeseriesResponse(BaseModel):
    days: int
    points: list[TimeseriesPoint]


class CallListItem(BaseModel):
    call_id: str
    started_at: str
    mc_number: str | None
    carrier_legal_name: str | None
    load_id: str | None
    loadboard_rate: float | None = None
    final_price: float | None
    negotiation_rounds: int | None = None
    outcome: str | None
    sentiment: str | None


class CallListResponse(BaseModel):
    count: int
    results: list[CallListItem]


class CallDetail(BaseModel):
    call_id: str
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    mc_number: str | None
    carrier_legal_name: str | None
    carrier_eligible: bool | None
    load_id: str | None
    loadboard_rate: float | None
    final_price: float | None
    final_carrier_offer: float | None
    negotiation_rounds: int | None
    outcome: str | None
    outcome_reasoning: str | None
    ineligibility_reasons: list[str] | None
    sentiment: str | None
    sentiment_reasoning: str | None
    transfer_attempted: bool | None
    transcript_url: str | None
    recording_url: str | None
    summary: str | None
    raw_payload: dict[str, Any] | None
    received_at: str
    extract_model: str | None = None
    extract_input_tokens: int | None = None
    extract_output_tokens: int | None = None
    extract_reasoning_tokens: int | None = None
    extract_cached_input_tokens: int | None = None
    outcome_model: str | None = None
    outcome_input_tokens: int | None = None
    outcome_output_tokens: int | None = None
    outcome_reasoning_tokens: int | None = None
    outcome_cached_input_tokens: int | None = None
    sentiment_model: str | None = None
    sentiment_input_tokens: int | None = None
    sentiment_output_tokens: int | None = None
    sentiment_reasoning_tokens: int | None = None
    sentiment_cached_input_tokens: int | None = None
