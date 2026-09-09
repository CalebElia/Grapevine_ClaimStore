"""A hand-prepared wiki markdown -> the coordinate space ingest reads.

WHY THIS EXISTS, AND WHAT IT IS NOT. The a2zero-wiki holds six documents a human already
converted and validated. Their text is the quality standard this pipeline is measured
against, and for a document that already has one there is no reason to prefer a machine
reading of the same PDF. This module does the small mechanical work that makes such a file
ingestable; it does NOT touch a word of it.

Three differences from what canonical.py expects, all of them format rather than content:

  * the page is written inline as "[Source: Page 12]", which canonical.py cannot see as a
    page marker and WILL see as prose -- 1,351 of them landing inside claim verbatims;
    they become `<!-- p.N -->` comments, emitted where the page CHANGES and not per block;
  * only 55% of blocks carry one at all; and
  * a YAML frontmatter block sits at the top, which parses as a paragraph of document text.

THE CARRY-FORWARD IS SOUND BECAUSE THE MARKERS ARE ORDERED. Measured on cap-2020: 1,355
markers, of which exactly one goes backwards. A block with no marker therefore belongs to
the last page named before it, and the worst a mistake can do is put a claim on an adjacent
page. That is an inference and it is recorded as one -- `page_source` in the sidecar says
"explicit" or "inferred" for every block, so a reviewer can tell which citations the
document asserted and which this module worked out.

The frontmatter is KEPT, as a comment. It carries the uuid, the title and
covers-period-start/end, which is exactly the metadata documents.covers_period_start exists
to hold, and the wiki lost a report's period once by treating such a header as decoration.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# "[Source: Page 12]", with any run of whitespace before it, anywhere in a block.
_SOURCE = re.compile(r"[ \t]*\[Source:\s*Page\s+(\d+)\]")
_FRONTMATTER = re.compile(r"\A\s*---\s*\n(?P<body>.*?)\n---\s*\n", re.S)
_SCALAR = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):\s*(?P<val>.*?)\s*$")


def split_frontmatter(md: str) -> tuple[dict, str]:
    """(metadata, body). A file with no frontmatter returns ({}, md) unchanged.

    Deliberately a flat scalar reader rather than a YAML parse: the only thing needed is the
    handful of top-level keys the wiki writes, and adding a dependency to read six lines
    would be the more surprising choice. A value this cannot read is skipped rather than
    guessed at, and the raw block is preserved verbatim in the output either way.
    """
    m = _FRONTMATTER.match(md)
    if not m:
        return {}, md
    meta: dict[str, str] = {}
    for line in m.group("body").splitlines():
        s = _SCALAR.match(line)
        if not s:
            continue
        # AFTER unquoting, not before. `ingest_date: ""` has a truthy raw value of two
        # quote characters and an empty real one, and storing "" would look like an answer
        # to a question the file explicitly left open.
        val = s.group("val").strip().strip('"').strip("'")
        if val:
            meta[s.group("key")] = val
    return meta, md[m.end():]


# The superscript in A²ZERO is drawn as a separate glyph, and a transcription that loses it
# leaves the 2 stranded between spaces. Confirmed against the PDF: page 13 prints "A²ZERO
# VALUES" where the prepared file reads "A 2 ZERO". Restoring it is a fidelity REPAIR, not a
# normalisation -- the other spellings the document really uses (A²ZERO 94x, A2ZERO 39x,
# A2Zero 3x, with page 14 printing two of them within two lines) are left exactly as they are.
_SPACED_A2ZERO = re.compile(r"\bA 2 (ZERO|Zero)\b")


def fix_spaced_superscript(md: str) -> tuple[str, int]:
    """(text, replacements). "A 2 ZERO" -> "A²ZERO", nothing else touched."""
    out, n = _SPACED_A2ZERO.subn(lambda m: "A²" + m.group(1), md)
    return out, n


def lift_heading_markers(md: str) -> tuple[str, int]:
    """Move a page marker that follows a heading to just BEFORE it. Returns (text, moved).

    A HEADING IS PRINTED ABOVE THE CONTENT IT INTRODUCES, so it belongs to the page that
    content starts on. The prepared wiki files write "[Source: Page 22]" at the end of the
    BODY block and never on the heading, so carry-forward puts the heading on the previous
    page and its own text on the next -- on cap-2020, 54 headings including every Action:

        ## Implement Community Choice Aggregation      <- inherits p.21
        <!-- p.22 -->
        Community Choice Aggregation (CCA) program...  <- p.22

    Both belong to page 22. Swapping the two blocks is the whole fix, and it moves no words:
    a marker is a comment, which canonical.py drops from the canonical text entirely.
    """
    blocks = md.split("\n\n")
    moved = 0
    i = 0
    while i < len(blocks) - 1:
        head, nxt = blocks[i].strip(), blocks[i + 1].strip()
        if head.startswith("#") and re.fullmatch(r"<!-- p\.\d+ -->", nxt):
            blocks[i], blocks[i + 1] = blocks[i + 1], blocks[i]
            moved += 1
            i += 2                      # do not re-examine the marker we just moved up
            continue
        i += 1
    return "\n\n".join(blocks), moved


def convert(md: str) -> tuple[str, list[dict]]:
    """(markdown with page-marker comments, one record per emitted block).

    Every block gets a marker, so canonical.py assigns every unit a page. The record says
    where that page came from, which is the part a reviewer needs and the markdown has
    nowhere to put.
    """
    meta, body = split_frontmatter(md)
    out: list[str] = []
    records: list[dict] = []

    if meta:
        # Machine-readable, and OUT of the prose. canonical.py drops comments from the
        # canonical text, so this cannot reach a claim's verbatim.
        out.append("<!-- wiki frontmatter: "
                   + "; ".join(f"{k}={v}" for k, v in meta.items()) + " -->")

    page: int | None = None
    emitted: int | None = None
    for raw in body.split("\n\n"):
        block = raw.strip("\n")
        if not block.strip():
            continue
        found = _SOURCE.findall(block)
        explicit = bool(found)
        if explicit:
            # The FIRST marker in the block. A block carrying two is a block whose text
            # spans a page break, and its opening words are the ones on the earlier page.
            page = int(found[0])
        cleaned = _SOURCE.sub("", block).rstrip()
        if not cleaned.strip():
            continue          # a block that was nothing but its own marker
        # ONLY WHEN THE PAGE CHANGES, which is the convention render_blocks already uses
        # (mark_page emits nothing while pending_page == page). canonical.py keeps a running
        # page that a marker SETS and every later unit inherits, so a marker per block is
        # pure redundancy: on cap-2020 it produced 1,190 markers where 100 carry the same
        # information, and it doubled the file a human has to hand-correct. The unit text,
        # the unit-to-page mapping and the canonical text are identical either way.
        if page is not None and page != emitted:
            out.append(f"<!-- p.{page} -->")
            emitted = page
        records.append({
            "page_no": page,
            "page_source": "explicit" if explicit else "inferred",
            "n_markers": len(found),
            "text": cleaned,
        })
        out.append(cleaned)

    # A HEADING TAKES THE PAGE OF WHAT IT INTRODUCES. Applied here rather than inside the
    # loop because it is a property of the rendered sequence, and the same function repairs
    # a file that has already been hand-edited.
    text, _ = lift_heading_markers("\n\n".join(out))
    return text + "\n", records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", required=True, help="the prepared wiki markdown (read only)")
    ap.add_argument("--out", required=True, help="where to write the converted markdown")
    ap.add_argument("--sidecar", help="where to write the per-block page provenance JSON")
    a = ap.parse_args()

    src = Path(a.md)
    text, records = convert(src.read_text())
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)

    meta, _ = split_frontmatter(src.read_text())
    if a.sidecar:
        Path(a.sidecar).write_text(json.dumps(
            {"source": str(src), "frontmatter": meta, "blocks": records}, indent=1))

    exp = sum(1 for r in records if r["page_source"] == "explicit")
    pages = sorted({r["page_no"] for r in records if r["page_no"] is not None})
    print(f"[prepare] {len(records)} blocks -> {a.out}")
    print(f"[prepare]   {exp} explicit page markers, {len(records) - exp} inferred "
          f"by carry-forward")
    print(f"[prepare]   pages named: {len(pages)} "
          f"({pages[0] if pages else '-'}..{pages[-1] if pages else '-'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
