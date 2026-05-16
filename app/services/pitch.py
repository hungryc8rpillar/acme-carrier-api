from datetime import datetime

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def _format_weight(weight: int | None) -> str | None:
    if weight is None:
        return None
    if weight >= 1000:
        return f"{weight // 1000}k pounds"
    return f"{weight} pounds"


def pitch_summary(load: dict) -> str:
    origin = load["origin"]
    destination = load["destination"]
    miles = load["miles"]
    equipment = load["equipment_type"].lower()
    rate = load["loadboard_rate"]
    rpm = round(rate / miles, 2) if miles else 0.0

    try:
        pickup_day = _DAYS[datetime.fromisoformat(load["pickup_datetime"].replace("Z", "+00:00")).weekday()]
    except (ValueError, KeyError):
        pickup_day = "soon"

    weight_phrase = _format_weight(load.get("weight"))
    commodity = (load.get("commodity_type") or "").strip()

    parts = [
        f"{origin} to {destination}",
        f"picks up {pickup_day}",
        f"{miles} miles",
        equipment,
    ]
    if weight_phrase and commodity:
        parts.append(f"{weight_phrase} of {commodity.lower()}")
    elif weight_phrase:
        parts.append(weight_phrase)
    elif commodity:
        parts.append(commodity.lower())

    parts.append(f"posted at {_format_money(rate)}")
    parts.append(f"that's ${rpm:.2f} a mile")
    return ", ".join(parts[:-1]) + f" — {parts[-1]}."
