"""Let a SECTION earn `clean`, so extraction has something it is allowed to read.

NOT pipeline/parse_audit.py. That is a different gate at a different layer: it asks whether
a PDF PARSED, from the PDF itself, before any markdown exists. This asks whether a section
of an already-converted, already-reviewed document may be extracted from. I first wrote
this file over parse_audit.py -- Write to a path I had not read -- and the names were close
enough that it looked like the right home. It was not.

Every section arrives `unaudited` and extraction accepts only `clean`. That is deliberate --
every failure this corpus produced was silent, so a section nothing has checked must not
behave like one something has -- but it means nothing is extractable until something here
says otherwise.

TWO SIGNATURES ARE REQUIRED, AND NEITHER IS SUFFICIENT ALONE.

A HUMAN signs off the document. Caleb worked a 132-item checklist against these five PDFs
and found things no check could: a caption attributed to the wrong section, a heading that
was really a signature, a sign-off wrongly tagged furniture. That judgement does not reduce
to a rule, and no rule should pretend to replace it.

The MACHINE re-checks what it can, and may only DEMOTE. It recomputes each section's
content_hash from the markdown on disk -- if the conversion moved since ingest, the human
signed off text that is no longer there. It re-runs the plausibility check that caught two
sentences woven together after a clean gate AND a clean checklist. And it reads the flags
ingest recorded.

So the human's sign-off is necessary and the machine's agreement is necessary. A section
where they disagree comes out `suspect`: stageable, not publishable, and visible.

WHY FLAGS DEMOTE RATHER THAN BLOCK THE DOCUMENT. `placement_inferred` means the coverage
sweep placed a block by geometry rather than by reading it. `has_ocr_blocks` means some
characters are a model's reading of pixels. Neither makes a document unusable; both make
one section less certain than its neighbours, which is what a per-section confidence is for.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Flags that make a section less certain than the document it sits in.
_DEMOTING = ("placement_inferred", "has_ocr_blocks", "ocr_conflicts")

# A flag that ANSWERS a demoting flag rather than merely accompanying it.
# has_ocr_blocks says "these characters came from a model reading pixels, and no text layer
# can check them". A second INDEPENDENT OCR engine reading the same pixels and producing the
# same characters is exactly the check that was missing -- the corroboration rule this store
# already applies to attestations, applied to reads. It does not make the text a text layer,
# and has_ocr_blocks stays on the row saying so; it means the specific doubt that flag
# raises has been met with evidence.
#
# ocr_conflicts is NOT answered this way and stays demoting: it is the record of where the
# two engines disagreed, which is the opposite of corroboration.
_ANSWERED_BY = {"has_ocr_blocks": "ocr_corroborated"}

_GARBLED = re.compile(r"[a-z][A-Z]")


def machine_verdict(body: str, stored_hash: str, flags: dict | None) -> tuple[str, str]:
    """What the machine can say about one section, on its own. It may only demote.

    The hash check is the one that matters most and is the cheapest: it asks whether the
    text a human signed off is still the text that is there. A conversion that moved since
    ingest invalidates the review, not merely the spans.
    """
    if hashlib.sha256(body.encode()).hexdigest() != stored_hash:
        return "suspect", "the conversion changed since ingest; the review no longer applies"
    garbled = [t for t in body.split() if len(_GARBLED.findall(t)) >= 2]
    if garbled:
        return "suspect", f"garbled token(s): {', '.join(garbled[:3])}"
    f = flags or {}
    hit = [x for x in _DEMOTING if f.get(x) and not f.get(_ANSWERED_BY.get(x, ""))]
    if hit:
        why = f"flagged {', '.join(hit)}"
        if f.get("ocr_conflicts"):
            why += f" ({len(f['ocr_conflicts'])} item(s): " \
                   f"{', '.join(map(str, f['ocr_conflicts'][:3]))})"
        return "suspect", why
    if f.get("has_ocr_blocks") and f.get("ocr_corroborated"):
        return "clean", (f"OCR text corroborated by a second independent read "
                         f"({f['ocr_corroborated']}); no figure or word disagreed")
    if not body.strip():
        return "suspect", "empty section"
    return "clean", "hash matches, no garbled tokens, no demoting flags"


def audit(document_id: int, reviewed_by: str, dsn: str = DSN,
          dry_run: bool = False) -> list[dict]:
    """Audit one document's sections. The caller asserts the human review happened."""
    import psycopg

    from pipeline.canonical import build

    out: list[dict] = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT markdown_path, parse_verdict, content_hash FROM documents "
                    "WHERE id = %s", (document_id,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"[audit] no document {document_id}")
        md_path, verdict, doc_hash = row
        if verdict == "REFUSE":
            raise SystemExit(
                f"[audit] document {document_id} was REFUSED by the quality gate. "
                f"A human cannot sign off a conversion the gate could not read; "
                f"re-convert it first.")

        canon = build(Path(md_path).read_text())
        if canon.content_hash != doc_hash:
            raise SystemExit(
                f"[audit] {md_path} no longer matches the ingested conversion\n"
                f"        stored {doc_hash[:16]}…  now {canon.content_hash[:16]}…\n"
                f"        re-ingest before auditing; a review of text that moved is not a "
                f"review of the text in the store.")

        cur.execute("SELECT id, sequence, heading, char_start, char_end, content_hash, "
                    "parse_flags FROM document_sections WHERE document_id = %s "
                    "ORDER BY sequence", (document_id,))
        for sid, seq, heading, a, b, sh, flags in cur.fetchall():
            conf, why = machine_verdict(canon.text[a:b], sh, flags)
            out.append({"section_id": sid, "sequence": seq, "heading": heading,
                        "parse_confidence": conf, "why": why})
            if not dry_run:
                cur.execute(
                    "UPDATE document_sections SET parse_confidence = %s, "
                    "parse_reviewed_by = %s, parse_reviewed_at = now() WHERE id = %s",
                    (conf, reviewed_by, sid))
        if not dry_run:
            c.commit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="record a human's document review as per-section confidence")
    ap.add_argument("--document-id", type=int, required=True)
    ap.add_argument("--reviewed-by", required=True,
                    help="the person asserting they reviewed this document")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = audit(a.document_id, a.reviewed_by, a.dsn, a.dry_run)
    for r in rows:
        print(f"  {r['sequence']:>2} {r['parse_confidence']:<8} "
              f"{(r['heading'] or '(front matter)')[:44]:<44} {r['why']}")
    n = sum(1 for r in rows if r["parse_confidence"] == "clean")
    print(f"[audit] document {a.document_id}: {n} clean, {len(rows) - n} suspect"
          + ("  (dry run — nothing written)" if a.dry_run else
             f"  reviewed_by={a.reviewed_by}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
