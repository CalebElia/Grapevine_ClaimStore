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


# A heading line, MEASURED against every standalone short line in the real Year 5
# document, not guessed. Every genuine heading found -- "CLOSING", "STRATEGY 2:
# BENEFICIAL", "1: 100% RENEWABLES" -- is entirely uppercase (digits/%/: allowed).
# An earlier version matched "short + capitalised + no terminal punctuation" instead,
# which also matches the FIRST LINE of an ordinary wrapped sentence ("This is a
# perfectly ordinary sentence that happens to wrap") -- caught by its own test before
# being trusted. Requiring the whole line to be uppercase is what ordinary sentence-case
# prose can never satisfy, wrapped or not.
_HEADING = re.compile(r"^[A-Z0-9][A-Z0-9 ,:%&'\-]{1,58}$")


# Below this, a document of ordinary report length is implausibly heading-free -- more
# likely the heading convention isn't ALL CAPS on this document and the heuristic is
# silently doing nothing. Measured on the real Year 5 document: 44 regex matches across
# ~7,100 words. That count is inflated (it also catches Table-of-Contents entries, each
# heading repeated as a sub-header, and bare residual page-number digits, which trivially
# satisfy a single-character match) and should not be read as "44 real headings" -- but
# the inflation only matters for precision, not for this check's purpose, which is
# telling "roughly zero" apart from "plausible". The threshold is set far enough under
# even a conservative real-heading estimate (~15) that it won't false-fire on a document
# merely light on section breaks.
_MIN_HEADING_DENSITY = 1 / 2000  # headings per word


def _paragraphs(t: str) -> tuple[list[str], int]:
    """Split on blank-line breaks AND heading lines, so neither can fuse with adjacent
    prose. A heading has no ".!?" and is short; a real sentence fragment from mid-page
    line-wrapping either ends in punctuation or is not a self-contained line at all.

    THE BUG THIS REPLACES. The previous version ran `t.split()` across the WHOLE
    document before splitting on sentence punctuation, which discards every newline —
    including the ones separating a page footer, a section heading, and the paragraph
    that follows it. Measured on the real Year 5 output: pdfplumber's raw text is
    "...household\nlevel. 23\n\nCLOSING\nA2ZERO is our community's plan..." -- correctly
    structured, with the heading on its own line -- and the old flattening step turned
    that into "23 CLOSING A2ZERO is our community's plan..." for the reviewer to read,
    making a real pdfplumber page-number defect look like a lost-heading defect too.

    THE LIMIT THIS DOES NOT SOLVE. _HEADING requires an all-caps line, because every
    real heading measured in this corpus is all-caps. A Title Case report ("Closing
    Remarks") will silently match NOTHING -- confirmed directly: _HEADING.match("Closing
    Remarks") is False, _HEADING.match("CLOSING REMARKS") is True. On such a document
    this function degrades exactly back to the bug it replaces, with no exception raised.
    The general fix is font-size/weight from pdfplumber's own page.chars, which every
    typeset heading uses regardless of case convention -- deferred, because CU is now the
    intended structure source for the real pipeline (its heading detection already beat
    pdfplumber's own on this test) and building a parallel font-metadata detector for a
    role pdfplumber is being retired from is not worth it right now.
    So instead of a better heuristic: a COUNT, returned alongside the paragraphs, so the
    caller can refuse to trust a suspiciously heading-free result rather than silently
    ship it. See _MIN_HEADING_DENSITY and its use in build_rows().
    """
    out: list[str] = []
    n_headings = 0
    for block in re.split(r"\n\s*\n+", t):          # real paragraph / page gaps
        buf: list[str] = []
        for ln in block.split("\n"):
            s = ln.strip()
            if s and _HEADING.match(s) and not re.search(r"[.!?]$", s):
                if buf:
                    out.append(" ".join(buf))
                    buf = []
                n_headings += 1
                # the heading itself is not a prose sentence -- do not emit it
            else:
                buf.append(ln)
        if buf:
            out.append(" ".join(buf))
    return out, n_headings


def _sentences(t: str, lo=60, hi=250) -> list[str]:
    paras, _ = _paragraphs(t)
    out = []
    for para in paras:
        for s in re.split(r"(?<=[.!?])\s+", para):
            flat = " ".join(s.split())          # collapse WITHIN-paragraph line wraps only
            if lo <= len(flat) <= hi:
                out.append(flat)
    return out


class HeadingDetectionUnreliable(RuntimeError):
    """Raised when a document is long enough to expect headings but the all-caps
    heuristic found none -- see _paragraphs()'s docstring for exactly what this heuristic
    does and does not generalize to. Silently proceeding here reproduces the bug this
    module was written to fix, on the next document that happens to use Title Case.
    """


def check_heading_density(text: str, label: str = "document") -> int:
    """Refuse to trust a suspiciously heading-free result rather than ship it quietly.

    See _MIN_HEADING_DENSITY for the measurement this threshold is set against. It does
    not fire on documents that are merely light on section breaks -- only on the "found
    approximately zero headings in a real report" case that means the heuristic itself
    is not matching this document's convention.
    """
    _, n = _paragraphs(text)
    words = len(text.split())
    if words > 1500 and n < words * _MIN_HEADING_DENSITY:
        raise HeadingDetectionUnreliable(
            f"{label}: {n} all-caps heading(s) detected across {words:,} words -- "
            f"implausibly low for a report this length. The heading heuristic in "
            f"_paragraphs() requires ALL CAPS and this document likely uses a different "
            f"convention (e.g. Title Case), meaning headings are silently fusing with "
            f"body text again. Do not trust this workbook's Disagreement/Control rows "
            f"until this is resolved -- either the document's real heading style, or a "
            f"font-metadata-based detector (see _paragraphs() docstring).")
    return n


def build_rows(arms: dict[str, str], page_of, seed: int = 7, n_control: int = 12,
              require_headings: bool = True) -> list[dict]:
    """arms: {label: text}. `page_of` maps a char offset in the reference arm to a page.

    require_headings=False skips check_heading_density() -- for a document already
    KNOWN not to use ALL CAPS headings, where the workbook is still useful (Disagreement
    and Control sections do not depend on heading detection at all) and the caller is
    choosing to accept degraded paragraph splitting rather than block on it.
    """
    ref_label = "pdfplumber" if "pdfplumber" in arms else sorted(arms)[0]
    ref = arms[ref_label]
    if require_headings:
        check_heading_density(ref, ref_label)
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
