"""The table of contents as structured ground truth.

WHY THIS IS EVIDENCE, NOT INFERENCE. A table of contents is the document declaring its own
hierarchy: which headings exist, in what order, at what depth, on what page. Every other
signal the conversion uses for structure -- font size, Docling's SectionHeaderItem, the shape
of a line -- is a guess at something the document has already written down.

Treating it as CONTENT costs twice. The hierarchy is thrown away, so a paragraph can be
promoted to `##` and a letter's "Friends--" salutation can become a heading; and the
dot-leader lines land in the prose, some as blockquotes and some as plain text.

LEVELS COME FROM INDENTATION, corroborated by font size. Measured on the CAP's pages 2-4:

    x0 76-77, size 12   Welcome Letter, Executive Summary, Introduction
    x0 88-94, size 11   A²ZERO Values, Strategy 3, Strategy 4
    x0 111,   size 10   the 44 named actions

Clustered rather than thresholded, because the columns a document chooses are its own. A plan
with two levels and a plan with four both work without a number in this file.
"""
from __future__ import annotations

import re

# A dot leader is a long run of dots, repeated down the page. Three dots is an ellipsis.
_LEADER = re.compile(r"\.{4,}")

# How many leader-bearing lines make a page a contents page. Measured: the CAP's three
# contents pages carry 25-26 each, and no prose page in the corpus carries more than one.
_MIN_LEADERS = 3

# Indents within this many points are the same column. The CAP's level-1 entries sit at 76
# and 77; its level-2 entries at 88 and 94.
_SAME_COLUMN = 10.0

# Lines are grouped into one visual row within this vertical tolerance.
_ROW_TOL = 4.0


# A heading is short. A page reference is small. Both bound what can look like an entry.
_MAX_TITLE_WORDS = 14
_MAX_PAGE_REF = 999

# What share of a page's lines must look like entries for it to be a contents page.
_MOSTLY_ENTRIES = 0.5

# Below this, a contents list is too short to distinguish a designed layout from a
# real hierarchy, so indentation is taken at face value.
_SCATTER_MIN_ENTRIES = 8


def _ascending_refs(text: str) -> int:
    """How many lines carry a plausible page reference, in ascending order.

    THE SIGNAL BOTH STYLES SHARE. The CAP writes "Welcome Letter ..... 5"; the annual
    reports write "3 INTRODUCTION" with no leader at all. What makes either recognisable
    without reading it is a column of page references that ASCEND beside short headings --
    which a page of statistics does not have, and a numbered list of prose fails because its
    lines are too long to be headings.
    """
    refs: list[int] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{1,3})\b", line) or re.search(r"\b(\d{1,3})$", line)
        if not m:
            continue
        rest = (line[m.end():] if m.start() == 0 else line[:m.start()]).strip(" .")
        if not re.search(r"[A-Za-z]", rest) or len(rest.split()) > _MAX_TITLE_WORDS:
            continue
        n = int(m.group(1))
        if 1 <= n <= _MAX_PAGE_REF:
            refs.append(n)
    return sum(1 for a, b in zip(refs, refs[1:]) if b >= a)


def is_toc_page(text: str | None) -> bool:
    """Whether a page is a table of contents.

    Deliberately not "does it contain the word CONTENTS": a document may title the section
    something else, or not title it at all. Two independent tells, either sufficient -- a run
    of dot leaders, or a column of ascending page references beside short headings.
    """
    if len(_LEADER.findall(text or "")) >= _MIN_LEADERS:
        return True
    lines = [l for l in (text or "").splitlines() if l.strip()]
    n = _ascending_refs(text or "")
    # A CONTENTS PAGE IS MOSTLY ENTRIES. Counting ascending references alone made the CAP
    # report 19 contents pages instead of 3 -- its strategy and action pages carry a handful
    # among prose. Measured: p.2 scores 0.85, p.6 scores 0.15, p.10 scores 0.05.
    return n >= _MIN_LEADERS and n / max(len(lines), 1) >= _MOSTLY_ENTRIES


def qualifies(text: str | None, entries: list) -> bool:
    """A page is a contents page only if it LOOKS like one and NAMES things.

    Shape alone cannot separate a contents list from a numbered appendix list: CAP pages 124
    and 126 read "28. January 21, 2020 - A²ZERO presentation...", ordinals ascending exactly
    like page references. Both yielded zero entries, and a contents page that names nothing
    is not one.

    This matters beyond tidiness: the page list is what marks pages as METADATA rather than
    prose, so a false positive deletes real content.
    """
    return is_toc_page(text) and len(entries) >= _MIN_LEADERS


def _rows(words: list[dict]) -> list[list[dict]]:
    """Words grouped into visual rows, each sorted left to right."""
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= _ROW_TOL:
            out[-1].append(w)
        else:
            out.append([w])
    for r in out:
        r.sort(key=lambda w: w["x0"])
    return out


def parse_toc_words(words: list[dict],
                    page_heading_tops: set | None = None) -> list[dict]:
    """Words on a contents page -> {title, page_ref, x0} per entry.

    A WRAPPED ENTRY IS NOT A NEW ONE. "Strategy 3: Significantly Improve the Energy
    Efficiency in our Homes," continues onto a second line carrying neither leader nor page
    number, and folding it back is what keeps the title whole -- a truncated heading is
    exactly what later fails to match anything.

    A row with no page reference and nothing above it to continue is dropped: that is the
    word CONTENTS at the top of the page, not an entry.
    """
    entries: list[dict] = []
    heads = page_heading_tops or set()
    for row in _rows(words):
        # A CONTENTS PAGE CARRIES ITS OWN FOOTER NUMBER. Read as a row it becomes a nameless
        # entry at the far-left margin, which then defines column 1 and pushes every real
        # entry down a level: "Welcome Letter" arrived as L2.
        if len(row) == 1 and row[0]["text"].strip().isdigit():
            continue
        # The word CONTENTS opens pages 3 and 4. Folded as a wrap it corrupts the entry above
        # it -- which is on the PREVIOUS page -- and invents an indent column.
        if any(abs(row[0]["top"] - h) <= _ROW_TOL for h in heads):
            continue
        page_ref = None
        body = row
        tail, head = row[-1]["text"].strip(), row[0]["text"].strip()
        if tail.isdigit():
            page_ref = int(tail)
            body = row[:-1]
        elif head.isdigit() and len(row) > 1:
            # THE OTHER STYLE. The annual reports put the reference FIRST -- "3 INTRODUCTION"
            # -- so the leading numeral is the page, not part of the title.
            page_ref = int(head)
            body = row[1:]
        title = " ".join(w["text"] for w in body if not _LEADER.fullmatch(w["text"].strip()))
        title = re.sub(r"\s*\.{2,}\s*$", "", title).strip()
        # AN ENTRY NAMES SOMETHING. A row of bare numerals is page furniture however many
        # numerals it carries -- the footer "1" beside a stray "2" parsed as an entry titled
        # "1" on page 2, and its far-left margin then defined indent column one.
        if not re.search(r"[A-Za-z]", title):
            continue
        if page_ref is None:
            if entries:
                entries[-1]["title"] = f"{entries[-1]['title']} {title}".strip()
            continue                      # a page heading, or a wrap with nothing to join
        # THE TITLE'S left edge, never the row's. Where the page reference comes first, the
        # numeral column is the same for every entry, and taking it would collapse all the
        # levels into one.
        entries.append({"title": title, "page_ref": page_ref,
                        "x0": round((body[0] if body else row[0])["x0"])})
    return entries


def assign_levels(entries: list[dict]) -> list[dict]:
    """Add a `level` to each entry by clustering its indent.

    CLUSTERED, NOT THRESHOLDED. The columns a document indents to are its own; a plan with
    two levels and one with four both work without a number written here.
    """
    if not entries:
        return []
    # A COLUMN MUST HOLD MORE THAN ONE ENTRY TO BE A LEVEL. Year 4's contents is arranged
    # around a graphic -- eleven entries at eleven different indents -- and reading that as a
    # hierarchy produced ten levels for eleven entries. A design is not a depth.
    from collections import Counter
    tally: Counter = Counter()
    columns: list[float] = []
    for x in sorted(e["x0"] for e in entries):
        if not columns or x - columns[-1] > _SAME_COLUMN:
            columns.append(x)
        tally[columns[-1]] += 1
    # ...but only where there are enough entries to tell a scatter from a hierarchy. With
    # three entries at three indents, either reading is available and neither is evidenced.
    if len(entries) >= _SCATTER_MIN_ENTRIES:
        columns = [c for c in columns if tally[c] > 1] or [min(e["x0"] for e in entries)]
    for e in entries:
        # An entry can sit LEFT of every surviving column once the scatter guard has dropped
        # the single-occupant ones, in which case it belongs to the shallowest level there
        # is. Without the default this raised on max() of an empty sequence.
        e["level"] = 1 + max((i for i, c in enumerate(columns)
                              if e["x0"] >= c - _SAME_COLUMN / 2), default=0)
    return entries


def extract(pdf_path) -> dict:
    """The whole contents of a PDF: which pages declare it, and what it declares.

    Returns {"pages": [...], "entries": [{title, page_ref, level, x0}]}. `pages` is what
    lets the conversion treat those pages as METADATA rather than prose -- the hierarchy is
    the content of a contents page, and the dot-leader lines are its rendering.
    """
    import pdfplumber

    pages: list[int] = []
    entries: list[dict] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if not is_toc_page(text):
                continue
            ws = page.extract_words()
            # PARSED PER PAGE, so a wrapped entry can never fold into the last entry of the
            # PREVIOUS page -- which is how "CONTENTS" corrupted the entry above it.
            heads = {w["top"] for w in ws if w["text"].strip().upper() == "CONTENTS"}
            found = parse_toc_words(ws, page_heading_tops=heads)
            if not qualifies(text, found):
                continue
            pages.append(pno)
            for e in found:
                e["page_no"] = pno
                entries.append(e)
    return {"pages": pages, "entries": assign_levels(entries)}


def main() -> int:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="extract a document's table of contents as structured ground truth")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True, help="where the toc json lands")
    a = ap.parse_args()

    r = extract(a.pdf)
    Path(a.out).write_text(json.dumps(r, indent=1))
    levels = {}
    for e in r["entries"]:
        levels[e["level"]] = levels.get(e["level"], 0) + 1
    print(f"[toc] {len(r['entries'])} entrie(s) from page(s) "
          f"{', '.join(map(str, r['pages'])) or '—'} · "
          + " · ".join(f"L{k} {v}" for k, v in sorted(levels.items())))
    print(f"[toc] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
