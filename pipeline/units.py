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
         "USD", "miles", "square_feet", "acres", "years", "months", "weeks", "days", "hours",
         "ratio", "other")

# Exact spellings seen in the corpus, mapped to the dimension they mean.
# Every member of UNITS, keyed by its own lowercase spelling. Built from UNITS so the two
# can never drift apart.
_CANON = {u.lower(): u for u in UNITS}

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
    "month": "months", "months": "months",
    # WEEKS ARE NOT DAYS. This mapped week -> days while leaving value_low untouched, so
    # "48 weeks" was stored as 48 DAYS -- a silent seven-fold error in the one direction
    # nothing downstream could detect. A dimension may be renamed; it may never be
    # converted without converting the number with it, so each keeps its own.
    "week": "weeks", "weeks": "weeks",
    "day": "days", "days": "days",
    "hour": "hours", "hours": "hours",
    "ratio": "ratio",
}

# CO2-equivalent mass, however the report spells it -- including "metrics tons", which is the
# document's own typo and is reproduced faithfully in the text.
# CARBON MASS, in the spellings the corpus actually uses. The previous pattern required the
# word "of" and ended on \b after "co2", so it matched NONE of "metric tons CO2e", "MT CO2e"
# or "metric tons carbon dioxide equivalent" -- the three ways this document writes its
# central unit -- and "metric tons of CO2e" fell through to _TONS, storing carbon tonnage as
# plain mass. The "of" is optional and the trailing e of CO2e is allowed for.
_CO2E = re.compile(
    # No \b after the abbreviation: the corpus writes "MTCO2e" with no separator, and
    # the carbon group that follows is specific enough to carry the match on its own.
    r"\b(?:m(?:etrics?)?\s*t(?:ons?)?|mt)[\s-]*(?:of\s+)?"
    r"(?:co\s*2\s*-?\s*e?\b|carbon\s+dioxide(?:\s+equivalents?)?\b)", re.I)
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
    # A CANONICAL UNIT MUST SURVIVE BEING FED BACK IN. Five of the sixteen did not:
    # split_unit("metric_tons_co2e") returned ("count", "metric_tons_co2e"), because the
    # underscored spellings this module EMITS were absent from the spellings it ACCEPTS. The
    # extraction model reads the vocabulary and often answers with the exact canonical name,
    # so the best-behaved responses were the ones demoted to a dimensionless count -- eight
    # emissions rows carrying real tonnage among them.
    if key in _CANON:
        return _CANON[key], None
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
