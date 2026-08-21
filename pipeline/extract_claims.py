"""Extract claims from one section, and store only the ones that can be anchored.

THE LOAD-BEARING RULE, AND THE ONLY ONE THAT MATTERS:

    A claim whose `verbatim` cannot be located character-for-character in its own section
    is DISCARDED, not stored.

This makes fabrication structurally impossible rather than merely discouraged. v1 produced
71 fabricated claims from empty input; `verbatim NOT NULL` was the schema's answer and this
is the pipeline's. It is the same contract as semantic_pass one layer up, and the same
division of labour the conversion rests on: the model proposes, string matching decides.

THE TWO FIELDS ARE NOT THE SAME KIND OF THING, and confusing them is how a store becomes
fiction. `position` is the assertion in Grapevine's words -- the model writes it, it can be
wrong, and being wrong is visible and recoverable. `verbatim` is the document's words, and
it must be FOUND. A claim carries both so a reader can always ask "where does it say that"
and get an offset rather than a paraphrase.

THE SECTION IS THE WHOLE CONTEXT. One call per section, and the model is given that section
and nothing else, so it cannot cite a sentence from elsewhere in the report -- the anchor
would fail anyway, but not sending it means the failure never has to happen.

EVERY CLAIM STARTS AT EVIDENCE GRADE C. C is model-only. B requires corroboration computed
from event_attestations independence, never a count of sources. A is human-verified. Nothing
this module writes has been verified by anyone, so nothing it writes may claim otherwise.

A ZERO REJECT RATE MEANS THE GUARD IS NOT RUNNING. The located-rate is the headline metric,
and a low one is a finding about the extraction contract rather than a failure to work
around.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

PROMPT = """You are extracting claims from one section of a municipal climate action
annual report, published by the City of Ann Arbor's Office of Sustainability and
Innovations. The section text is the ONLY source. Do not use outside knowledge.

A CLAIM is one assertion the document makes about the world: something done, achieved,
funded, committed to, or reported. Skip headings, navigation and anything that asserts
nothing.

For each claim return an object:

  "verbatim"   -- the exact span of section text the claim rests on, COPIED CHARACTER FOR
                  CHARACTER from the text I gave you. Do not fix spelling, spacing,
                  punctuation or capitalisation. Prefer one complete sentence. A verbatim I
                  cannot find in the text is discarded, so copy, never paraphrase.
  "position"   -- the assertion in your own words, one sentence, third person.
  "polarity"   -- one of: support, oppose, informational, mixed, procedural.
                  A report describing its own accomplishments is "informational".
  "modality"   -- one of: asserted, hedged, hypothetical, attributed_to_other.
                  Use "asserted" for a plain statement of something done.
  "asserted_start", "asserted_end" -- ISO dates (YYYY-MM-DD) IF the text states or clearly
                  implies when the thing happened. Omit entirely if it does not.
  "asserted_date_text" -- the words the date came from, if any.
  "quantities" -- a LIST, empty if the claim states no measured amount. Each:
                  {"value_low": number, "unit": "...", "measure": "...", "verbatim": "..."}
                  "unit" is what you count IN (metric tons, households, MW, acres).
                  "measure" is what is being COUNTED, and must be one of:
                    emissions · energy · installed_capacity · savings · cost · participants
                    population · households_served · facilities_treated · units_deployed
                    area_protected · diversion · adoption_rate · goal_target · duration
                    vote_share · other
                  Two numbers sharing a unit but not a measure must never be summed.
  "fiscal"     -- a LIST, empty if the claim states no money. Each:
                  {"amount_low": number, "currency": "USD", "purpose": "...",
                   "funding_source": "...", "funder_name": "...", "verbatim": "..."}
                  These are TWO DIFFERENT THINGS and both matter.
                  "funding_source" is the KIND of money, and must be one of:
                    federal_grant · state_grant · county · philanthropic · utility
                    rate_payer · millage · bond · general_fund · local_match · surplus
                    other
                  "funder_name" is WHO gave it, named exactly as the text names them:
                    "MI-HOPE", "McKnight Foundation", "SEMCOG", "U.S Department of
                    Energy". Omit if the text does not say. Never infer a funder from
                    the kind of money, or a kind from the funder.

ONE CLAIM PER ASSERTION, NOT ONE PER NUMBER. A sentence may state several measured things
at once:

    "AIP completed retrofits in the homes of 17 program participants, helping to reduce
     energy burdens with $45,800 in utility costs saved plus a reduction of 113 metric
     tons of carbon emissions."

That is ONE claim -- one thing the City says it did -- carrying THREE payloads: a quantity
of 17 participants, a fiscal reference of $45,800, and a quantity of 113 metric tons. Do
not emit it as three claims. Two claims must never share the same verbatim.

ONE CLAIM PER LIST ITEM. A lead-in ending in a colon introduces a list, and each item in
that list is its own assertion:

    "OSI submitted multiple grants and was successful in securing:
       MI-HOPE ($500,000) for the work to decarbonize the Bryant neighborhood.
       AmeriCorps program ($229,000) to bring 10 AmeriCorps Members to OSI. ..."

That is thirteen claims, not one -- each names a different grant, a different amount and a
different funder, and a reader asking "who funded Bryant" needs MI-HOPE findable on its
own. Never span a colon lead-in and its items in one verbatim.

VERBATIM MUST BE A COMPLETE SENTENCE. A clause on its own is not a claim: "which would
significantly improve the health and safety of new buildings" has no subject, and a reader
following that span learns nothing. Quote the whole sentence, and let the payload carry the
detail.

Return ONLY a JSON array of these objects. No prose, no markdown fence."""


def _as_list(*candidates) -> list[dict]:
    """Accept a list, a bare object, or nothing. The contract asks for a list; a model that
    returns the single object it used to return is still understood rather than dropped."""
    for c in candidates:
        if isinstance(c, list):
            return [x for x in c if isinstance(x, dict)]
        if isinstance(c, dict):
            return [c]
    return []


@dataclass
class Anchored:
    proposal: dict
    verbatim: str
    span_start: int
    span_end: int
    method: str


@dataclass
class Result:
    anchored: list[Anchored] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    @property
    def located_rate(self) -> float:
        n = len(self.anchored) + len(self.rejected)
        return len(self.anchored) / n if n else 0.0


# ── anchoring ─────────────────────────────────────────────────────────────────────────

_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"',
           "–": "-", "—": "-", " ": " "}


def _fold(text: str) -> tuple[str, list[int]]:
    """Normalised text plus, for each normalised char, its index in the ORIGINAL.

    The index map is the whole point. Normalising both sides and searching is easy;
    reporting a span that indexes the normalised string would store an offset into a
    string nobody has. Every offset this module writes points into the section text
    exactly as the store holds it.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        ch = _QUOTES.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def anchor(verbatim: str, section: str) -> tuple[int, int, str] | None:
    """Locate `verbatim` in `section`, or None. Offsets are into `section` as given.

    Exact first, because an exact hit needs no argument. Then a fold that collapses
    whitespace and unifies the quote and dash characters a model habitually 'corrects' --
    Year 3 is full of typographic apostrophes, and a model that returns a straight one has
    still copied the sentence. Nothing else is forgiven: a paraphrase, a trimmed clause or
    an invented sentence is simply not there.
    """
    v = verbatim.strip()
    if not v:
        return None
    at = section.find(v)
    if at >= 0:
        return at, at + len(v), "exact"

    folded, idx = _fold(section)
    fv, _ = _fold(v)
    fv = fv.strip()
    if not fv:
        return None
    at = folded.find(fv)
    if at < 0:
        return None
    start = idx[at]
    end = idx[at + len(fv) - 1] + 1
    return start, end, "folded"


def anchor_all(proposals: list[dict], section: str) -> Result:
    r = Result()
    for p in proposals:
        v = str(p.get("verbatim") or "")
        hit = anchor(v, section)
        if hit is None:
            r.rejected.append({**p, "_reason": "verbatim not found in the section text"})
            continue
        a, b, how = hit
        # The stored verbatim is the DOCUMENT'S characters, not the model's. If the fold
        # forgave a curly quote, the store keeps the curly quote.
        r.anchored.append(Anchored(p, section[a:b], a, b, how))
    return r


# ── the call ──────────────────────────────────────────────────────────────────────────

def propose(section_text: str, heading: str | None,
            deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> list[dict]:
    from openai import OpenAI

    from pipeline import config

    client = OpenAI(base_url=config.get("OPENAI_BASE_URL"),
                    api_key=config.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=config.get(deployment_env),
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": section_text}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return out if isinstance(out, list) else []


# ── storage ───────────────────────────────────────────────────────────────────────────

_POLARITY = {"support", "oppose", "informational", "mixed", "procedural"}
_MODALITY = {"asserted", "hedged", "hypothetical", "attributed_to_other"}


def _funder_org(cur, name: str | None) -> int | None:
    """The named funder, resolved to an org, or None.

    WHO gave the money and WHAT KIND of money it is are different questions with different
    homes: funding_source is a vocabulary of categories (federal_grant, philanthropic,
    millage), awarding_org_id is a referent. The first extraction filled neither -- it
    wrote 'other' sixteen times because nothing told it the vocabulary existed, and put
    "MI-HOPE" only in the verbatim, where no query can reach it.

    Resolved by exact name, never fuzzily. A funder attributed to the wrong body is a
    citation saying something false about who paid, which is worse than a NULL -- the
    research question a NULL raises is the correct outcome when we do not know.
    """
    n = (name or "").strip()
    if not n:
        return None
    cur.execute("SELECT id FROM orgs WHERE lower(name) = lower(%s)", (n,))
    if (row := cur.fetchone()):
        return row[0]
    # THE WIKI'S ALIASES, WHICH IS WHERE THE JUDGEMENT ALREADY LIVES. A document writes
    # "SEMCOG" where the registry holds "Southeast Michigan Council of Governments", and
    # resolving that is not fuzzy matching -- somebody curated the pair. Exact match on an
    # alias, never a similarity score: a funder attributed to the wrong body says something
    # false about who paid, and the research question a NULL raises is the right outcome
    # when we do not know.
    from pipeline.resolve_orgs import read_aliases, read_funder_aliases
    # The corpus's own spelling first -- "SEMCOG", "U.S Department of Energy" -- then the
    # wiki's alias table. Both are pairs a person decided are the same body; neither is a
    # similarity score.
    local = read_funder_aliases().get(n.lower())
    if local:
        cur.execute("SELECT id FROM orgs WHERE lower(name) = lower(%s)", (local,))
        if (row := cur.fetchone()):
            return row[0]
        n = local
    aliases, _ = read_aliases()
    slug = aliases.get(n.lower())
    if not slug:
        return None
    cur.execute("SELECT id FROM orgs WHERE notes LIKE %s", (f"%wiki/actors/{slug}.md",))
    row = cur.fetchone()
    return row[0] if row else None


def store(res: Result, section_id: int, document_id: int, content_hash: str,
          section_char_start: int, extracted_by: str, dsn: str = DSN,
          period: tuple | None = None) -> dict:
    """Write anchored claims. Spans are absolute offsets into the document's canonical text.

    WHY THE SPAN IS SHIFTED. `anchor` works within the section, because that is what the
    model was shown. claims.span_start indexes the DOCUMENT's canonical text, which is what
    documents.content_hash covers. Storing the section-relative offset would produce a span
    that round-trips against a string the store does not hold -- the same failure as
    indexing the markdown file, one level down.
    """
    import psycopg

    counts = {"claims": 0, "quantities": 0, "fiscal_references": 0}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for a in res.anchored:
            p = a.proposal
            pol = p.get("polarity") if p.get("polarity") in _POLARITY else "informational"
            mod = p.get("modality") if p.get("modality") in _MODALITY else "asserted"
            cur.execute(
                """INSERT INTO claims
                     (document_id, document_section_id, position, polarity, modality,
                      source_type, verbatim, span_start, span_end, span_unit,
                      source_content_hash, asserted_start, asserted_end,
                      asserted_date_text, evidence_grade, extracted_by, curation_state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'char',%s,%s,%s,%s,'C',%s,'proposed')
                   RETURNING id""",
                (document_id, section_id, (p.get("position") or a.verbatim)[:2000], pol,
                 mod, "annual_report", a.verbatim,
                 section_char_start + a.span_start, section_char_start + a.span_end,
                 content_hash, p.get("asserted_start") or None,
                 p.get("asserted_end") or None, p.get("asserted_date_text"), extracted_by))
            claim_id = cur.fetchone()[0]
            # A CLAIM WITH NO DATE IS INVISIBLE FOREVER. Timeline queries compare
            # intervals, so a NULL start cannot participate in one -- not wrong, absent.
            # Every sentence here IS dated: the report covers a stated period, and a claim
            # naming no date of its own happened somewhere inside it. Precision
            # 'reporting_period' says exactly that and does not pretend to more.
            if not p.get("asserted_start") and period and period[0]:
                cur.execute(
                    "UPDATE claims SET asserted_start=%s, asserted_end=%s, "
                    "asserted_precision='reporting_period', "
                    "asserted_date_text=coalesce(asserted_date_text,%s) WHERE id=%s",
                    (period[0], period[1],
                     f"inherited from the document's coverage period ({period[2]})",
                     claim_id))
            counts["claims"] += 1

            for q in _as_list(p.get("quantities"), p.get("quantity")):
                if q.get("value_low") is None:
                    continue
                cur.execute(
                    """INSERT INTO quantities
                         (claim_id, value_low, unit, measure, source_type, verbatim)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (claim_id, q.get("value_low"), q.get("unit"), q.get("measure"),
                     "annual_report", (q.get("verbatim") or a.verbatim)[:2000]))
                counts["quantities"] += 1

            for f in _as_list(p.get("fiscal"), p.get("fiscal_references")):
                if f.get("amount_low") is None:
                    continue
                cur.execute(
                    """INSERT INTO fiscal_references
                         (claim_id, amount_low, currency, purpose, funding_source,
                          source_type, verbatim, awarding_org_id, funder_name_text)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (claim_id, f.get("amount_low"), f.get("currency") or "USD",
                     f.get("purpose"), f.get("funding_source"), "annual_report",
                     (f.get("verbatim") or a.verbatim)[:2000],
                     _funder_org(cur, f.get("funder_name")),
                     # KEPT WHETHER OR NOT IT RESOLVED. The name is evidence; the id is a
                     # join. Storing only the id threw away the document's own words every
                     # time the registry was short a row, and a discarded name becomes a
                     # research question asking who funded something the document names.
                     (f.get("funder_name") or "").strip() or None))
                counts["fiscal_references"] += 1
        c.commit()
    return counts


def run(section_id: int, dsn: str = DSN, dry_run: bool = False,
        rejects_path: Path | None = None) -> dict:
    import psycopg

    from pipeline.canonical import build

    with psycopg.connect(dsn) as c:
        row = c.execute(
            """SELECT s.document_id, s.char_start, s.char_end, s.heading,
                      s.extraction_tier, s.parse_confidence, d.markdown_path,
                      d.content_hash, d.covers_period_start, d.covers_period_end,
                      d.covers_period_source
               FROM document_sections s JOIN documents d ON d.id = s.document_id
               WHERE s.id = %s""", (section_id,)).fetchone()
    if not row:
        raise SystemExit(f"[extract] no section {section_id}")
    doc_id, a, b, heading, tier, conf, md_path, doc_hash, ps, pe, psrc = row

    # FAIL CLOSED. The whole point of section_audit is that this refuses.
    if conf != "clean":
        raise SystemExit(f"[extract] section {section_id} is '{conf}', not 'clean'. "
                         f"Extraction reads only audited sections.")
    if tier == "C":
        raise SystemExit(f"[extract] section {section_id} is tier C — nothing to extract.")

    canon = build(Path(md_path).read_text())
    if canon.content_hash != doc_hash:
        raise SystemExit("[extract] the conversion changed since ingest; re-ingest first.")
    section = canon.text[a:b]
    # The provenance travels: Years 1 and 2 have a period a human ESTIMATED, and a claim
    # dated from it inherits that, not the certainty of a stated one.
    period = (ps, pe, psrc) if ps else None

    t0 = time.time()
    proposals = propose(section, heading)
    res = anchor_all(proposals, section)
    # TWO CLAIMS ON ONE SPAN MEANS THE CONTRACT WAS NOT FOLLOWED. Not rejected -- the
    # verbatim is real -- but it is the exact defect the first hand-read found, so it is
    # counted and printed rather than left to be noticed in SQL later.
    seen: dict[tuple[int, int], int] = {}
    for x in res.anchored:
        seen[(x.span_start, x.span_end)] = seen.get((x.span_start, x.span_end), 0) + 1
    shared = {k: n for k, n in seen.items() if n > 1}
    if rejects_path and res.rejected:
        rejects_path.parent.mkdir(parents=True, exist_ok=True)
        rejects_path.write_text(json.dumps(res.rejected, indent=2))

    out = {"section_id": section_id, "document_id": doc_id, "heading": heading,
           "proposed": len(proposals), "anchored": len(res.anchored),
           "rejected": len(res.rejected), "located_rate": res.located_rate,
           "seconds": round(time.time() - t0, 1), "result": res,
           "exact": sum(1 for x in res.anchored if x.method == "exact"),
           "folded": sum(1 for x in res.anchored if x.method == "folded"),
           "distinct_spans": len(seen), "shared_spans": len(shared),
           "money_in_text": len(re.findall(r"\$[\d,]+", section)),
           "fiscal_proposed": sum(len(_as_list(x.proposal.get("fiscal"),
                                               x.proposal.get("fiscal_references")))
                                  for x in res.anchored)}
    if not dry_run:
        out["stored"] = store(res, section_id, doc_id, doc_hash, a,
                              "extract_claims/gpt", dsn, period)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="extract claims from one section")
    ap.add_argument("--section-id", type=int, required=True)
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--rejects", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    r = run(a.section_id, a.dsn, a.dry_run,
            Path(a.rejects) if a.rejects else None)
    print(f"[extract] section {r['section_id']} — {r['heading'][:56]}")
    print(f"  proposed {r['proposed']} · anchored {r['anchored']} "
          f"({r['exact']} exact, {r['folded']} folded) · rejected {r['rejected']} "
          f"· located-rate {r['located_rate']:.0%} · {r['seconds']}s")
    # A PAYLOAD TYPE THAT GOES TO ZERO WHILE THE TEXT IS FULL OF IT is what a silent
    # storage bug looks like from the outside: the run reports success and the column is
    # simply empty. Counting what the SECTION contains is the only way to notice.
    if r["money_in_text"] and not r["fiscal_proposed"]:
        print(f"  ** the section contains {r['money_in_text']} currency figure(s) and no "
              f"claim carries a fiscal payload — check the contract and the storage path **")
    if r["shared_spans"]:
        print(f"  ** {r['shared_spans']} span(s) carry more than one claim — "
              f"{r['anchored']} claims on {r['distinct_spans']} spans. One claim per "
              f"assertion; a sentence with several numbers takes several payloads. **")
    if "stored" in r:
        print("  stored " + " · ".join(f"{k} {v}" for k, v in r["stored"].items()))
    for x in r["result"].rejected:
        print(f"  REJECTED: {str(x.get('verbatim'))[:88]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
