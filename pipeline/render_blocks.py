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

CAPTIONS ARE TAGGED, NEVER DELETED. A caption whose figure was kept is that figure's
title and is emitted with it; a caption whose photograph was not retained is emitted
where it stands, marked, so ingest can filter on the tag exactly as it filters furniture.

The earlier design deleted the second kind, and it was the single most dangerous
mechanism in this pipeline: three separate content losses came from it -- a sentence
continuation on Year 3 page 9, another on page 7, and Year 5's OSI staff roster -- each
discovered only because a human read the output and noticed prose had gone. Every guard
added afterwards was correct and none of them was sufficient, because the failure mode
was the deletion itself. Tagging removes the class: a misjudged caption is now
mislabelled rather than missing, which is visible, recoverable, and harmless downstream.

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


def figure_key(rec: dict) -> tuple:
    """Identity of one picture, agreed on by a PictureItem block and a figures.json record.

    KEYING BY PAGE ALONE LOSES FIGURES, and this module used to say so in its own docstring:
    a page with two data-bearing figures "would need a finer key, which Year 5 does not
    exercise". The CAP exercises it -- 55 substantive figures across 38 pages, 17 of them
    overwritten. Page 117 carried two, both classified `photograph`: the ACTION cost card
    reading $1,000,000 and a snapshot of volunteers landscaping. The page rendered neither.

    Label and confidence come from the same classification pass on both sides, so they agree
    by construction. Confidence is rounded because it round-trips through JSON.
    """
    conf = rec.get("top_conf")
    return (rec.get("page_no"), rec.get("top_label"),
            round(float(conf), 3) if conf is not None else None)


def render_blocks(blocks: list[dict], figure_xml: dict[int, str], title: str,
                  page_markers: bool = False,
                  heading_shapes: set[str] | None = None,
                  body_shapes: set[str] | None = None,
                  lead_ins: set[str] | None = None,
                  period: dict | None = None) -> str:
    """blocks in reading order + {page_no: figure XML} -> markdown.

    figure_xml is keyed by figure_key() -- page, classification and confidence -- so that a
    page carrying several data-bearing figures keeps all of them. Keying by page alone lost
    17 of the CAP's 55 substantive figures, including the ACTION cost card on page 117.
    """
    pics = {b.get("self_ref"): b for b in blocks if b["kind"] == "PictureItem"}
    # A kept figure's caption is emitted WITH the figure, so it is not also emitted in
    # place; every other caption is emitted where it stands, tagged.
    # EVERY caption, not just the first. Year 3's pie chart carries four associated
    # legend blocks; a setdefault here kept "Waste, 2%" and silently dropped
    # "Transportation, 29.72%", "Propane, 0.5%" and "Natural Gas, 27%" -- the last
    # deletion path left in the renderer after captions stopped being dropped elsewhere.
    captions: dict[str, list[str]] = {}
    for b in blocks:
        ref = b.get("caption_for")
        if ref and ref in pics:
            captions.setdefault(ref, []).append(b["text"])

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
    # THE PERIOD, MACHINE-READABLE, NOT ONLY AS A HEADING. Year 2 states its coverage in
    # a section heading ("2021 - 2022 Annual Report") and Years 3-5 in a subtitle; once
    # sectioning runs, those are strings like any other and the value is only as safe as
    # whatever a later stage decides to do with headings. documents.covers_period_start
    # exists because the wiki lost a report's period exactly that way.
    if period:
        out.insert(4, f"<!-- COVERAGE PERIOD: {period['text']} -> "
                      f"{period['start']}..{period['end']} ({period['days']} days)"
                      + ("" if 358 <= period["days"] <= 372 else
                         " -- NOT A YEAR; an annual report's stated period should be one,"
                         " so treat this as unconfirmed until a human rules on it")
                      + " -->")
    last_heading = None
    stats = {"figures": 0, "captions_dropped": 0, "recovered": 0}

    page = None
    pending_page = None
    in_recovered = False

    def mark_page(buf: list[str]) -> None:
        """Flush the page marker, if this page has not announced itself yet."""
        nonlocal page
        if page_markers and pending_page != page:
            page = pending_page
            buf.append(f"<!-- p.{page} -->")
    for b in blocks:
        kind, text = b["kind"], (b.get("text") or "").strip()
        if b["kind"] != "UncoveredText" and (b.get("text") or "").strip():
            in_recovered = False

        if title_ref is not None and b is title_ref:
            continue                       # already emitted as the H1

        # THE MARKER FOLLOWS THE CONTENT, NOT THE BLOCK. Emitting it here printed a page
        # marker for blocks that then produced nothing -- a photograph that was not
        # retained, or a block whose text had been merged into its neighbour. Year 3 read
        # "<!-- p.4 --> ... <!-- p.3 --> <!-- p.4 -->", an empty p.3 sandwiched between
        # p.4's bullets, because a dropped picture from page 3 sits there in the stream.
        # A marker that names a page carrying none of the text under it is worse than no
        # marker: every line-number reference in the checklist is resolved against these.
        pending_page = b.get("page_no")

        if kind == "PictureItem":
            if not b.get("worth_extraction"):
                continue                       # a photograph carries no citable content
            mark_page(out)
            stats["figures"] += 1
            cap = " ".join(captions.get(b.get("self_ref")) or []) or None
            fig_page = b["page_no"]        # NOT `page`: that tracks marker emission
            out.append("")
            # No provenance comment emitted here -- render_figure_block() already writes a
            # richer one (deployment + extraction time). Two comments for one figure read
            # as two figures.
            if cap:
                out.append(f"**Figure ({b.get('top_label')}, page {fig_page}):** {cap}")
            xml = figure_xml.get(figure_key(b))
            if xml:
                out += ["", xml.strip()]
            else:
                # NOT PROSE. A caption and a page footer render as blockquotes because
                # they ARE text on the page; this is the pipeline talking about itself,
                # and putting it in the reading stream made Year 2 appear to contain a
                # sentence about a screenshot. It says only that a figure was kept and
                # no vision pass has read it -- which for Year 2 is true of every figure,
                # because that stage has never been run for this document.
                out.append(f"<!-- UNEXTRACTED FIGURE: {b.get('top_label')} on page "
                           f"{fig_page} was kept, but no vision output is available -->")
            out.append("")
            continue

        if not text:
            continue

        mark_page(out)
        tag = ""
        if srcs and b.get("text_source") and b["text_source"] != dominant:
            tag = f"{mark[b['text_source']]} "

        ref = b.get("caption_for")
        if ref and ref in pics:
            if pics[ref].get("worth_extraction"):
                continue          # a kept figure renders its own caption with the figure
            # TAGGED, NOT DELETED. Three of the last four content losses in this pipeline
            # came from this branch removing a block it had misjudged -- a sentence
            # continuation, a staff roster, a bullet tail -- and each was found only
            # because a human read the output. A caption is content; ingest can filter on
            # the tag exactly as it filters furniture. A misjudgement now mislabels
            # instead of deleting, which is visible and recoverable.
            stats["captions_tagged"] = stats.get("captions_tagged", 0) + 1
            out += [f"<!-- CAPTION: describes a {pics[ref].get('top_label')} on page "
                    f"{b['page_no']} that was not retained; not an assertion -->",
                    f"> {tag}{text}", ""]
            continue

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
        # A LEAD-IN IS NOT A SECTION. Year 1 types "In Year One, we:" as a heading under
        # every one of its seven strategies; as `##` it cut each strategy in half and
        # detached the achievements from the strategy they belong to. See
        # convert_blocks.recurring_lead_ins for why the majority vote cannot catch it.
        if kind == "SectionHeaderItem" and lead_ins and \
                " ".join(text.lower().split()) in lead_ins:
            kind = "TextItem"

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
            # A ROSTER IS NOT A FOOTER. Both are furniture -- neither asserts anything
            # about the world -- but calling Year 3's sign-off a "page footer" tells a
            # reviewer something untrue about where it came from, and the tag is what
            # ingest filters on.
            kind_note = ("staff roster / sign-off, not an assertion"
                         if b.get("_signoff") else "page footer, not an assertion")
            out += [f"<!-- FURNITURE: {kind_note} -->", f"> {tag}{text}", ""]
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

    out.insert(3, f"<!-- {stats['figures']} figure(s) · "
                  f"{stats.get('captions_tagged', 0)} caption(s) tagged · "
                  f"{stats.get('furniture', 0)} furniture block(s) · "
                  f"{stats['recovered']} region(s) recovered by the coverage sweep -->")
    return "\n".join(out)
