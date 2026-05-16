"""US state name <-> abbreviation expansion for /loads/search location filters."""
from __future__ import annotations

import re

US_STATES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

# Longest names first so "West Virginia" wins over "Virginia" in the alternation.
_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(US_STATES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_ABBR_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in US_STATES.values()) + r")\b",
    re.IGNORECASE,
)
_NAME_TO_ABBR_CI = {n.casefold(): a for n, a in US_STATES.items()}
_ABBR_TO_NAME_CI = {a.casefold(): n for n, a in US_STATES.items()}


def expand_location_query(q: str) -> list[str]:
    """Return [original, name->abbr swap, abbr->name swap], deduped.

    Carrier may ask for "Texas" while seed stores "Dallas, TX" (and vice versa).
    Two single-pass substitutions produce at most three candidates, which the
    caller ORs together in SQL.
    """
    variants: list[str] = [q]
    seen = {q.casefold()}

    abbr_form = _NAME_RE.sub(lambda m: _NAME_TO_ABBR_CI[m.group(1).casefold()], q)
    if abbr_form.casefold() not in seen:
        variants.append(abbr_form)
        seen.add(abbr_form.casefold())

    name_form = _ABBR_RE.sub(lambda m: _ABBR_TO_NAME_CI[m.group(1).casefold()], q)
    if name_form.casefold() not in seen:
        variants.append(name_form)
        seen.add(name_form.casefold())

    return variants
