"""Which way the money moved, from the claim's own words.

WHY THIS EXISTS. As extracted, the largest fiscal_reference in this corpus is $1,000,000,000
-- "the City has saved rate payers more than $1,000,000,000 through testimony and advocacy".
It is not an award. Summed alongside grants received it turns a $110M funding picture into a
$1.1B one, and nothing errors. Awards, savings, disbursements and authorisations are
different facts about different money.

IT ABSTAINS RATHER THAN GUESSES. Every phrase it does not recognise is `unknown`. Defaulting
to the commonest case would re-create the bug somewhere quieter: a mis-signed row still sums,
still looks healthy, and is wrong. An `unknown` is visible and countable.

ORDER IS EVIDENCE, NOT PREFERENCE. Savings are tested first because "won recognition for $2
million in energy savings" opens on an award verb and is a savings claim -- what the money
DID governs, not the first verb in the sentence. The received/disbursed split follows
emit_questions: "granted" and "provided" run the OTHER way in this corpus, describing the
City handing money out, while "was granted" is the City receiving.
"""
from __future__ import annotations

import re

DIRECTIONS = ("received", "disbursed", "saved", "spent", "authorized", "unknown")

# Tested in this order. The first match wins, and the order encodes which evidence is
# stronger rather than which case is commoner.
_RULES: list[tuple[str, re.Pattern]] = [
    # sav(ed|ing|ings): the corpus writes "saving residents over $X" as often as "saved",
    # and the first pattern here matched only the past tense.
    ("saved", re.compile(r"\b(sav(?:ed|ing|ings)|cost[- ]avoidance|avoided cost)\b", re.I)),
    # A passive auxiliary makes an award-verb point INWARD, whichever verb it is.
    ("received", re.compile(r"\b(?:was|were|been)\s+(awarded|granted|given|allocated)\b", re.I)),
    ("received", re.compile(r"\b(won|secur(?:ed|e|ing)|receiv(?:ed|ing)|obtained)\b", re.I)),
    # "$4,500,000 FROM the American Rescue Plan Act" carries no verb at all. `from` is the
    # preposition of receipt, and it is tested after `saved` so that "savings from the
    # retrofit programme" stays a savings claim.
    ("received", re.compile(r"\$[\d,.]+\s*(?:million|billion)?\s+from\b", re.I)),
    ("disbursed", re.compile(r"\b(granted|provided|distributed|disbursed|awarded)\b.{0,40}"
                             r"\b(to|through|for)\b", re.I)),
    ("disbursed", re.compile(r"\bin grants to\b", re.I)),
    ("authorized", re.compile(r"\b(authorized|authorised|appropriated|committed)\b", re.I)),
    ("spent", re.compile(r"\b(spent|invested|paid out)\b", re.I)),
]


def classify(verbatim: str | None) -> str:
    """One of DIRECTIONS. `unknown` whenever the words do not say."""
    t = (verbatim or "").strip()
    if not t:
        return "unknown"
    for direction, rx in _RULES:
        if rx.search(t):
            return direction
    return "unknown"


def normalise_amount(amount, verbatim: str | None):
    """A zero the document never stated is NULL, not zero.

    Two references arrived carrying amount_low = 0.00 for sentences that name no figure --
    "Won a planning grant from the U.S. Department of Energy to design a district
    geothermal". Zero is a legitimate amount; "not stated" is not zero, and recording it as
    zero manufactures a $0 award that sums cleanly and reads as real.

    The reference itself is still worth keeping: it names a funder and a purpose. Only the
    amount is unknown.
    """
    if amount is None:
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    if value != 0:
        return amount
    return amount if re.search(r"[$][\s]*0(?![.,]?\d*[1-9])", verbatim or "") else None
