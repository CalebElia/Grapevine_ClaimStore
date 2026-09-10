"""Propose an award_status for each fiscal reference, and serve the ones needing a person.

WHY ALL 75 SAY 'unknown'. The status of an award is almost never in the sentence that names
the amount. "won $10 million from the Department of Energy" is one claim; "(Funding is
currently on hold)" is a different claim two sentences later, with no dollar figure in it, so
no fiscal_references row was ever created for it. The status and the money are in separate
records and nothing joined them.

THREE GROUPS, AND ONLY ONE NEEDS A HUMAN.

  verb-derived   The claim's OWN words say it: won / secured / awarded / received -> awarded;
                 submitted an application -> applied. This is the same standard a literal
                 mention is held to -- the evidence is inside the quoted sentence, so string
                 matching can decide it.

  reversal       The status is in a NEIGHBOURING claim. Attaching it means judging that "this
                 award" refers to the one named two sentences earlier, which is a proximity
                 judgement, and this store does not let proximity decide anything. Queued.

  silent         54 rows whose claim states an amount and nothing about its fate. 'unknown' is
                 the true answer and they are left alone.

ORDER MATTERS, AND GETTING IT WRONG IS SILENT. Fiscal row 188 contains BOTH an award verb and
a nearby reversal: "won $10 million" ... "(Funding is currently on hold)". A verb rule applied
without checking for a reversal marks it `awarded`, which is precisely the spurious-
transferability failure migration 012 was written to prevent -- a playbook recommending a
grant that is frozen.
"""
from __future__ import annotations

import os
import re

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

_AWARD_VERB = re.compile(r"\b(won|awarded|secured|received|granted)\b", re.I)
_APPLY_VERB = re.compile(r"\b(applied|submitted|seeking|requested|pursuing)\b", re.I)
# Language that says an award's fate changed after it was made.
_REVERSAL = re.compile(r"(terminat|rescind|clawed back|on hold|actively disput|withdraw"
                       r"|revok|laps)", re.I)
# How far a status sentence may sit from the claim it describes and still be offered as a
# candidate. Generous on purpose: this only proposes, and a person reads the page either way.
_NEAR = 900


def verb_status(verbatim: str) -> tuple[str, str] | None:
    """(status, the words that say so), from the claim's own text. None if it is silent."""
    if (m := _AWARD_VERB.search(verbatim or "")):
        return "awarded", m.group(0)
    if (m := _APPLY_VERB.search(verbatim or "")):
        return "applied", m.group(0)
    return None


def classify(cur) -> dict[str, list[dict]]:
    """Split every fiscal reference into verb-derived, reversal-queued, and silent."""
    cur.execute("""
        SELECT f.id, f.amount_low, f.funder_name_text, f.purpose, f.award_status,
               cl.id, cl.verbatim, cl.document_section_id, cl.span_start,
               d.covers_period_end, d.title
          FROM fiscal_references f
          JOIN claims cl ON cl.id = f.claim_id
          JOIN document_sections s ON s.id = cl.document_section_id
          JOIN documents d ON d.id = s.document_id
         ORDER BY f.amount_low DESC NULLS LAST""")
    rows = cur.fetchall()

    out = {"verb": [], "reversal": [], "silent": []}
    for (fid, amt, funder, purpose, status, cid, verbatim, sec, span, period_end,
         doc) in rows:
        # Neighbouring claims that talk about an award changing state.
        cur.execute("""SELECT id, verbatim, span_start FROM claims
                        WHERE document_section_id = %s AND id <> %s
                          AND verbatim ~* %s AND abs(span_start - %s) < %s
                        ORDER BY abs(span_start - %s)""",
                    (sec, cid, _REVERSAL.pattern, span, _NEAR, span))
        near = [{"claim_id": i, "verbatim": v, "distance": abs(sp - span)}
                for i, v, sp in cur.fetchall()]
        rec = {"fiscal_id": fid, "amount": float(amt) if amt is not None else None,
               "funder": funder, "purpose": purpose, "current_status": status,
               "claim_id": cid, "verbatim": verbatim, "document": doc,
               "period_end": str(period_end) if period_end else None,
               "reversal_claims": near}
        v = verb_status(verbatim)
        if near:
            # A REVERSAL OUTRANKS THE VERB IT FOLLOWS. "won $10 million ... funding is on
            # hold" is not an awarded grant.
            rec["proposed"] = None
            rec["verb_would_have_said"] = v[0] if v else None
            out["reversal"].append(rec)
        elif v:
            rec["proposed"], rec["evidence"] = v
            out["verb"].append(rec)
        else:
            out["silent"].append(rec)
    return out


def apply_status(cur, fiscal_id: int, status: str, reason: str | None,
                 as_of: str | None, who: str) -> dict:
    """Set one award_status, with the words that justify it.

    status_change_reason IS THE EVIDENCE, and is required for anything but 'unknown'. A status
    with no quoted sentence behind it is the assertion this table exists to avoid.
    """
    cur.execute("SELECT term FROM vocabulary_terms WHERE vocabulary='award_status'")
    terms = {r[0] for r in cur.fetchall()}
    if status not in terms:
        raise ValueError(f"{status!r} is not an approved award_status: {sorted(terms)}")
    if status != "unknown" and not (reason or "").strip():
        raise ValueError("a status needs status_change_reason: quote the sentence that says so")
    cur.execute("""UPDATE fiscal_references
                      SET award_status=%s, status_change_reason=%s,
                          status_as_of=COALESCE(%s::date, status_as_of)
                    WHERE id=%s""", (status, reason, as_of, fiscal_id))
    return {"fiscal_id": fiscal_id, "status": status, "rows": cur.rowcount, "by": who}
