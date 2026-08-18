"""Assemble ONE clean markdown file from the validated pipeline: CU decides section
structure, pdfplumber supplies verbatim text, and a section that can't be located is
marked UNVERIFIED rather than silently cited as clean.

WHAT THIS DOES NOT YET DO. Figure/chart content is not automatically inserted. The vision
extraction path (pipeline/vision_extract.py) is validated -- a 600dpi crop plus the
refined anti-hallucination prompt correctly extracted 31 real data points from the Year 5
dashboard chart, with both test deployments agreeing exactly on every value legible at
that resolution. What is NOT yet built is the wiring that decides WHICH of a document's
embedded images are worth that call: Docling's picture classifier (pipeline/
convert_docling.py) currently reports only a per-DOCUMENT label count, not a per-picture
page/location record, so there is no automatic way to route "this specific image on this
specific page is a bar_chart" into a crop-and-extract step. Measured on Year 5: 32 images
cross the 5%-of-page-area size threshold, and only 1 is a genuine chart -- running vision
on all 32 would be both expensive and wrong, so this module does not do it. The one figure
already fully validated (the Year 5 dashboard, page 5) is included via
`--splice-figure`, as a concrete demonstration of the target shape, not a general solution.

WHY THIS IS FAIL-CLOSED. A section whose pdfplumber anchor could not be located gets
CU's OWN text instead (see section_boundaries.py's cu_body), clearly marked as
unverified. This keeps the document complete and readable while never presenting an
unverified span as if it were citation-grade -- the same rule already applied to chart
data and to unverified transcript spans in the video pipeline.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from pipeline.section_boundaries import Section, build


def render(sections: list[Section], pdf_text: str, title: str,
          figure_blocks: dict[str, str] | None = None) -> str:
    """figure_blocks: {heading_substring: markdown_to_append} -- the --splice-figure path.

    Takes pdf_text directly rather than reading it off each Section, so Section stays a
    lean boundary description (heading, offsets, located flag) with no opinion on how a
    caller wants its body rendered -- this module's business, not section_boundaries.py's.
    """
    figure_blocks = figure_blocks or {}
    lines = [f"# {title}", "", f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
            f"-- structure from Azure CU, text from pdfplumber -->", ""]
    unverified = 0
    for sec in sections:
        lines.append(f"## {sec.heading}")
        lines.append("")
        if sec.located:
            body = pdf_text[sec.pdf_start:sec.pdf_end].strip()
            lines.append(body)
        else:
            unverified += 1
            lines.append("> **[UNVERIFIED SECTION — the pdfplumber anchor for this heading "
                         "was not found, most likely because of column-interleaving or a "
                         "reading-order defect on this page. The text below is CU's own "
                         "reading, not cross-checked against pdfplumber. Do not cite without "
                         "review.]**")
            lines.append("")
            lines.append(sec.cu_body.strip())
        for key, block in figure_blocks.items():
            if key.lower() in sec.heading.lower():
                lines.append("")
                lines.append(block.strip())
        lines.append("")
    if unverified:
        lines.insert(2, f"<!-- {unverified} of {len(sections)} section(s) UNVERIFIED -- "
                        f"see markers below -->")
    return "\n".join(lines)


def orchestrate(pdf_path: str, cu_markdown_path: str, title: str,
                figure_blocks: dict[str, str] | None = None) -> str:
    from pipeline.convert_document import convert
    conv = convert(pdf_path, "pdfplumber")
    cu_text = Path(cu_markdown_path).read_text()
    sections = build(cu_text, conv)
    return render(sections, conv.text, title, figure_blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description="assemble one clean markdown file")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--cu", required=True, help="CU markdown output for the same PDF")
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--splice-figure", nargs=2, action="append", default=[],
                    metavar=("HEADING_SUBSTRING", "XML_FILE"),
                    help="insert a pre-validated figure block after any section whose "
                         "heading contains HEADING_SUBSTRING. Repeatable.")
    a = ap.parse_args()
    blocks = {h: Path(p).read_text() for h, p in a.splice_figure}
    md = orchestrate(a.pdf, a.cu, a.title, blocks)
    Path(a.out).write_text(md)
    print(f"[orchestrate] wrote {a.out} ({len(md.split()):,} words)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
