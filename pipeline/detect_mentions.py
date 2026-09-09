"""Find where a claim NAMES an initiative, and prove it with a span.

THE PROBLEM THIS SOLVES AND THE ONE IT DOES NOT. resolve_actions links a SECTION to the
initiative its heading names -- "Implement Community Choice Aggregation" -- which works
because a heading is a declaration. An annual report has no such headings: its sections are
strategies, and the initiatives appear inside narrative sentences. So the CAP knows about
Geothermal Districts and the reports know about the Bryant geothermal project, and until
something reads the prose the two never meet.

A MENTION IS NOT A SUBJECT. claims.subject_id says what a claim is ABOUT and already holds
the strategy its section declared. This records that the claim's text NAMES an initiative,
which is weaker, many-to-many -- one sentence names four things -- and stored separately so
the weaker fact cannot inherit the stronger one's authority.

THE SPAN IS THE EVIDENCE. Every stored row is provable by slicing the claim's own verbatim.
Nothing here rests on a similarity score: the standing rule is that embeddings may propose
and only symbols decide, and a mention whose only justification is "these felt close" is
exactly the finding-with-a-threshold-in-it that rule forbids.

THREE TIERS, AND WHAT SEPARATES THEM IS WHO DECIDES.

  literal_name          The name occurs whole. Symbols locate it and symbols settle it: 136
                        mentions, and a twelve-row sample was entirely correct.
  core_phrase_verified  The name minus its leading verb occurs whole -- the wiki writes
                        "Move Toward a Circular Economy" and a report writes "circular
                        economy". Symbols locate it; only meaning can settle it, because the
                        same rule leaves "Greenhouse Gas Emissions" when "Offset" is stripped
                        and that is a subject area, not a programme. 50 judged, 19 kept.
  core_phrase_verified  ...and, in the same tier, an ORDER-INSENSITIVE window: the wiki has
                        "Geothermal Districts" and the reports write "a district geothermal
                        loop in the Bryant neighborhood". A run of consecutive words holding
                        every identity word and nothing else but function words, so the span
                        stays tight and provable. Verified like any other proposal.
  proximity             The identity words merely appear near each other, scattered. Never
                        stored, in any form. These sit in the queue for a person.

THE VERIFIER MOVES NO TEXT AND FINDS NOTHING. It is handed a span that already exists and
asked one question: is this occurrence a reference to the programme, or shared vocabulary?
It refused 31 of 50, and it drew the line INSIDE a single programme -- keeping "Launched a
City circular economy website" and refusing "presentations to the community on the circular
economy". That distinction is not available to any string rule, and inventing a frequency
cutoff to approximate it would be the threshold-in-a-finding this project forbids.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from pipeline.resolve_actions import _LEAD_VERBS, _STOP, key_terms

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# A one-word name is never a mention. The wiki has initiatives called "Offsets" and
# "Benchmarking", and matching those as phrases would tag every sentence using the word.
MIN_NAME_WORDS = 2

# How far apart a name's identity words may sit and still be PROPOSED as one reference.
# Only ever used for the queue; nothing is written on this basis.
_PROXIMITY_CHARS = 60


def literal_mentions(verbatim: str,
                     names: list[tuple[int, str, str]]) -> list[dict]:
    """Every occurrence of a subject's name or alias in this verbatim, with its span.

    `names` is (subject_id, text, method). Case-insensitive, because the reports write
    "community choice aggregation legislation" where the wiki has "Community Choice
    Aggregation" -- the same subject, differently cased, and matched_text keeps what the
    page actually printed.

    Word-boundary anchored at both ends, so "EV Infrastructure" does not match inside a
    longer word, and every occurrence is reported rather than only the first.
    """
    out: list[dict] = []
    if not (v := verbatim or ""):
        return out
    for sid, text, method in names:
        t = (text or "").strip()
        if len(t.split()) < MIN_NAME_WORDS:
            continue
        for m in re.finditer(rf"\b{re.escape(t)}\b", v, re.I):
            out.append({"subject_id": sid, "span_start": m.start(), "span_end": m.end(),
                        "matched_text": v[m.start():m.end()], "method": method})
    # Deterministic, and a longer name wins its position: "Green Rental Housing Program"
    # rather than a shorter subject that happens to sit inside it.
    out.sort(key=lambda d: (d["span_start"], -(d["span_end"] - d["span_start"])))
    return _drop_contained(out)


def _drop_contained(spans: list[dict]) -> list[dict]:
    """Remove a mention wholly inside another mention's span.

    "Solarize Ann Arbor" contains "Ann Arbor", which is a place subject and not what the
    sentence is naming. The longer match is the more specific reading.
    """
    kept: list[dict] = []
    for s in spans:
        if any(k["span_start"] <= s["span_start"] and s["span_end"] <= k["span_end"]
               and k is not s for k in kept):
            continue
        kept.append(s)
    return kept


def core_phrase(name: str) -> str:
    """A name with only its LEADING verbs and function words removed.

    The wiki names an initiative for the doing of it -- "Move Toward a Circular Economy",
    "Support Aging in Place Efficiently" -- and a report writes the thing itself: "engagement
    around the circular economy", "our Aging in Place Efficiently program". Stripping the
    lead restores a phrase that occurs literally, so the match keeps a real span.

    ONLY THE LEAD. Interior function words are structural -- "Aging IN Place" -- and removing
    them would leave a phrase the document never printed and no span could prove.
    """
    words = re.findall(r"[A-Za-z0-9']+", name or "")
    i = 0
    while i < len(words) and (words[i].lower() in _LEAD_VERBS or words[i].lower() in _STOP):
        i += 1
    return " ".join(words[i:])


def core_phrase_mentions(verbatim: str,
                         names: list[tuple[int, str, str]]) -> list[dict]:
    """Occurrences of a name's core phrase, with a span. PROPOSALS -- see the module note.

    These are located, not decided. A core phrase can be a real programme name ("Aging in
    Place Efficiently") or generic domain vocabulary ("Greenhouse Gas Emissions", left when
    "Offset" is stripped from "Offset Greenhouse Gas Emissions"), and no amount of string
    matching separates those two: one names a thing, the other describes a subject area.
    Only meaning does, which is why these go to a verifier rather than to the store.
    """
    out: list[dict] = []
    v = verbatim or ""
    for sid, text, _ in names:
        core = core_phrase(text)
        if len(core.split()) < MIN_NAME_WORDS or core.lower() == (text or "").lower():
            continue                       # nothing was stripped: literal_mentions had it
        for m in re.finditer(rf"\b{re.escape(core)}\b", v, re.I):
            out.append({"subject_id": sid, "name": text, "core": core,
                        "span_start": m.start(), "span_end": m.end(),
                        "matched_text": v[m.start():m.end()]})
    return _drop_contained(out)


# Interior words a name's terms may be separated by and still read as one phrase:
# "district geothermal SYSTEM", "affordable housing SITES to net zero energy". More than two
# and the words are no longer naming one thing together.
_MAX_FILLER = 2


def _stem(word: str) -> str:
    """Crudest possible singular/plural fold, and deliberately no more.

    "Geothermal Districts" against "district geothermal" differs by one trailing s. A real
    stemmer would also fold "housing" to "hous" and start matching things that share a root
    but not a referent, which is precisely the failure this module keeps refusing to make.
    """
    w = word.lower()
    for suffix in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


def window_mentions(verbatim: str,
                    names: list[tuple[int, str, str]],
                    max_filler: int = _MAX_FILLER) -> list[dict]:
    """A name's identity words appearing together in ANY ORDER. PROPOSALS, like core phrases.

    THE CASE THIS EXISTS FOR. The wiki curates "Geothermal Districts"; the reports write "a
    district geothermal study around Veterans Park" and "design a district geothermal loop in
    the Bryant neighborhood". Same referent, reversed order, and one trailing s -- so the
    literal and core-phrase tiers both miss it, and the 2020 idea never meets what it became.

    STILL A SPAN, AND STILL TIGHT. The match is a run of CONSECUTIVE words containing every
    identity word and nothing else except at most `max_filler` function words. That is what
    keeps it provable: the slice really does contain the name's words and little besides.
    Scattered words across a sentence remain proximity, which is never stored.
    """
    out: list[dict] = []
    v = verbatim or ""
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9']+", v)]
    if not toks:
        return out
    stems = [_stem(t[0]) for t in toks]

    for sid, text, _ in names:
        terms = {_stem(w) for w in key_terms(text)}
        if len(terms) < MIN_NAME_WORDS:
            continue
        n = len(terms)
        for size in range(n, n + max_filler + 1):
            for i in range(0, len(toks) - size + 1):
                win = stems[i:i + size]
                if not terms <= set(win):
                    continue
                # Everything else in the window must be a function word, or the run is a
                # sentence that happens to contain the terms rather than a phrase naming one.
                if any(w not in terms and w not in _STOP for w in win):
                    continue
                # Trim to the identity words themselves so the span is the name, not the
                # articles around it.
                hits = [j for j, w in enumerate(win) if w in terms]
                a, b = toks[i + hits[0]][1], toks[i + hits[-1]][2]
                out.append({"subject_id": sid, "name": text,
                            "span_start": a, "span_end": b, "matched_text": v[a:b]})
                break
            else:
                continue
            break
    return _drop_contained(out)


def proximity_candidates(verbatim: str,
                         names: list[tuple[int, str, str]],
                         window: int = _PROXIMITY_CHARS) -> list[dict]:
    """Subjects whose identity words all appear close together. PROPOSALS ONLY.

    Never written to the store. This is what a person or the semantic pass looks at, and it
    exists because "pilot commercial Solarize program" really is the Commercial Solarize
    Pilot Program and no literal rule will ever see it.
    """
    out: list[dict] = []
    flat = re.sub(r"[^a-z0-9 ]+", " ", (verbatim or "").lower())
    for sid, text, _ in names:
        tk = key_terms(text)
        if len(tk) < 2:
            continue
        firsts = []
        for term in tk:
            m = re.search(rf"\b{re.escape(term)}\b", flat)
            if not m:
                firsts = []
                break
            firsts.append(m.start())
        if firsts and max(firsts) - min(firsts) <= window:
            out.append({"subject_id": sid, "name": text,
                        "window": [min(firsts), max(firsts)]})
    return out


VERIFY_PROMPT = """You decide whether one sentence REFERS TO a specific named programme.

You are given a sentence from a City of Ann Arbor climate report, a programme name, and the
exact phrase inside the sentence that a string match located. Decide whether that phrase, in
this sentence, refers to THAT PROGRAMME.

Answer "yes" only when the sentence is talking about the programme itself. Answer "no" when
the phrase is ordinary subject-area language that happens to overlap the name.

  programme "Offset Greenhouse Gas Emissions", phrase "greenhouse gas emissions",
  sentence "...helping governments measure their greenhouse gas emissions..."   -> no
  (the sentence is about measuring emissions generally, not about the offsets programme)

  programme "Support Aging in Place Efficiently", phrase "Aging in Place Efficiently",
  sentence "...improvements through our Aging in Place Efficiently program."    -> yes

You are not asked to rewrite anything, and you may not. Reply with exactly one word: yes or no.
"""


def verify(verbatim: str, name: str, matched_text: str,
           deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> bool | None:
    """Does this located phrase refer to this programme? True, False, or None if unusable.

    THE MODEL DECIDES NOTHING ABOUT THE TEXT. The span is already found and the words are
    already the document's; the only judgement asked for is whether an occurrence is a
    reference or a coincidence of vocabulary -- which is the one thing string matching
    provably cannot do, having proposed "greenhouse gas emissions" and "Aging in Place
    Efficiently" with identical confidence.

    None rather than a guess when the answer is not a clean yes or no, so an unparseable
    reply leaves the row unstored instead of silently accepted.
    """
    from openai import OpenAI

    from pipeline import config

    client = OpenAI(base_url=config.get("OPENAI_BASE_URL"),
                    api_key=config.get("OPENAI_API_KEY"))
    user = (f"programme: {name}\nphrase: {matched_text}\nsentence: {verbatim}")
    resp = client.chat.completions.create(
        model=config.get(deployment_env),
        messages=[{"role": "system", "content": VERIFY_PROMPT},
                  {"role": "user", "content": user}],
    )
    a = (resp.choices[0].message.content or "").strip().lower().rstrip(".")
    return True if a.startswith("yes") else False if a.startswith("no") else None


def _names(cur) -> list[tuple[int, str, str]]:
    """(subject_id, text, method) for every initiative name and approved alias."""
    # live_subjects, not subjects: a merged duplicate still holds its name, and matching on
    # it would attach mentions to a tombstone nothing else points at. See migrations/024.
    cur.execute("SELECT id, name FROM live_subjects WHERE subject_kind = 'initiative'")
    out = [(sid, n, "literal_name") for sid, n in cur.fetchall()]
    cur.execute("""SELECT a.subject_id, a.alias FROM subject_aliases a
                     JOIN subjects s ON s.id = a.subject_id
                    WHERE s.subject_kind = 'initiative'""")
    out += [(sid, a, "literal_alias") for sid, a in cur.fetchall()]
    return out


def run(doc_type: str = "annual_report", dsn: str = DSN, dry_run: bool = False,
        detected_by: str = "detect_mentions", queue_path: Path | None = None,
        verify_fn=None) -> dict:
    """`verify_fn(verbatim, name, matched_text) -> bool|None` enables the core-phrase pass.

    Passing None keeps the run purely symbolic, which is what the tests and a no-network run
    want; nothing is stored on a core phrase without a decision.
    """
    import psycopg

    counts = {"claims": 0, "mentions": 0, "claims_with_a_mention": 0, "queued": 0,
              "verified": 0, "verify_rejected": 0, "core_verified": 0}
    queue: list[dict] = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        names = _names(cur)
        cur.execute("""SELECT cl.id, cl.verbatim FROM claims cl
                         JOIN document_sections s ON s.id = cl.document_section_id
                         JOIN documents d ON d.id = s.document_id
                        WHERE d.doc_type = %s ORDER BY cl.id""", (doc_type,))
        rows = cur.fetchall()
        counts["claims"] = len(rows)
        for claim_id, verbatim in rows:
            found = literal_mentions(verbatim, names)
            if found:
                counts["claims_with_a_mention"] += 1
                counts["mentions"] += len(found)
                if not dry_run:
                    for f in found:
                        cur.execute(
                            """INSERT INTO claim_subject_mentions
                                 (claim_id, subject_id, span_start, span_end, matched_text,
                                  method, detected_by)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (claim_id, subject_id, span_start) DO NOTHING""",
                            (claim_id, f["subject_id"], f["span_start"], f["span_end"],
                             f["matched_text"], f["method"], detected_by))
                continue
            # A CORE PHRASE IS LOCATED BUT NOT DECIDED. It has a real span, so it can be
            # stored -- but only once something has ruled that the occurrence is a reference
            # rather than shared vocabulary. String matching proposed "greenhouse gas
            # emissions" and "Aging in Place Efficiently" with identical confidence.
            # ORDER-INSENSITIVE LAST. A core phrase preserves the name's own word order and
            # is the stronger reading, so it is offered first; the window tier exists for
            # "Geothermal Districts" against "district geothermal", where no ordered rule can
            # reach. Both are proposals and both go to the same verifier.
            core = core_phrase_mentions(verbatim, names) if verify_fn else []
            if verify_fn:
                seen = {(c["subject_id"], c["span_start"]) for c in core}
                core += [w for w in window_mentions(verbatim, names)
                         if (w["subject_id"], w["span_start"]) not in seen]
            confirmed = []
            for cand in core:
                counts["verified"] += 1
                ok = verify_fn(verbatim, cand["name"], cand["matched_text"])
                if ok:
                    confirmed.append(cand)
                else:
                    # A refusal is a result, kept so the queue shows what was considered and
                    # rejected rather than only what was never looked at.
                    counts["verify_rejected"] += 1
            if confirmed:
                counts["claims_with_a_mention"] += 1
                counts["mentions"] += len(confirmed)
                counts["core_verified"] += len(confirmed)
                if not dry_run:
                    for f in confirmed:
                        cur.execute(
                            """INSERT INTO claim_subject_mentions
                                 (claim_id, subject_id, span_start, span_end, matched_text,
                                  method, detected_by)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (claim_id, subject_id, span_start) DO NOTHING""",
                            (claim_id, f["subject_id"], f["span_start"], f["span_end"],
                             f["matched_text"], "core_phrase_verified", detected_by))
                continue

            # Only propose where nothing was proved: a claim with a literal mention needs no
            # guesswork, and queueing it would bury the ones that do.
            prox = proximity_candidates(verbatim, names)
            if prox:
                queue.append({"claim_id": claim_id, "verbatim": verbatim,
                              "candidates": prox,
                              "core_phrases_rejected": [
                                  {"name": c["name"], "phrase": c["matched_text"]}
                                  for c in core]})
        if not dry_run:
            c.commit()
    counts["queued"] = len(queue)
    if queue_path:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(json.dumps(queue, indent=1))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doc-type", default="annual_report")
    ap.add_argument("--detected-by", default="detect_mentions")
    ap.add_argument("--queue", help="where to write proximity proposals, which are NOT stored")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--verify", action="store_true",
                    help="run the core-phrase pass, which costs one model call per candidate")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    r = run(a.doc_type, a.dsn, a.dry_run, a.detected_by,
            Path(a.queue) if a.queue else None,
            verify_fn=(verify if a.verify else None))
    print(f"[mentions] {a.doc_type}: {r['claims']} claim(s) read")
    print(f"[mentions]   {r['mentions']} mention(s) stored across "
          f"{r['claims_with_a_mention']} claim(s), each provable by span")
    if r["verified"]:
        print(f"[mentions]   {r['core_verified']} core-phrase mention(s) confirmed of "
              f"{r['verified']} judged ({r['verify_rejected']} refused as shared vocabulary)")
    print(f"[mentions]   {r['queued']} claim(s) queued on proximity — proposed, never stored")
    if a.dry_run:
        print("[mentions]   dry run — nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
