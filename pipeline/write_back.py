"""Apply a recorded correction to the converted text without breaking stored claims.

A claim is a character range in the canonical text. Any edit that changes length moves every
span after it -- and a moved span still round-trips perfectly against the text it was written
from while pointing at different words in the text that is now there. Nothing raises. That is
this project's founding failure mode arriving at the last layer, so most of this module is
refusals.

CONCRETELY, ON THIS CORPUS. The `careprogram` correction sits in section 147 at chars
19145-21246. Section 148's forty-one claims begin at 21621. "Care program" is one character
longer than "Careprogram". Applied without migration, all forty-one read one character off,
for ever, with no error.

THE RECORDED READING IS A WINDOW, NOT A REPLACEMENT. The semantic pass captured the second
arm's reading as an aligned window so the model had a real choice to make: "March 215, 2022"
against "March 21st, 2022". Substituting the recorded string for the conflict token yields
"March 21st, 2022, 2022". minimal_edit exists because the thing we asked a human to judge and
the thing we may safely write are not the same string.

WHAT IS DELIBERATELY NOT AUTOMATED. A claim whose own span CONTAINS a correction cannot be
fixed by arithmetic: its verbatim now disagrees with the document. Those are flagged for
re-anchoring rather than shifted, because shifting them would produce a claim that looks
healthy and quotes text the document no longer contains.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edit:
    """One replacement, positioned in the canonical text."""
    at: int
    old_len: int
    new_len: int

    @property
    def delta(self) -> int:
        return self.new_len - self.old_len

    @property
    def end(self) -> int:
        return self.at + self.old_len


def minimal_edit(before: str, after: str) -> tuple[str, str]:
    """The smallest substring that actually differs, with its replacement.

    Shared leading and trailing text is not part of the change, and including it is how a
    correction acquires a duplicated ", 2022".
    """
    if before == after:
        return "", ""
    n = min(len(before), len(after))
    pre = 0
    while pre < n and before[pre] == after[pre]:
        pre += 1
    suf = 0
    while (suf < n - pre
           and before[len(before) - 1 - suf] == after[len(after) - 1 - suf]):
        suf += 1
    return before[pre:len(before) - suf], after[pre:len(after) - suf]


def locate_unique(text: str, needle: str) -> int | None:
    """The offset of `needle` when it occurs EXACTLY once, else None.

    Two candidates means the edit could land in the wrong sentence, and nothing in the
    record says which one the reviewer read. There is no safe tie-break, so there is none.
    """
    if not needle:
        return None
    first = text.find(needle)
    if first < 0 or text.find(needle, first + 1) >= 0:
        return None
    return first


def shift_spans(spans: list[tuple[int, int]],
                edits: list[Edit]) -> list[tuple[int, int, str]]:
    """Migrate stored spans across a set of edits, flagging what arithmetic cannot fix."""
    for a, b in zip(edits, edits[1:]):
        if b.at < a.at:
            raise ValueError("edits must be supplied in ascending position; "
                             "accumulating deltas out of order mis-shifts every span "
                             "after the first inversion")
    out = []
    for start, end in spans:
        if any(start <= e.at < end for e in edits):
            out.append((start, end, "needs_reanchor"))
            continue
        delta = sum(e.delta for e in edits if e.end <= start)
        out.append((start + delta, end + delta,
                    "shifted" if delta else "unchanged"))
    return out


def measure_edit(old_md: str, new_md: str) -> tuple[Edit | None, str, str]:
    """Rebuild both markdowns and report the change in CANONICAL coordinates.

    THE FILE AND THE COORDINATE SPACE ARE NOT THE SAME. Comments, heading markup and block
    separators sit between them, so the offset an edit lands on in the markdown is not the
    offset a claim lives at. Assuming otherwise would migrate every span by a plausible
    wrong number.

    Returns (edit, old_core, new_core). The caller compares the cores against what it
    intended: if editing the file changed more than the intended words -- a heading
    absorbed, two blocks merged -- they will not match, and migrating spans against that
    delta would be worse than doing nothing.
    """
    from pipeline.canonical import build

    a = build(old_md).text
    b = build(new_md).text
    if a == b:
        return None, "", ""
    old_core, new_core = minimal_edit(a, b)
    n = min(len(a), len(b))
    pre = 0
    while pre < n and a[pre] == b[pre]:
        pre += 1
    return Edit(at=pre, old_len=len(old_core), new_len=len(new_core)), old_core, new_core


def plan_correction(md: str, primary_window: str, resolved_window: str) -> dict:
    """Plan one correction against a markdown file. Refuses far more often than it applies.

    The intended change is derived from the two WINDOWS -- the same span of page as each arm
    read it -- so `minimal_edit` yields the smallest substring that actually differs. The
    plan is then verified by rebuilding the canonical text and checking that the change it
    measures is exactly that one. Anything else means the substitution had side effects in
    the document structure, and a span migration computed from it would be confidently
    wrong.
    """
    want_old, want_new = minimal_edit(primary_window, resolved_window)
    if (want_old, want_new) == ("", ""):
        return {"ok": False, "why": "the two readings are identical; nothing to change"}

    at = locate_unique(md, primary_window)
    if at is None:
        return {"ok": False,
                "why": f"{primary_window!r} must appear exactly once in the markdown; "
                       f"it appears {md.count(primary_window)} time(s)"}

    new_md = md[:at] + resolved_window + md[at + len(primary_window):]
    edit, got_old, got_new = measure_edit(md, new_md)
    if edit is None:
        return {"ok": False, "why": "the rebuilt canonical text is unchanged; nothing to do"}
    if (got_old, got_new) != (want_old, want_new):
        return {"ok": False,
                "why": f"the edit changed more than intended: expected "
                       f"{want_old!r}->{want_new!r}, the rebuilt text shows "
                       f"{got_old!r}->{got_new!r} (a side effect on document structure)",
                "edit": edit}
    return {"ok": True, "new_md": new_md, "edit": edit,
            "old": want_old, "new": want_new}


def atomic_apply(md_path, new_md: str, db_work) -> None:
    """Write the markdown and do the database work as one unit, or neither.

    THE INCIDENT THIS EXISTS FOR. The first version wrote the file, then ran the database
    updates. The last statement raised, the transaction rolled back, and the file kept its
    correction -- leaving 41 claims pointing one character off, with nothing raised and
    nothing to notice, inside the module written to prevent that exact thing.

    A filesystem write is not transactional and a database commit is, so they cannot be
    made truly atomic. What they can be is REVERSIBLE: the original bytes are held, the
    file is written so that db_work can re-read and re-hash what will be committed, and any
    failure puts the original back before the exception propagates.
    """
    from pathlib import Path

    md_path = Path(md_path)
    original = md_path.read_text()
    md_path.write_text(new_md)
    try:
        db_work()
    except BaseException:
        md_path.write_text(original)
        raise


def unmigratable_span_columns(claims: list[dict]) -> list[int]:
    """Claim ids carrying span data this migration does not move.

    claims.extra_spans holds additional ranges, and nothing here shifts them. It is empty
    across the whole corpus today -- which is precisely why the guard exists: the day it is
    populated, a partial migration would leave the main span correct and the extras pointing
    at the wrong words, which is the quietest possible corruption.
    """
    return [c["id"] for c in claims
            if c.get("extra_spans") not in (None, [], {}, "null")]


DSN = __import__("os").environ.get(
    "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine")


def apply_correction(document_id: int, primary_window: str, resolved_window: str,
                     applied_by: str, dsn: str = DSN, dry_run: bool = True) -> dict:
    """Write one correction to the markdown and migrate everything that points into it.

    REFUSES BEFORE IT WRITES, in this order:
      * the stored conversion hash must still match the file -- otherwise the spans in the
        store were written against a different text and none of this arithmetic applies;
      * the plan must be clean (see plan_correction);
      * no claim's span may CONTAIN the edit. Such a claim's verbatim now disagrees with
        the document, and shifting it by a delta would leave it looking healthy while
        quoting words the document no longer holds. Those are named and the write is
        refused, because deciding what to do with them is a person's job.

    WHAT MOVES. Claim spans after the edit shift by the delta. Section char ranges shift.
    Every section's content_hash is recomputed, and the document's with it -- which makes
    any human approval lapse on its own, because human_verdict_hash will no longer match.
    That is correct: an approval was given for text that has now changed.
    """
    import hashlib
    from pathlib import Path

    import psycopg

    from pipeline.canonical import build, sections as canon_sections

    with psycopg.connect(dsn) as c:
        row = c.execute("SELECT markdown_path, content_hash FROM documents WHERE id=%s",
                        (document_id,)).fetchone()
    if not row:
        return {"ok": False, "why": f"no document {document_id}"}
    md_path, stored_hash = Path(row[0]), row[1]
    md = md_path.read_text()

    canon = build(md)
    if canon.content_hash != stored_hash:
        return {"ok": False,
                "why": "the conversion no longer matches what was ingested; stored spans "
                       "were written against different text. Re-ingest before correcting."}

    plan = plan_correction(md, primary_window, resolved_window)
    if not plan["ok"]:
        return plan
    edit = plan["edit"]

    with psycopg.connect(dsn) as c:
        rows_ = c.execute(
            "SELECT c.id, c.span_start, c.span_end, c.extra_spans FROM claims c "
            "JOIN document_sections s ON s.id = c.document_section_id "
            "WHERE s.document_id = %s AND c.span_start IS NOT NULL "
            "ORDER BY c.span_start", (document_id,)).fetchall()
    claims = [(a, b, d) for a, b, d, _ in rows_]
    extras = unmigratable_span_columns(
        [{"id": a, "extra_spans": e} for a, _, _, e in rows_])
    if extras:
        return {"ok": False,
                "why": f"{len(extras)} claim(s) carry extra_spans, which this migration "
                       f"does not move: {extras[:10]}. Shifting only the main span would "
                       f"leave the extras pointing at the wrong words."}

    moved = shift_spans([(a, b) for _, a, b in claims], [edit])
    blocked = [cid for (cid, _, _), (_, _, state) in zip(claims, moved)
               if state == "needs_reanchor"]
    if blocked:
        return {"ok": False, "edit": edit,
                "why": f"{len(blocked)} claim(s) span the corrected words and cannot be "
                       f"migrated by arithmetic: {blocked[:10]}. Their verbatim disagrees "
                       f"with the document now; re-extract or delete them first."}

    new_canon = build(plan["new_md"])
    report = {"ok": True, "edit": edit, "old": plan["old"], "new": plan["new"],
              "claims_shifted": sum(1 for _, _, s in moved if s == "shifted"),
              "claims_unchanged": sum(1 for _, _, s in moved if s == "unchanged"),
              "new_hash": new_canon.content_hash, "markdown_path": str(md_path)}
    if dry_run:
        report["dry_run"] = True
        return report

    def _db_work():
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            for (cid, _, _), (a, b, state) in zip(claims, moved):
                if state == "shifted":
                    cur.execute("UPDATE claims SET span_start=%s, span_end=%s WHERE id=%s",
                                (a, b, cid))
            # Sections are rewritten from the rebuilt canonical text rather than patched,
            # so char ranges and hashes cannot drift apart from the text they describe.
            for sec in canon_sections(new_canon):
                cur.execute(
                    "UPDATE document_sections SET char_start=%s, char_end=%s, "
                    "content_hash=%s WHERE document_id=%s AND sequence=%s",
                    (sec["char_start"], sec["char_end"], sec["content_hash"],
                     document_id, sec["sequence"]))
            cur.execute("UPDATE documents SET content_hash=%s WHERE id=%s",
                        (new_canon.content_hash, document_id))
            cur.execute(
                "INSERT INTO research_questions (question, origin, priority, status) "
                "VALUES (%s,'human',5,'answered')",
                (f"Correction applied to document {document_id} by {applied_by}: "
                 f"{plan['old']!r} -> {plan['new']!r} at canonical offset {edit.at}. "
                 f"Source: cross-arm conflict adjudicated by the semantic pass.",))
            c.commit()

    atomic_apply(md_path, plan["new_md"], _db_work)
    return report


def settle_document(document_id: int, pdf_path, second_md_path, applied_by: str,
                    dsn: str = DSN, dry_run: bool = True) -> list[dict]:
    """Carry the adjudicator's verdicts into the text and the flags.

    THREE OUTCOMES, THREE ACTIONS, and the middle one is the easily-missed case:
      second_read  the primary is wrong -> write the correction
      primary      the primary is RIGHT -> clear the conflict flag and nothing else
      unresolved   nobody decided -> leave the flag, keep the section held

    A decided-for-the-primary conflict that keeps its flag holds a perfectly good section
    out of extraction for ever, on the strength of a disagreement that has already been
    settled. Years 4 and 5 each had one.

    Corrections are applied ONE AT A TIME with the text re-read between them, because each
    write moves every offset after it and a batch computed up front would apply the second
    edit against stale coordinates.
    """
    import json

    import psycopg

    from pipeline.adjudicate import (WINNER_PRIMARY, WINNER_SECOND, adjudicate_document)
    from pipeline.canonical import build
    from pipeline.semantic_pass import build_conflict_case

    out: list[dict] = []
    second_text = open(second_md_path).read()
    while True:
        verdicts = adjudicate_document(document_id, pdf_path, second_md_path, dsn)
        todo = [v for v in verdicts if v["winner"] == WINNER_SECOND]
        if not todo:
            break
        v = todo[0]
        with psycopg.connect(dsn) as c:
            md_path = c.execute("SELECT markdown_path FROM documents WHERE id=%s",
                                (document_id,)).fetchone()[0]
        case = build_conflict_case(v["token"], build(open(md_path).read()).text, second_text)
        if case is None:
            out.append({"token": v["token"], "action": "no safe window; left alone"})
            break
        r = apply_correction(document_id, case["primary"], case["second"], applied_by,
                             dsn, dry_run)
        out.append({"token": v["token"], "action": "corrected" if r["ok"] else "refused",
                    "detail": f"{r.get('old')!r}->{r.get('new')!r}" if r["ok"]
                              else r["why"][:110]})
        if dry_run or not r["ok"]:
            break                      # without a write the loop would never terminate

        # RE-CORROBORATE BEFORE LOOKING AGAIN. adjudicate_document reads the stored
        # ocr_conflicts flags, and those still name the token we just fixed. Without this
        # the next pass tries to correct "actgrant" in a document that now says "act
        # grant", finds no window, and stops with four corrections still outstanding.
        from pipeline.corroborate_ocr import apply as record_corroboration, compare
        with psycopg.connect(dsn) as c:
            md_now = c.execute("SELECT markdown_path FROM documents WHERE id=%s",
                               (document_id,)).fetchone()[0]
        record_corroboration(document_id,
                             compare(open(md_now).read(), second_text),
                             "azure_content_understanding", dsn, dry_run=False)

    # Conflicts the primary won: settled, so the flag must go.
    settled = {v["token"] for v in adjudicate_document(document_id, pdf_path,
                                                       second_md_path, dsn)
               if v["winner"] == WINNER_PRIMARY}
    if settled and not dry_run:
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            cur.execute("SELECT id, parse_flags FROM document_sections "
                        "WHERE document_id=%s AND parse_flags ? 'ocr_conflicts'",
                        (document_id,))
            for sid, flags in cur.fetchall():
                flags = dict(flags or {})
                keep = [x for x in flags.get("ocr_conflicts", []) if x not in settled]
                if keep == flags.get("ocr_conflicts", []):
                    continue
                if keep:
                    flags["ocr_conflicts"] = keep
                else:
                    flags.pop("ocr_conflicts", None)
                cur.execute("UPDATE document_sections SET parse_flags=%s, "
                            "parse_confidence='unaudited' WHERE id=%s",
                            (json.dumps(flags), sid))
            c.commit()
    for t in sorted(settled):
        out.append({"token": t, "action": "conflict cleared (primary was right)"})
    return out
