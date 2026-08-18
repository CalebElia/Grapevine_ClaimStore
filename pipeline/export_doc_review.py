"""Build the human review workbook for a converted document.

WHY A HUMAN READS THIS AT ALL. Every number in the bake-off is an automated proxy, and
during one afternoon of building them FOUR proxies were wrong before they were right: chart
readings scored as hallucinations, a line break scored as a fabrication, a formatting gap
read as infidelity, and a TypeError that made a working capability report as "never fired".
Each was caught by looking at the text, never by another metric. The human is the check on
the checker, and this workbook is how that check gets bounded to something finishable.

THREE SHEETS, AND THE THIRD IS THE ONE PEOPLE SKIP:

  Disagreements   where converters differ. High yield -- the arms have already told us
                  they are unsure. But it only finds errors SOME arm avoided.
  Chart data      figure-derived numerics. Highest fabrication risk in the corpus: Docling
                  returned a GHG table whose six non-electricity columns are identical to
                  one decimal across six years, which real emissions data does not do.
  Control         passages NOTHING flagged, sampled at random. This measures what the
                  automation MISSES. Without it the false-negative rate is unknowable and
                  every other number here reads better than it is.

ROW KEYS ARE CONTENT-DERIVED so the round-trip survives a re-export. Positions shift when
a converter changes; a sha256 of section+text does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

REPO = Path(__file__).parent.parent

COLS = [("#", 5), ("Section", 13), ("Page", 6), ("What to check", 30),
        ("Version A", 52), ("Version B", 52),
        ("✎ Correct?", 12), ("✎ True text", 46), ("✎ Notes", 30), ("row_key", 20)]
HEADER = [c[0] for c in COLS]
COL = {n: i for i, (n, _) in enumerate(COLS, start=1)}

VERDICTS = ["A is right", "B is right", "both wrong", "both fine", "unsure"]


def row_key(section: str, text: str) -> str:
    return hashlib.sha256(f"{section}::{text}".encode()).hexdigest()[:16]


def _sentences(t: str, lo=60, hi=250) -> list[str]:
    return [" ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+", t)
            if lo <= len(s.strip()) <= hi]


def build_rows(arms: dict[str, str], page_of, seed: int = 7, n_control: int = 12) -> list[dict]:
    """arms: {label: text}. `page_of` maps a char offset in the reference arm to a page."""
    ref_label = "pdfplumber" if "pdfplumber" in arms else sorted(arms)[0]
    ref = arms[ref_label]
    rows: list[dict] = []

    # ── Disagreements: reference sentence present in one arm, absent/truncated in another
    others = [k for k in arms if k != ref_label]
    for s in _sentences(ref):
        for o in others:
            flat = " ".join(arms[o].split())
            if s in flat:
                continue
            head = " ".join(s.split()[:6])
            kind = "TRUNCATED in " + o if head in flat else "ABSENT from " + o
            got = ""
            if head in flat:
                i = flat.find(head)
                got = flat[i:i + len(s) + 20]
            rows.append({"section": "Disagreement", "page": page_of(ref.find(s)),
                         "check": kind, "a": s, "b": got or "(not present)"})
            break
    random.Random(seed).shuffle(rows)
    rows = rows[:20]

    # ── Chart data: numerics that appear ONLY in a figure-capable arm
    ref_nums = set(re.findall(r"[0-9][0-9,\.]*", ref))
    for label in others:
        only = [n for n in set(re.findall(r"[0-9][0-9,\.]*", arms[label]))
                if n not in ref_nums and len(n) >= 3][:10]
        for n in only:
            m = re.search(rf"(.{{0,70}}{re.escape(n)}.{{0,70}})", arms[label])
            rows.append({"section": "Chart data", "page": "",
                         "check": f"only {label} reports this — real or fabricated?",
                         "a": (m.group(1) if m else n).strip(), "b": f"(absent from {ref_label})"})

    # ── Control: sentences NOTHING flagged. Measures the false-negative rate.
    flagged = {r["a"] for r in rows}
    clean = [s for s in _sentences(ref)
             if s not in flagged and all(s in " ".join(arms[o].split()) for o in others)]
    for s in random.Random(seed).sample(clean, min(n_control, len(clean))):
        rows.append({"section": "Control", "page": page_of(ref.find(s)),
                     "check": "nothing flagged this — is it actually correct?",
                     "a": s, "b": "(all arms agree)"})
    return rows


def write(rows: list[dict], out: Path, title: str) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    gs = wb.active
    gs.title = "Start here"
    guide = [
        (f"REVIEW: {title}", True),
        ("", False),
        ("Fill in ONLY the ✎ columns. Everything else is machine output and is read back by key.", False),
        ("", False),
        ("HOW TO WORK THIS", True),
        ("1. Open the Review sheet. Compare Version A and Version B against the ORIGINAL PDF.", False),
        ("2. Set ✎ Correct? from the dropdown. That is the minimum useful annotation.", False),
        ("3. If both are wrong, put the true wording in ✎ True text. Quote the PDF exactly.", False),
        ("4. ✎ Notes is free text — anything odd, even if you cannot name it.", False),
        ("", False),
        ("THE THREE SECTIONS DO DIFFERENT JOBS", True),
        ("Disagreement — converters differ here. High yield, but only finds errors some arm avoided.", False),
        ("Chart data   — numbers read out of images. HIGHEST fabrication risk: one converter", False),
        ("               returned six years of emissions data with six columns identical to one", False),
        ("               decimal place, which real data does not do. Treat every row as suspect.", False),
        ("Control      — nothing flagged these. They measure what the automation MISSES.", False),
        ("               Please do not skip them; the false-negative rate is invisible otherwise.", False),
        ("", False),
        ("WHEN DONE", True),
        ("Save the file, then run:", False),
        ("    python -m pipeline.import_doc_review --workbook <this file>", False),
        ("", False),
        ("Your edits are written to a .reviewed.json beside the machine output and NEVER", False),
        ("overwrite it, so a re-run of the converter cannot destroy your work.", False),
    ]
    for i, (txt, bold) in enumerate(guide, start=1):
        c = gs.cell(i, 1, txt)
        if bold:
            c.font = Font(bold=True, size=12 if i == 1 else 11)
    gs.column_dimensions["A"].width = 100

    ws = wb.create_sheet("Review")
    hdr = PatternFill("solid", fgColor="DDDDDD")
    for name, width in COLS:
        c = ws.cell(1, COL[name], name)
        c.font = Font(bold=True)
        c.fill = hdr
        ws.column_dimensions[c.column_letter].width = width
    ws.freeze_panes = "A2"

    tint = {"Disagreement": "FFF6E5", "Chart data": "FDE7E7", "Control": "EAF4EA"}
    for n, r in enumerate(rows, start=2):
        ws.cell(n, COL["#"], n - 1)
        ws.cell(n, COL["Section"], r["section"])
        ws.cell(n, COL["Page"], r.get("page") or "")
        ws.cell(n, COL["What to check"], r["check"])
        ws.cell(n, COL["Version A"], r["a"][:600])
        ws.cell(n, COL["Version B"], r["b"][:600])
        ws.cell(n, COL["row_key"], row_key(r["section"], r["a"]))
        fill = PatternFill("solid", fgColor=tint[r["section"]])
        for name in ("Section", "What to check", "Version A", "Version B"):
            ws.cell(n, COL[name]).fill = fill
            ws.cell(n, COL[name]).alignment = Alignment(wrap_text=True, vertical="top")
        for name in ("✎ Correct?", "✎ True text", "✎ Notes"):
            ws.cell(n, COL[name]).fill = PatternFill("solid", fgColor="FFFFCC")
        ws.row_dimensions[n].height = 46

    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{ws.cell(2, COL['✎ Correct?']).column_letter}2:"
           f"{ws.cell(2, COL['✎ Correct?']).column_letter}{len(rows)+1}")

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="build the document review workbook")
    ap.add_argument("--arm", action="append", required=True, metavar="LABEL=PATH")
    ap.add_argument("--pdf", help="for page numbers (uses the pdfplumber page map)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="document review")
    a = ap.parse_args()

    arms = {}
    for spec in a.arm:
        label, _, path = spec.partition("=")
        arms[label] = Path(path).read_text()

    page_of = lambda off: ""
    if a.pdf:
        from pipeline.convert_document import convert
        conv = convert(a.pdf, "pdfplumber")
        page_of = lambda off: (conv.page_for(off) or "") if off >= 0 else ""

    rows = build_rows(arms, page_of)
    out = write(rows, Path(a.out), a.title)
    counts = {s: sum(1 for r in rows if r["section"] == s) for s in
              ("Disagreement", "Chart data", "Control")}
    print(f"[review] {len(rows)} rows -> {out}")
    for k, v in counts.items():
        print(f"           {k:<14}{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
