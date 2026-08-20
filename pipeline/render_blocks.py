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

from pipeline.convert_blocks import _matches_heading_shape
import re
from pipeline.section_boundaries import _same_section


def render_blocks(blocks: list[dict], figure_xml: dict[int, str], title: str,
                  page_markers: bool = False,
                  heading_shapes: set[str] | None = None,
                  body_shapes: set[str] | None = None) -> str:
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

    # THE DOCUMENT'S OWN TITLE IS THE TITLE. Both years' reviews flagged the same thing:
    # an H1 carrying a command-line string, with the document's real title repeated below
    # it -- a `##` heading on Year 5, plain prose on Year 4. The first block on page 1 is
    # the title, taken verbatim; the caller's string drops to provenance, where it still
    # says which run produced the file without claiming to be what the document is called.
    # A page-1 HEADING outranks an earlier plain block: Year 2's first page-1 block is
    # OCR of the city logo ("City Ann Arbor of", word order scrambled) and the real title
    # follows it as a SectionHeaderItem. Docling's heading type is evidence about which
    # block is the title; position alone is not. Year 4 has no page-1 heading at all, so
    # position remains the fallback.
    p1 = [b for b in blocks if b.get("page_no") == 1 and b["kind"] != "PictureItem"
          and (b.get("text") or "").strip()]
    title_ref = next((b for b in p1 if b["kind"] in ("SectionHeaderItem", "TitleItem")),
                     p1[0] if p1 else None)
    doc_title = (title_ref.get("text") or "").strip() if title_ref else ""

    # OCR PROVENANCE. Year 2's prose is rendered as images, so 94% of its blocks come
    # from Docling's OCR rather than a text layer; Years 4 and 5 are 0%. OCR output is a
    # model's reading of pixels, not character-exact text, and the difference has to
    # travel with the document. The fraction is always stated; individual blocks are
    # marked only when they differ from the document's dominant source, because marking
    # every block in a 94% document buries the very signal the mark exists to carry.
    srcs = [b.get("text_source") for b in blocks if (b.get("text") or "").strip()
            and b["kind"] != "PictureItem"]
    n_ocr = sum(1 for x in srcs if x == "docling_ocr")
    ocr_pct = round(100 * n_ocr / len(srcs)) if srcs else 0
    dominant = "docling_ocr" if n_ocr * 2 > len(srcs) else "pdfplumber"
    mark = {"docling_ocr": "[OCR]", "pdfplumber": "[text layer]"}

    out = [f"# {doc_title or title}", "",
           f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -- "
           f"structure and reading order from Docling, characters from pdfplumber, "
           f"figures from vision extraction -->",
           f"<!-- run: {title} -->",
           f"<!-- {ocr_pct}% OCR: {n_ocr} of {len(srcs)} text blocks were read by OCR "
           f"(no usable text layer), not extracted character-exact. Dominant source: "
           f"{dominant}. -->", ""]
    last_heading = None
    stats = {"figures": 0, "captions_dropped": 0, "recovered": 0}

    page = None
    in_recovered = False
    for b in blocks:
        kind, text = b["kind"], (b.get("text") or "").strip()
        if b["kind"] != "UncoveredText" and (b.get("text") or "").strip():
            in_recovered = False

        if title_ref is not None and b is title_ref:
            continue                       # already emitted as the H1

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

        # A HEADING DOCLING MISTYPED IS STILL A HEADING. Year 4: the long-form strategy
        # headings are SectionHeaderItem on pages 5, 13, 15 and 19 but plain TextItem on
        # 8, 11 and 17 -- same document, same visual style. Promoting on the document's
        # own confirmed shapes also lets the existing continuation merge fold the
        # short-form repeat that follows into one section, rather than opening a second.
        if kind in ("TextItem", "UncoveredText") and _matches_heading_shape(b, heading_shapes):
            kind = "SectionHeaderItem"

        tag = ""
        if srcs and b.get("text_source") and b["text_source"] != dominant:
            tag = f"{mark[b['text_source']]} "

        # A HEADING DOCLING INVENTED IS NOT A HEADING. The mirror of the promotion
        # above: if this block's leading shape is one the document mostly uses for BODY
        # text, the heading type is a mistype. Year 2's DIVE DEEPER callouts are four
        # TextItems and one SectionHeaderItem; the odd one out split a callout in half
        # and detached the bullets below it from their real strategy heading.
        if kind == "SectionHeaderItem" and body_shapes:
            sh = " ".join(re.findall(r"[a-z#]{2,}",
                                     re.sub(r"\d+", "#", text.lower()))[:3])
            if sh in body_shapes:
                kind = "TextItem"

        if kind == "SectionHeaderItem":
            if last_heading is not None and _same_section(text, last_heading):
                continue                        # a continuation, not a new section
            last_heading = text
            out += [f"## {text}", ""]
            continue

        # FURNITURE IS DECIDED BEFORE PROVENANCE. A footer recovered by the coverage
        # sweep is still a footer; letting the UncoveredText branch claim it first marked
        # only 3 of Year 2's 7 contact blocks, because 4 arrived through the sweep.
        if b.get("is_furniture"):
            # Kept -- it is the staff roster and belongs to `people` -- but marked,
            # because it asserts nothing about the world, and extracting seven of these
            # as claims would manufacture statements the report never makes.
            stats["furniture"] = stats.get("furniture", 0) + 1
            out += ["<!-- FURNITURE: page footer, not an assertion -->",
                    f"> {tag}{text}", ""]
            continue

        if kind == "UncoveredText":
            stats["recovered"] += 1
            # One marker per RUN of recovered regions, not per region. Year 5's contents
            # page recovers as 11 consecutive entries, and 11 identical caveats is noise
            # that buries the one thing the caveat is for.
            if not in_recovered:
                out.append(f"<!-- recovered by coverage sweep: no Docling block modelled "
                           f"this region on page {b['page_no']}; placement inferred -->")
            in_recovered = True
            out += [f"> {text}", ""]
            continue

        lvl = b.get("list_level")
        if kind == "ListItem" and lvl is not None:
            # Two spaces per level is markdown's own nesting; the marker is re-emitted
            # rather than kept, because Docling normalises every bullet glyph to the same
            # character regardless of depth (see convert_blocks.assign_nesting).
            out += [f"{'  ' * lvl}- {tag}{text.lstrip('-o• ').strip()}", ""]
            continue

        out += [f"{tag}{text}", ""]

    out.insert(3, f"<!-- {stats['figures']} figure(s) · {stats['captions_dropped']} "
                  f"caption(s) dropped with their photos · {stats['recovered']} "
                  f"region(s) recovered by the coverage sweep -->")
    return "\n".join(out)
