"""A second, independent read of an OCR-heavy document — so a human need not read it all.

THE PROBLEM THIS SOLVES. Year 2's numbers exist in no text layer: 234 extractable words
across 14 pages, ZERO dollar figures, while the conversion carries 15. Every one of them
came from OCR, and `parse_audit.conservation()` — the check that catches figures vanishing
between two reads — has nothing to compare against. No machine verdict will ever reach
`clean`, so without this the only way such a document enters the store is a person reading
3,400 words against the rendered PDF, for every OCR-heavy document, forever.

CORROBORATION IS INDEPENDENCE, NOT COUNT. The same rule the claim store applies to
attestations applies to reads. A human re-reading the same conversion adds nothing; a second
OCR engine reading the same PIXELS is genuinely independent evidence about what the page
says. Azure Content Understanding is that second engine, and it agrees with Docling's OCR on
27 of 27 quantities in Year 2 — 15 money, 4 percent, 8 unit — which is a far stronger warrant
than "someone looked at it".

IT DOES NOT CORRECT, AND THAT IS DELIBERATE. Neither engine is authoritative. On Year 2 the
primary read produced "March 215, 2022" where CU read "March 21st, 2022" — the primary
corrupted a date — while CU leaked a GUID into its own prose elsewhere. A module that
resolved these would be picking a winner between two unreliable narrators. So agreement
earns corroboration and disagreement earns a SHORT, SPECIFIC list: on Year 2 that is three
words across ten sections, instead of 3,400 words of re-reading.

"March 215, 2022" is the case that justifies the whole thing. It is not a garbled word a
reader would catch — it is a plausible date with one extra digit, and a wrong date breaks
timeline analysis silently rather than raising a NULL that screams.

COMPARED ON CANONICAL TEXT, NEVER ON THE FILE. The renderer writes its own OCR percentage
into a header comment, so comparing raw markdown reports "96%" and "98%" as figures the
second engine failed to corroborate — the pipeline's own scaffolding manufacturing conflicts
about itself. canonical.build() is the coordinate space that excludes it by construction.

A FIGURE IS CORROBORATED DOCUMENT-WIDE, NOT SECTION-LOCALLY. The two engines section a
document differently, so requiring a figure to appear in the SAME section in both would
report sectioning differences as OCR conflicts. The question this answers is "did a second
engine read these characters", which is about the glyphs. WHERE a figure belongs is the
reading-order machinery's job and is not re-litigated here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from pipeline.canonical import build, sections
from pipeline.parse_audit import MONEY, PERCENT, UNIT_NUM

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Above this share of OCR-derived blocks, a document REQUIRES a second read. Measured, not
# picked: Years 1, 3, 4 and 5 are 0% and Year 2 is 96%, so anything in between is untested
# by this corpus. Set low deliberately — a document that is a third OCR still has figures
# nothing can check, and the cost of a second read is seconds.
OCR_SECOND_READ_THRESHOLD = 20

_WORD = re.compile(r"[a-z0-9$%]+")


def _norm(x: str) -> str:
    """Whitespace and thousands separators are typography; digits are not."""
    return re.sub(r"[\s,]", "", x.strip().lower())


def figures(text: str) -> dict[str, set[str]]:
    return {"money": {_norm(m) for m in MONEY.findall(text)},
            "percent": {_norm(m) for m in PERCENT.findall(text)},
            "unit": {_norm(m) for m in UNIT_NUM.findall(text)}}


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def compare(primary_md: str, second_md: str) -> dict:
    """Per-section agreement between two independent reads of the same pages."""
    canon = build(primary_md)
    second_words = words(second_md)
    second_figs = figures(second_md)

    out = []
    for s in sections(canon):
        body = canon.text[s["char_start"]:s["char_end"]]
        fig_conflicts: list[str] = []
        n_fig = 0
        for kind, got in figures(body).items():
            n_fig += len(got)
            for f in sorted(got - second_figs[kind]):
                fig_conflicts.append(f"{kind}:{f}")
        missing = sorted(words(body) - second_words)
        out.append({
            "sequence": s["sequence"], "heading": s["heading"],
            "content_hash": s["content_hash"],
            "figures": n_fig, "figure_conflicts": fig_conflicts,
            "words": len(words(body)), "word_conflicts": missing,
            # BOTH must be clean. A section whose prose agrees but whose figures do not is
            # the dangerous case, not the reassuring one: the figures are what claims carry.
            "corroborated": not fig_conflicts and not missing,
        })
    return {"ocr_pct": canon.ocr_pct, "dominant": canon.dominant_text_source,
            "sections": out}


def apply(document_id: int, result: dict, second_read_by: str, dsn: str = DSN,
          dry_run: bool = False) -> list[tuple]:
    """Record corroboration on parse_flags, matched by content_hash.

    Matched on hash rather than sequence because that is the value that identifies THIS
    text: a corroboration recorded against a section whose conversion later moved would be
    a claim about words that are no longer there.
    """
    import psycopg

    changed = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for s in result["sections"]:
            cur.execute("SELECT id, parse_flags FROM document_sections "
                        "WHERE document_id=%s AND content_hash=%s",
                        (document_id, s["content_hash"]))
            row = cur.fetchone()
            if not row:
                continue
            sid, flags = row
            flags = dict(flags or {})
            if s["corroborated"]:
                flags["ocr_corroborated"] = second_read_by
                flags.pop("ocr_conflicts", None)
            else:
                flags.pop("ocr_corroborated", None)
                flags["ocr_conflicts"] = (s["figure_conflicts"] + s["word_conflicts"])[:20]
            changed.append((sid, s["sequence"], s["corroborated"]))
            if not dry_run:
                # A section whose corroboration changed must be re-audited: its verdict was
                # reached on the old evidence.
                cur.execute("UPDATE document_sections SET parse_flags=%s, "
                            "parse_confidence='unaudited' WHERE id=%s",
                            (json.dumps(flags), sid))
        if not dry_run:
            c.commit()
    return changed


def needs_second_read(dsn: str = DSN) -> list[dict]:
    """Documents whose OCR share demands a second read but have none recorded."""
    import psycopg

    with psycopg.connect(dsn) as c:
        rows = c.execute(
            """SELECT d.id, d.title, d.markdown_path,
                      count(*) FILTER (WHERE s.parse_flags ? 'has_ocr_blocks') AS ocr_secs,
                      count(*) FILTER (WHERE s.parse_flags ? 'ocr_corroborated') AS done,
                      count(*) AS total
                 FROM documents d JOIN document_sections s ON s.document_id = d.id
             GROUP BY d.id, d.title, d.markdown_path
               HAVING count(*) FILTER (WHERE s.parse_flags ? 'has_ocr_blocks') > 0
             ORDER BY d.id""").fetchall()
    return [{"document_id": a, "title": b, "markdown_path": c_,
             "ocr_sections": d, "corroborated": e, "sections": f}
            for a, b, c_, d, e, f in rows]


def report(res: dict) -> None:
    ok = [s for s in res["sections"] if s["corroborated"]]
    print(f"\n  {res['ocr_pct']}% OCR (dominant: {res['dominant']})")
    print(f"  {'seq':>4} {'figs':>5} {'words':>6}  state")
    print("  " + "-" * 74)
    for s in res["sections"]:
        state = "corroborated" if s["corroborated"] else "CONFLICT"
        print(f"  {s['sequence']:>4} {s['figures']:>5} {s['words']:>6}  {state:<13}"
              f"{(s['heading'] or '(front matter)')[:34]}")
        for cflt in s["figure_conflicts"]:
            print(f"         !! figure not in second read: {cflt}")
        for w in s["word_conflicts"]:
            print(f"         ?  word not in second read: {w!r}")
    print(f"\n  {len(ok)} of {len(res['sections'])} sections corroborated; "
          f"{len(res['sections']) - len(ok)} need a human on the listed items only")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="corroborate an OCR-heavy conversion against a second independent read")
    ap.add_argument("--document-id", type=int)
    ap.add_argument("--primary", help="the ingested markdown")
    ap.add_argument("--second", help="a second read of the same PDF (e.g. convert_cu output)")
    ap.add_argument("--second-read-by", default="azure_content_understanding")
    ap.add_argument("--pending", action="store_true",
                    help="list OCR-heavy documents with no second read recorded")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.pending:
        rows = needs_second_read(a.dsn)
        for r in rows:
            gap = r["ocr_sections"] - r["corroborated"]
            mark = "OK " if gap == 0 else "** "
            print(f"  {mark}doc {r['document_id']:<3} {r['corroborated']}/{r['ocr_sections']}"
                  f" OCR sections corroborated  {r['title'][:38]}")
        print(f"[corroborate] {sum(1 for r in rows if r['ocr_sections'] > r['corroborated'])}"
              f" document(s) still need a second read")
        return 0

    if not (a.primary and a.second):
        ap.error("--primary and --second are required unless --pending")
    res = compare(Path(a.primary).read_text(), Path(a.second).read_text())
    report(res)
    if a.document_id:
        ch = apply(a.document_id, res, a.second_read_by, a.dsn, a.dry_run)
        print(f"  {sum(1 for _, _, ok in ch if ok)} section(s) flagged corroborated, "
              f"{sum(1 for _, _, ok in ch if not ok)} flagged with conflicts"
              + ("  (dry run — nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
