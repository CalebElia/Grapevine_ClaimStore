"""Bind each document row to the exact bytes it was converted from.

WHY THIS IS A GATE AND NOT A CONVENIENCE. A claim in this store is a character range in a
canonical text derived from a markdown file derived from a PDF. Every layer above the PDF
is reproducible and hashed; the PDF itself was, until now, a filename nobody recorded. That
makes the whole chain unverifiable at its root: the store could not answer "which document
did you read", only "some document that produced this markdown".

Two byte-identical copies of each report sit on this machine, one of them in a directory
literally named "docling-test copy". They agree today. The store had no way to notice if
they stopped agreeing, and no way to say which one it had read.

THE MATCH IS PROVEN, NOT ASSUMED. Filenames encode a year and the store holds a title, and
neither is evidence -- AA_AnnualReport_2024.pdf and AA_AnnualReport_2025.pdf both have 24
pages, so a page count alone cannot separate them either. So this refuses to write unless
the PDF's page count equals documents.page_count AND its first page corroborates the stored
title. Binding a claim store to the wrong source document is worse than leaving it unbound,
because an unbound store is visibly unbound.

WHAT WE KNOW AND WHAT WE DO NOT. We know the bytes: the hash is computed here and is exact.
We do NOT know when the file was retrieved -- the filesystem mtime records when it was
COPIED to this machine -- and we do not know the URL. So retrieved_at stays NULL and
snapshot_source says `local_file`, rather than dressing a copy operation up as a download.
Migration 007 set this precedent for coverage periods: an inferred value must say so.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# How much of the first page's text must corroborate the stored title. The titles are not
# identical strings -- the store holds "YEAR THREE ANNUAL REPORT" where the PDF renders
# "Year three Annual Report" -- so this compares alphanumerics, case-folded.
_TITLE_PREFIX = 12


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while (b := f.read(chunk)):
            h.update(b)
    return h.hexdigest()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@dataclass
class Candidate:
    document_id: int
    pdf: Path
    pages: int
    stored_pages: int | None
    stored_title: str
    first_page: str
    sha256: str

    @property
    def pages_agree(self) -> bool:
        return self.stored_pages is not None and self.pages == self.stored_pages

    @property
    def title_agrees(self) -> bool:
        """The stored title's opening characters appear on the PDF's first page."""
        want = _key(self.stored_title)[:_TITLE_PREFIX]
        return bool(want) and want in _key(self.first_page)

    @property
    def ok(self) -> bool:
        return self.pages_agree and self.title_agrees

    @property
    def why_not(self) -> str:
        if self.stored_pages is None:
            return "documents.page_count is NULL — nothing to check the PDF against"
        if not self.pages_agree:
            return f"page count {self.pages} != stored {self.stored_pages}"
        if not self.title_agrees:
            return f"stored title {self.stored_title[:40]!r} not found on page 1"
        return ""


def inspect(document_id: int, pdf: Path, dsn: str = DSN) -> Candidate:
    import pdfplumber
    import psycopg

    with psycopg.connect(dsn) as c:
        row = c.execute("SELECT page_count, title FROM documents WHERE id=%s",
                        (document_id,)).fetchone()
    if row is None:
        raise SystemExit(f"no document {document_id}")
    with pdfplumber.open(str(pdf)) as d:
        pages = len(d.pages)
        first = d.pages[0].extract_text() or ""
    return Candidate(document_id, pdf, pages, row[0], row[1] or "", first,
                     sha256_file(pdf))


def record(cand: Candidate, dsn: str = DSN, dry_run: bool = False,
           source_url: str | None = None, because: str | None = None) -> bool:
    """Write the binding. Refuses unless the PDF proves it is this document.

    THE OVERRIDE EXISTS BECAUSE THE GUARD IS RIGHT, NOT BECAUSE IT IS WRONG. Year 2's first
    page carries no text layer at all -- it is the 96%-OCR report -- so the title can never
    be corroborated that way, however many times this runs. That is not a check to relax
    corpus-wide; it is one document whose evidence has to come from somewhere else, stated
    by a person and written into the row. A silent `--force` would make every future
    binding unfalsifiable to save one argument today.
    """
    import psycopg

    if not cand.ok and not because:
        return False
    if dry_run:
        return True
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            """UPDATE documents
                  SET snapshot_path=%s, snapshot_hash=%s, snapshot_source='local_file',
                      snapshot_note=%s, is_mutable_source=FALSE,
                      source_url=COALESCE(%s, source_url)
                WHERE id=%s""",
            (str(cand.pdf), cand.sha256,
             (f"Bound by page count ({cand.pages}) and first-page title match. "
              if cand.ok else
              f"HUMAN OVERRIDE — automatic check failed ({cand.why_not}). Reason given: "
              f"{because} ")
             + "retrieved_at is NULL because no retrieval was recorded: the file was "
               "already on disk and its mtime is when it was copied here, not downloaded.",
             source_url, cand.document_id))
        c.commit()
    return True


def verify(dsn: str = DSN) -> list[dict]:
    """Re-hash every bound document and report drift.

    A hash written once and never re-read is decoration. This is the check that makes the
    binding mean something: it turns "we recorded which bytes we read" into "the bytes are
    still the ones we read". Missing files count as drift -- a snapshot_path pointing at
    nothing is exactly as unverifiable as a changed file, and quieter about it.
    """
    import psycopg

    out = []
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT id, snapshot_path, snapshot_hash FROM documents "
            "WHERE snapshot_path IS NOT NULL ORDER BY id").fetchall()
    for did, path, want in rows:
        p = Path(path)
        if not p.exists():
            out.append({"document_id": did, "state": "MISSING", "path": path})
            continue
        got = sha256_file(p)
        out.append({"document_id": did, "path": path,
                    "state": "ok" if got == want else "CHANGED",
                    "expected": want, "actual": got})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="bind documents to their source PDFs")
    ap.add_argument("--document-id", type=int)
    ap.add_argument("--pdf")
    ap.add_argument("--verify", action="store_true",
                    help="re-hash every bound document and report drift")
    ap.add_argument("--source-url", help="only if you actually know it; never a guess")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--because", help="bind despite a failed check, stating why; "
                                      "the reason is written into snapshot_note")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.verify:
        res = verify(a.dsn)
        bad = [r for r in res if r["state"] != "ok"]
        for r in res:
            mark = "  " if r["state"] == "ok" else "**"
            print(f"  {mark} doc {r['document_id']:<3} {r['state']:<8} "
                  f"{Path(r['path']).name}")
            if r["state"] == "CHANGED":
                print(f"       expected {r['expected'][:20]}…  actual {r['actual'][:20]}…")
        print(f"[provenance] {len(res)-len(bad)} of {len(res)} documents match their "
              f"recorded snapshot")
        return 1 if bad else 0

    if a.document_id is None or not a.pdf:
        ap.error("--document-id and --pdf are required unless --verify")
    cand = inspect(a.document_id, Path(a.pdf), a.dsn)
    mark = "OK" if cand.ok else "REFUSED"
    print(f"  doc {cand.document_id}  {cand.pdf.name}  pages={cand.pages}"
          f"  sha256={cand.sha256[:16]}…  {mark}")
    if not cand.ok:
        print(f"    {cand.why_not}")
        if not a.because:
            return 1
        print(f"    OVERRIDE: {a.because}")
    record(cand, a.dsn, a.dry_run, a.source_url, a.because)
    print("    bound" + ("  (dry run — nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
