"""Load a document's footnotes and the prose references that point at them.

THE REFERENCE IS RECOVERED, NOT READ, and the store says so. On an OCR-only document the
superscript never survives as a numeral: Docling rendered Year 2's seven as a stray letter
("wet"), as an apostrophe ("we'"), or as nothing at all. The digit comes from a second
independent read of the same pixels. marker_evidence records that, because a citation whose
provenance the store cannot explain is one nobody should trust.

THE MARKER CHARACTER IS GONE AND THE POSITION IS NOT. The stray letter was OCR noise rather
than a word and has been removed from the prose; where it stood is still where the citation
was made, so char_at keeps it.
"""
from __future__ import annotations

import argparse
import os
import re

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")


def _section_of(offset: int, sections: list[tuple]) -> int | None:
    """The section id whose char range contains this offset."""
    for sid, a, b in sections:
        if a <= offset < b:
            return sid
    return None


def load(document_id: int, second_md: str | None = None, dsn: str = DSN,
         dry_run: bool = False) -> dict:
    import psycopg

    from pipeline.canonical import build

    with psycopg.connect(dsn) as c:
        md_path = c.execute("SELECT markdown_path FROM documents WHERE id=%s",
                            (document_id,)).fetchone()[0]
        sections = c.execute(
            "SELECT id, char_start, char_end FROM document_sections "
            "WHERE document_id=%s ORDER BY sequence", (document_id,)).fetchall()

    canon = build(open(md_path).read())
    second = open(second_md).read() if second_md else ""

    bodies = [u for u in canon.units if u.kind == "footnote"]
    counts = {"footnotes": 0, "references": 0, "unreferenced": 0}

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for u in bodies:
            n = u.flags["footnote_number"]
            sid = _section_of(u.char_start, sections)
            if not dry_run:
                cur.execute(
                    """INSERT INTO footnotes
                         (document_id, number, body_text, page_no, char_start, char_end,
                          document_section_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (document_id, number) DO UPDATE
                         SET body_text=EXCLUDED.body_text, char_start=EXCLUDED.char_start,
                             char_end=EXCLUDED.char_end
                       RETURNING id""",
                    (document_id, n, u.text, u.page_no, u.char_start, u.char_end, sid))
                fid = cur.fetchone()[0]
            else:
                fid = None
            counts["footnotes"] += 1

            # THE REFERRING BLOCK. The marker stood immediately before the colon of the
            # lead-in line -- "In Year Two, we⁴:" -- and the second arm is what tells us
            # which number it was.
            # SCOPED TO THE FOOTNOTE'S OWN SECTION. Once the markers were corrected, six
            # of the seven lead-in lines read identically ("In Year Two, we:"), so a
            # document-wide search matched the first one for every footnote and gave all
            # six the same char_at. The body sits at the foot of the page whose prose cites
            # it, so the section is the correct scope -- and it is what makes the reference
            # a fact about this section rather than about the first line that looked right.
            ref = None
            for v in canon.units:
                if v.kind != "para" or ":" not in v.text:
                    continue
                if _section_of(v.char_start, sections) != sid:
                    continue
                stem = v.text.rstrip()[:-1].rstrip()
                if not re.search(r"\bwe$", stem):
                    continue
                if not second or not re.search(rf"{re.escape(stem)}\s*{n}\s*:", second):
                    continue
                ref = v
                break
            if ref is None:
                counts["unreferenced"] += 1
                continue
            at = ref.char_start + ref.text.rindex(":")
            if not dry_run:
                cur.execute(
                    """INSERT INTO footnote_references
                         (footnote_id, document_section_id, char_at, marker_evidence)
                       VALUES (%s,%s,%s,'second_read')
                       ON CONFLICT (footnote_id, document_section_id) DO NOTHING""",
                    (fid, _section_of(ref.char_start, sections), at))
            counts["references"] += 1
        if not dry_run:
            c.commit()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="load footnotes and their references")
    ap.add_argument("--document-id", type=int, required=True)
    ap.add_argument("--second", help="a second independent read, for marker recovery")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = load(a.document_id, a.second, a.dsn, a.dry_run)
    print(f"[footnotes] {r['footnotes']} footnote(s) · {r['references']} reference(s) · "
          f"{r['unreferenced']} with no locatable reference"
          + ("  (dry run — nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
