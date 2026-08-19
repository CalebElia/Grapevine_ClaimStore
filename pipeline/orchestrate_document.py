"""Assemble ONE clean markdown file from the validated pipeline: CU decides section
structure, pdfplumber supplies verbatim text, Docling's classifier + a vision call supply
figure content, and a section that can't be located is marked UNVERIFIED rather than
silently cited as clean.

FIGURES ARE PLACED BY PAGE, NOT BY HEADING TEXT. An earlier version took
{heading_substring: xml_file} and matched by substring -- workable as a one-off manual
demonstration on a single document whose headings were already known, but it does not
generalize: it required a human to know and type the right heading text per figure. Every
figure extracted via pipeline/extract_figures.py already carries the real signal, page_no,
straight from Docling's own provenance data. A figure belongs wherever pdfplumber's page
map says that page falls -- Section.page_start/page_end, computed once in
section_boundaries.locate() -- so placement is automatic for any document, not just this
one.

A FIGURE ON A PAGE NO SECTION CLAIMS IS NOT DROPPED. If a figure's page falls outside every
located section's page range (a gap, or a page before the first / after the last located
section), it is rendered anyway, under its own heading at the end -- the same fail-closed
instinct as the UNVERIFIED marker: silently losing an extracted figure would look identical
to there having been nothing there.

WHY THE TEXT SIDE IS FAIL-CLOSED. A section whose pdfplumber anchor could not be located
gets CU's OWN text instead (see section_boundaries.py's cu_body), clearly marked as
unverified. This keeps the document complete and readable while never presenting an
unverified span as if it were citation-grade -- the same rule already applied to unverified
transcript spans in the video pipeline.

PAGE MARKERS EXIST FOR THE HUMAN VERIFICATION STEP, WHICH IS THE ONLY REAL GATE. Nothing
downstream can tell whether a verbatim span is FAITHFUL to the PDF -- offsets round-trip
perfectly against text that pdfplumber scrambled. Only a person comparing this file to the
original can catch that, and on this corpus a single section spans up to five pages
(STRATEGY 7: pages 19-23), so an unanchored file makes that comparison a page hunt per
sentence. Each heading carries its page range and each page boundary inside a section
carries a `<!-- p.N -->` marker, so any line can be traced to one page without searching.
The page map this reads was already computed in section_boundaries.locate(); it was simply
being discarded at render time.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pipeline.extract_figures import extract_figures, render_figure_block
from pipeline.section_boundaries import Section, build


def insert_page_markers(text: str, start: int, end: int,
                        page_map: list[tuple[int, int, int]]) -> str:
    """Splice `<!-- p.N -->` into the span [start, end) at each page boundary it contains.

    Boundaries are taken STRICTLY inside the span: a marker at the section's own first
    character would duplicate the page range already on the heading, and one at `end`
    belongs to the next section. Splices back-to-front so each insertion cannot shift the
    offsets of the ones not yet applied -- the ordinary off-by-N trap when editing a
    string at several positions computed against its original form.

    Page boundaries always fall on the PAGE_SEP blank line that convert_document.py joins
    pages with, so a marker lands between blocks and never mid-sentence.
    """
    cuts = sorted(cs for _, cs, _ in page_map if start < cs < end)
    pages = {cs: p for p, cs, _ in page_map}
    body = text[start:end]
    for cs in reversed(cuts):
        rel = cs - start
        body = f"{body[:rel]}<!-- p.{pages[cs]} -->\n{body[rel:]}"
    return body


def render(sections: list[Section], pdf_text: str, title: str,
          figures: list[dict] | None = None,
          page_map: list[tuple[int, int, int]] | None = None) -> str:
    """figures: [{"page_no": int, "block": markdown_str}, ...], one per vision-extracted
    figure. Placed after whichever located section's [page_start, page_end] contains
    page_no; anything that matches no section is rendered at the end, never dropped.

    page_map: when supplied, headings carry their page range and page boundaries inside
    each section body get a `<!-- p.N -->` marker -- see the module docstring on why the
    human verification pass needs this. Optional so render() stays usable (and testable)
    without one.

    Takes pdf_text directly rather than reading it off each Section, so Section stays a
    lean boundary description (heading, offsets, located flag) with no opinion on how a
    caller wants its body rendered -- this module's business, not section_boundaries.py's.
    """
    remaining = list(figures or [])
    lines = [f"# {title}", "", f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
            f"-- structure from Azure CU, text from pdfplumber, figures from Docling + "
            f"vision -->", ""]
    unverified = 0
    for sec in sections:
        pages = ""
        if sec.page_start is not None and sec.page_end is not None:
            pages = (f"  *(page {sec.page_start})*" if sec.page_start == sec.page_end
                     else f"  *(pages {sec.page_start}–{sec.page_end})*")
        lines.append(f"## {sec.heading}{pages}")
        lines.append("")
        if sec.located:
            if page_map:
                body = insert_page_markers(pdf_text, sec.pdf_start, sec.pdf_end, page_map)
            else:
                body = pdf_text[sec.pdf_start:sec.pdf_end]
            lines.append(body.strip())
        else:
            unverified += 1
            lines.append("> **[UNVERIFIED SECTION — the pdfplumber anchor for this heading "
                         "was not found, most likely because of column-interleaving or a "
                         "reading-order defect on this page. The text below is CU's own "
                         "reading, not cross-checked against pdfplumber. Do not cite without "
                         "review.]**")
            lines.append("")
            lines.append(sec.cu_body.strip())
        if sec.page_start is not None and sec.page_end is not None:
            here = [f for f in remaining if sec.page_start <= f["page_no"] <= sec.page_end]
            for f in here:
                lines.append("")
                lines.append(f["block"].strip())
                remaining.remove(f)
        lines.append("")
    if remaining:
        lines.append("## FIGURES NOT MATCHED TO A SECTION")
        lines.append("")
        lines.append("> **[These figures' pages did not fall inside any located section's "
                     "page range. Included here rather than dropped.]**")
        lines.append("")
        for f in remaining:
            lines.append(f["block"].strip())
            lines.append("")
    if unverified:
        lines.insert(2, f"<!-- {unverified} of {len(sections)} section(s) UNVERIFIED -- "
                        f"see markers below -->")
    return "\n".join(lines)


def orchestrate(pdf_path: str, cu_markdown_path: str, title: str,
                figures: list[dict] | None = None, page_markers: bool = True) -> str:
    from pipeline.convert_document import convert
    conv = convert(pdf_path, "pdfplumber")
    cu_text = Path(cu_markdown_path).read_text()
    sections = build(cu_text, conv)
    return render(sections, conv.text, title, figures,
                  page_map=conv.page_map if page_markers else None)


def main() -> int:
    ap = argparse.ArgumentParser(description="assemble one clean markdown file")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--cu", required=True, help="CU markdown output for the same PDF")
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pictures", help="<out>.pictures.json from convert_docling -- if "
                    "given, worth_extraction pictures are cropped and vision-extracted "
                    "automatically before assembly")
    ap.add_argument("--figures-dir", help="where crops + figures.json land (required with "
                    "--pictures)")
    ap.add_argument("--figures-json", help="reuse a figures.json from an earlier run "
                    "instead of re-extracting. Vision calls cost money and are "
                    "non-deterministic; re-rendering the markdown should not silently "
                    "re-run them, nor change the numbers under a review already in "
                    "progress. Mutually exclusive with --pictures.")
    ap.add_argument("--deployment-env", default="GRAPEVINE_DEPLOYMENT_VISION")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--min-area-frac", type=float, default=0.02)
    ap.add_argument("--no-page-markers", action="store_true",
                    help="omit heading page ranges and in-body <!-- p.N --> markers. "
                         "They exist for the human verification pass; turn them off only "
                         "for a machine consumer that wants the text unadorned.")
    a = ap.parse_args()

    if a.pictures and a.figures_json:
        ap.error("--pictures and --figures-json are mutually exclusive: one extracts, "
                 "the other reuses an extraction")

    figures = None
    if a.pictures:
        if not a.figures_dir:
            ap.error("--figures-dir is required with --pictures")
        pictures = json.loads(Path(a.pictures).read_text())
        figs_dir = Path(a.figures_dir)
        raw = extract_figures(Path(a.pdf), pictures, figs_dir, a.deployment_env,
                              dpi=a.dpi, min_area_frac=a.min_area_frac)
        (figs_dir / "figures.json").write_text(json.dumps(raw, indent=2))
        figures = [{"page_no": f["page_no"], "block": render_figure_block(f)} for f in raw]
        print(f"[orchestrate] extracted {len(figures)} figure(s) from {a.pictures}")
    elif a.figures_json:
        raw = json.loads(Path(a.figures_json).read_text())
        figures = [{"page_no": f["page_no"], "block": render_figure_block(f)} for f in raw]
        print(f"[orchestrate] reused {len(figures)} figure(s) from {a.figures_json} "
              f"(no vision calls)")

    md = orchestrate(a.pdf, a.cu, a.title, figures, page_markers=not a.no_page_markers)
    Path(a.out).write_text(md)
    print(f"[orchestrate] wrote {a.out} ({len(md.split()):,} words)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
