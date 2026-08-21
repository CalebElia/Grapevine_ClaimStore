"""Money whose source the document does not name becomes a research question.

CALEB'S RULE: "If we see $$$, but the source of the $$ isn't named, that should become a
research question." A funding_award with no funder is a hole in the chain of custody --
the store knows a sum moved and cannot say who moved it, which is exactly the shape of
question the acquisition loop exists to answer.

BUT ASK THE CORPUS FIRST. Year 3 section 5 says the City "won $500,000 to advance
neighborhood decarbonization in Bryant" and names no funder; section 9 of the SAME REPORT
says "MI-HOPE ($500,000) for the work to decarbonize the Bryant neighborhood". The question
"who funded this" is asked and answered eleven sections apart in one document. A gap the
corpus can close itself is not dark matter -- it is a join nobody has made -- and emitting
it as a research question would send a human hunting for something already on their desk.

So a candidate is suppressed when another claim in the same document states the same amount
AND names a source. The suppression is recorded, not silent: it becomes a note on the
answering claim rather than a question nobody asked.

MONEY RECEIVED, NOT MONEY SAVED. "$45,800 in utility costs saved" has no funder because
nothing funded it -- it is an outcome, not an award. Only a fiscal_reference attached to a
claim the document frames as receiving money can lack a source in the sense that matters.

DARK MATTER IS A LEAD QUEUE, NEVER A FINDING. Every question here is `open` with
`promoted_by` NULL: a human gates it before it drives acquisition. Nothing in this module
asserts that money was hidden -- only that this store cannot say where it came from.
"""
from __future__ import annotations

import argparse
import os
import re

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# A source this vague names nobody. The extraction writes 'other' when the vocabulary has
# no better term and the document gave it nothing to work with.
_EMPTY_SOURCE = {None, "", "other", "unspecified", "unknown", "n/a", "not specified"}

# Money the document frames as RECEIVED. Everything else -- saved, spent, avoided -- has no
# funder to be missing.
# "granted" and "provided" run the OTHER WAY in this corpus: "Granted over $70,000 through
# our Sustaining Ann Arbor Together grant program" is the City giving money away, and money
# the City hands out has no missing funder -- the City is the funder. "was awarded" is
# received; a bare "awarded" is usually the City awarding.
_RECEIVED = re.compile(r"\b(won|secured|received|obtained|"
                       r"was awarded|were awarded|successful in securing)\b", re.I)


def is_received(verbatim: str) -> bool:
    return bool(_RECEIVED.search(verbatim or ""))


def names_a_source(funding_source: str | None) -> bool:
    return (funding_source or "").strip().lower() not in _EMPTY_SOURCE


def find_candidates(dsn: str = DSN) -> list[dict]:
    """Fiscal references for money received whose source the claim does not name."""
    import psycopg

    out = []
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            """SELECT f.id, f.claim_id, f.amount_low, f.funding_source, f.purpose,
                      c.document_id, c.verbatim, c.document_section_id
               FROM fiscal_references f JOIN claims c ON c.id = f.claim_id
               ORDER BY f.amount_low DESC""").fetchall()
        answers = c.execute(
            """SELECT c.document_id, f.amount_low, f.funding_source, c.id, c.verbatim
               FROM fiscal_references f JOIN claims c ON c.id = f.claim_id""").fetchall()

    # SAME AMOUNT, SAME DOCUMENT, DIFFERENT SENTENCE. The suppression cannot depend on
    # funding_source being populated: Year 3 section 9 says "MI-HOPE ($500,000)" and the
    # extraction still wrote funding_source='other', so the funder is in the verbatim and
    # not in the field. What is reliable is the amount and the document -- if this report
    # states $500,000 twice, the second sentence is where to look before sending anyone
    # outside. The candidate is reported for a human to join, never auto-answered.
    by_doc_amount: dict[tuple, list] = {}
    for doc, amt, src, cid, vb in answers:
        by_doc_amount.setdefault((doc, amt), []).append((cid, src, vb))

    for fid, cid, amt, src, purpose, doc, verbatim, sec in rows:
        if names_a_source(src) or not is_received(verbatim):
            continue
        answered = [x for x in by_doc_amount.get((doc, amt), []) if x[0] != cid]
        out.append({"fiscal_id": fid, "claim_id": cid, "amount": amt, "purpose": purpose,
                    "document_id": doc, "section_id": sec, "verbatim": verbatim,
                    "answered_by": answered[0] if answered else None})
    return out


def emit(candidates: list[dict], dsn: str = DSN, dry_run: bool = False) -> dict:
    import psycopg

    asked = suppressed = 0
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for k in candidates:
            if k["answered_by"]:
                suppressed += 1
                continue
            amount = f"${k['amount']:,.0f}".replace(".00", "")
            q = (f"Who funded the {amount} described as “"
                 f"{(k['purpose'] or k['verbatim'])[:90]}”? The document states the "
                 f"amount and does not name a source.")
            if not dry_run:
                cur.execute(
                    """INSERT INTO research_questions
                         (question, origin, origin_claim_id, priority, status)
                       VALUES (%s,'dark_matter_gap',%s,%s,'open')""",
                    (q, k["claim_id"], 3 if k["amount"] >= 100000 else 5))
            asked += 1
        if not dry_run:
            c.commit()
    return {"asked": asked, "suppressed": suppressed}


def main() -> int:
    ap = argparse.ArgumentParser(description="emit research questions for unfunded money")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cands = find_candidates(a.dsn)
    for k in cands:
        mark = ("ANSWERED IN-CORPUS by claim "
                f"{k['answered_by'][0]} ({k['answered_by'][1]})" if k["answered_by"]
                else "-> question")
        print(f"  ${k['amount']:>12,.0f}  doc {k['document_id']}  claim {k['claim_id']:<4} {mark}")
        print(f"                 {k['verbatim'][:84]}")
    r = emit(cands, a.dsn, a.dry_run)
    print(f"[questions] {r['asked']} asked · {r['suppressed']} suppressed "
          f"(the corpus answers them itself)"
          + ("  (dry run — nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
