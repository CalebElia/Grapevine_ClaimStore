"""Split a free-text unit into a comparable DIMENSION and the thing that was counted.

WHY, MEASURED. 160 of 218 pending vocabulary proposals were quantity_unit values, because the
extraction put the counted noun in `unit` -- "Direct Current Fast Chargers (DCFCs)", "pilot
battery back-up Solarize bulk buys". The prompt invited it: it said `"unit" is what you count
IN (metric tons, households, MW, acres)`, and `households` is a noun. Real units appeared
whenever the text offered one, so the field degraded rather than failed, which is why nobody
noticed until the proposal queue stopped being readable.

THE DETAIL IS THE VALUABLE PART AND IS NOT DISCARDED. "18 air quality monitors" is a better
fact than "18 things". But `unit='air quality monitors'` will never aggregate with
`unit='AQMesh monitors'`, and a controlled vocabulary with 160 pending members is not
controlled. So the two facts are separated rather than traded off:

    unit        the DIMENSION. Small, closed, comparable. This is what you GROUP BY.
    unit_basis  WHAT WAS COUNTED. Verbatim, uncontrolled, never normalised. This is what
                makes the row worth reading.

That column already existed for exactly this -- "what a % or count is OF" -- and had been
populated zero times in 388 rows. The same shape as funder_name_text beside awarding_org_id:
one field to join on, one field that keeps what the document actually said.

WHAT THE UNIT MUST NOT DO IS ASSERT MORE THAN THE TEXT. "tons of material" diverted from
landfill is not CO2e, so bare tons stay `metric_tons` and only explicit carbon phrasing
becomes `metric_tons_co2e`. `measure` is what separates emissions from diversion.
"""
from __future__ import annotations

import re

# The closed set. Deliberately small: every member is something two rows could sensibly be
# summed or compared within.
UNITS = ("count", "percent", "MW", "kW", "MWh", "kWh", "metric_tons_co2e", "metric_tons",
         "USD", "miles", "square_feet", "acres", "years", "days", "ratio", "other")

# Exact spellings seen in the corpus, mapped to the dimension they mean.
_EXACT = {
    "%": "percent", "percent": "percent", "percentage": "percent",
    "mw": "MW", "megawatts": "MW", "megawatt": "MW",
    "kw": "kW", "kilowatts": "kW",
    "mwh": "MWh", "kwh": "kWh",
    "usd": "USD", "dollars": "USD", "$": "USD",
    "mile": "miles", "miles": "miles",
    "square feet": "square_feet", "sq ft": "square_feet", "square foot": "square_feet",
    "acre": "acres", "acres": "acres",
    "year": "years", "years": "years", "annual": "years", "times per year": "years",
    "day": "days", "days": "days", "week": "days", "weeks": "days",
    "ratio": "ratio",
}

# CO2-equivalent mass, however the report spells it -- including "metrics tons", which is the
# document's own typo and is reproduced faithfully in the text.
_CO2E = re.compile(r"\bmetrics?\s+tons?\s+of\s+(co2|carbon dioxide)\b", re.I)
# Mass that is NOT asserted to be carbon: "tons of material", "metric tons".
_TONS = re.compile(r"\b(metric\s+)?tons?\b(?:\s+of\s+(?P<of>.+))?$", re.I)


def split_unit(raw: str | None) -> tuple[str, str | None]:
    """(dimension, what was counted). The second is None when the unit is a real unit."""
    t = (raw or "").strip()
    if not t:
        # `unit` is NOT NULL, so something must be stored. `other` is visible; `count` would
        # silently assert that the row counts things.
        return "other", None

    key = t.lower().strip(". ")
    if key in _EXACT:
        return _EXACT[key], None
    if _CO2E.search(t):
        return "metric_tons_co2e", None
    m = _TONS.match(key)
    if m:
        return "metric_tons", (m.group("of") or None)

    # Anything else is a noun: the row counts those things. The phrase is kept exactly as
    # written, because it is the most informative thing about the row.
    return "count", t
