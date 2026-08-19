"""Typed blocks -> reviewable markdown.

WHY THIS REPLACES THE CU-ANCHORED RENDERER. The previous path took section structure from
Azure CU and body text from pdfplumber, then had to LOCATE each CU heading inside
pdfplumber's text by substring search. That reconciliation is where several reported
defects came from: a heading appeared both as `##` (from CU) and again inside the body
(from pdfplumber) because the two reads were never the same stream, and a section whose
anchor failed to locate had to be marked unverified. Here, structure and text come from
one stream -- Docling's typed blocks, filled with pdfplumber's characters -- so there is
nothing to reconcile and the duplicate cannot occur. CU remains valuable as an
INDEPENDENT cross-check of the section list; it is simply no longer load-bearing.

CAPTIONS FOLLOW THEIR PICTURE. A caption whose photograph was dropped is noise stranded
mid-page ("City officials break ground on Fire Station 4" with no photo above it); a
caption whose figure was kept is that figure's title and belongs inside its description.
Both were called out in the human review, and both are decided here by `caption_for`,
which convert_docling.py resolves structurally rather than by guessing from position.

RECOVERED TEXT IS MARKED, NOT BLENDED IN. Blocks typed UncoveredText exist because
Docling modelled no block for them (see convert_blocks.group_uncovered) -- their position
in the reading order is inferred, not read. Presenting them as ordinary prose would claim
a confidence the pipeline does not have, so they are rendered with an explicit marker.
"""
from __future__ import annotations

import time

from pipeline.section_boundaries import _same_section


def render_blocks(blocks: list[dict], figure_xml: dict[int, str], title: str,
                  page_markers: bool = False) -> str:
    """blocks in reading order + {page_no: figure XML} -> markdown.

    figure_xml is keyed by page because that is what pipeline/extract_figures.py records
    per extraction; a document with two data-bearing figures on ONE page would need a
    finer key, which Year 5 does not exercise and which is not invented here.
    """
    pics = {b.get("self_ref"): b for b in blocks if b["kind"] == "PictureItem"}
    # A kept figure's caption is emitted with the figure, so it must not also be emitted
    # in place; a dropped picture's caption is emitted nowhere at all.
    captions: dict[str, str] = {}
    for b in blocks:
        ref = b.get("caption_for")
        if ref and ref in pics:
            captions.setdefault(ref, b["text"])

    out = [f"# {title}", "",
           f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -- "
           f"structure and reading order from Docling, characters from pdfplumber, "
           f"figures from vision extraction -->", ""]
    last_heading = None
    stats = {"figures": 0, "captions_dropped": 0, "recovered": 0}

    page = None
    for b in blocks:
        kind, text = b["kind"], (b.get("text") or "").strip()

        if page_markers and b.get("page_no") != page:
            page = b.get("page_no")
            out += [f"<!-- p.{page} -->"]

        if kind == "PictureItem":
            if not b.get("worth_extraction"):
                continue                       # a photograph carries no citable content
            stats["figures"] += 1
            cap = captions.get(b.get("self_ref"))
            fig_page = b["page_no"]        # NOT `page`: that tracks marker emission
            out.append("")
            # No provenance comment emitted here -- render_figure_block() already writes a
            # richer one (deployment + extraction time). Two comments for one figure read
            # as two figures.
            if cap:
                out.append(f"**Figure ({b.get('top_label')}, page {fig_page}):** {cap}")
            xml = figure_xml.get(fig_page)
            if xml:
                out += ["", xml.strip()]
            else:
                out.append(f"> **[{b.get('top_label')} on page {fig_page} was kept but "
                           f"not extracted -- no vision output available.]**")
            out.append("")
            continue

        if not text:
            continue

        ref = b.get("caption_for")
        if ref and ref in pics:
            if not pics[ref].get("worth_extraction"):
                stats["captions_dropped"] += 1  # its photo is gone; the caption is noise
            continue                            # kept figures render their own caption

        if kind == "SectionHeaderItem":
            if last_heading is not None and _same_section(text, last_heading):
                continue                        # a continuation, not a new section
            last_heading = text
            out += [f"## {text}", ""]
            continue

        if kind == "UncoveredText":
            stats["recovered"] += 1
            out += [f"<!-- recovered by coverage sweep: no Docling block modelled this "
                    f"region on page {b['page_no']}; placement inferred -->",
                    f"> {text}", ""]
            continue

        out += [text, ""]

    out.insert(3, f"<!-- {stats['figures']} figure(s) · {stats['captions_dropped']} "
                  f"caption(s) dropped with their photos · {stats['recovered']} "
                  f"region(s) recovered by the coverage sweep -->")
    return "\n".join(out)
