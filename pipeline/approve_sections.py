"""Record a person's verdict on sections, separately from the machine's.

WHY THIS IS NOT section_audit WITH A FLAG. section_audit computes what the FLAGS say: it is
a function of parse_flags and the stored hash, it is reproducible, and it is allowed to be
re-run at any time by anything. This records what a PERSON checked, which is none of those
things. Keeping them in one column is what let a maintenance run overwrite a human review of
Year 2 with the string 'machine (OCR provenance repair)' and raise nothing.

They are meant to disagree. Year 2 is 96% OCR, so the machine says `suspect` and should keep
saying it forever -- that fact does not go away because someone read the document. What a
person can add is independent evidence the machine does not have: "I compared this against
the rendered PDF." Both belong in the store, visibly, side by side.

THE APPROVAL IS PINNED TO THE TEXT THAT WAS READ. human_verdict_hash stores the section's
content_hash at approval time. If the conversion later changes, the hash no longer matches
and the approval stops counting -- automatically, with no one having to remember. section_audit
already refuses to audit a document whose conversion moved, on the grounds that "a review of
text that moved is not a review of the text in the store". An approval is the stronger case:
it is the thing that lets extraction spend money and write claims.

WHAT THE NOTE IS FOR. `--note` is required for approval and is not ceremony. A reviewer who
read prose for sense has not checked that OCR read "$45,800" and not "$45,300", and those are
different warrants. The note is where the difference is recorded, so a later reader knows what
the approval actually covers.
"""
from __future__ import annotations

import argparse
import os

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

_VERDICTS = ("approved", "approved_with_caveats", "rejected", "not_reviewed")


def approve(document_id: int, verdict: str, by: str, note: str,
            sequences: list[int] | None = None, dsn: str = DSN,
            dry_run: bool = False) -> list[dict]:
    """Set a human verdict on a document's sections, pinned to their current hashes."""
    import psycopg

    if verdict not in _VERDICTS:
        raise SystemExit(f"verdict must be one of {_VERDICTS}")
    if verdict != "not_reviewed" and not (by and note):
        raise SystemExit("--by and --note are required for any verdict but not_reviewed")

    out = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        q = ("SELECT id, sequence, heading, content_hash, parse_confidence "
             "FROM document_sections WHERE document_id = %s")
        args: tuple = (document_id,)
        if sequences:
            q += " AND sequence = ANY(%s)"
            args += (sequences,)
        cur.execute(q + " ORDER BY sequence", args)
        for sid, seq, heading, chash, machine in cur.fetchall():
            out.append({"section_id": sid, "sequence": seq, "heading": heading or "",
                        "machine": machine, "verdict": verdict})
            if dry_run:
                continue
            cur.execute(
                """UPDATE document_sections
                      SET human_verdict=%s, human_verdict_by=%s, human_verdict_at=now(),
                          human_verdict_note=%s, human_verdict_hash=%s
                    WHERE id=%s""",
                (verdict, by, note, chash, sid))
        if not dry_run:
            c.commit()
    return out


def stale(dsn: str = DSN) -> list[dict]:
    """Approvals whose text has changed since. They no longer count; this says which."""
    import psycopg

    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT id, document_id, sequence, human_verdict, human_verdict_by "
            "FROM document_sections "
            "WHERE human_verdict IS NOT NULL AND human_verdict <> 'not_reviewed' "
            "AND human_verdict_hash IS DISTINCT FROM content_hash "
            "ORDER BY document_id, sequence").fetchall()
    return [{"section_id": a, "document_id": b, "sequence": c_, "verdict": d, "by": e}
            for a, b, c_, d, e in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="record a human verdict on sections")
    ap.add_argument("--document-id", type=int)
    ap.add_argument("--verdict", choices=_VERDICTS)
    ap.add_argument("--by", help="the person standing behind this")
    ap.add_argument("--note", help="what was actually checked, and what was not")
    ap.add_argument("--sequences", type=int, nargs="*",
                    help="section sequences; omit for the whole document")
    ap.add_argument("--stale", action="store_true",
                    help="list approvals whose text has changed since")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.stale:
        rows = stale(a.dsn)
        for r in rows:
            print(f"  STALE  doc {r['document_id']} seq {r['sequence']:>2}  "
                  f"{r['verdict']} by {r['by']} — text has changed since")
        print(f"[approve] {len(rows)} approval(s) no longer match their text")
        return 1 if rows else 0

    if a.document_id is None or not a.verdict:
        ap.error("--document-id and --verdict are required unless --stale")
    rows = approve(a.document_id, a.verdict, a.by or "", a.note or "",
                   a.sequences, a.dsn, a.dry_run)
    for r in rows:
        print(f"  seq {r['sequence']:>2}  machine={r['machine']:<9} "
              f"human={r['verdict']:<22} {r['heading'][:38]}")
    print(f"[approve] {len(rows)} section(s) set to '{a.verdict}' by {a.by}"
          + ("  (dry run — nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
