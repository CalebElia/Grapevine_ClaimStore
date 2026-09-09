"""Rendering typed blocks to markdown.

Every rule here traces to a specific finding in the human review of the Year 5 output:

  headings   "This is a header and we've lost the formatting" / "This 'GREENHOUSE GAS
             EMISSIONS SUMMARY' is duplicative" -- headings previously came from CU while
             the body came from pdfplumber, so a heading appeared BOTH as `##` and again
             inside the body text. Typed SectionHeaderItem blocks make the body and the
             heading the same stream, so the duplicate cannot arise.

  captions   "We should likely design this so that the captions are dropped unless the
             image is retained" and "this is a caption text and should be folded into the
             image description itself since we're keeping the image."

  figures    "This figure description is not in precisely the right location. It should be
             after the first three paragraphs" -- reading-order placement, not
             end-of-section placement.
"""
from __future__ import annotations

from pipeline.render_blocks import fold_caption, figure_key, render_blocks


def _blk(kind, page, text, **kw):
    return {"kind": kind, "page_no": page, "text": text, "caption_for": None, **kw}


def test_a_section_header_block_becomes_a_markdown_heading():
    # page 24, not page 1: the first page-1 block is the document's H1 title, so a cover
    # page cannot also be used as an arbitrary stand-in for "some body page".
    out = render_blocks([_blk("SectionHeaderItem", 24, "CLOSING")], {}, "T")
    assert "## CLOSING" in out


def test_a_heading_never_also_appears_as_body_text():
    """The reported duplication: one heading rendered twice, once as ## and once inline."""
    out = render_blocks([_blk("SectionHeaderItem", 4, "GREENHOUSE GAS EMISSIONS SUMMARY")],
                        {}, "T")
    assert out.count("GREENHOUSE GAS EMISSIONS SUMMARY") == 1


def test_a_repeated_continuation_heading_does_not_open_a_second_section():
    """Real Year 5 structure: 'STRATEGY 1: 100% RENEWABLES' on p.6 then '1: 100%
    RENEWABLES' on p.7 -- the same section resuming, not a new one."""
    out = render_blocks([_blk("SectionHeaderItem", 6, "STRATEGY 1: 100% RENEWABLES"),
                         _blk("TextItem", 6, "Body on six."),
                         _blk("SectionHeaderItem", 7, "1: 100% RENEWABLES"),
                         _blk("TextItem", 7, "Body on seven.")], {}, "T")
    assert out.count("## ") == 1
    assert "Body on six." in out and "Body on seven." in out


def test_list_items_render_as_list_items_in_reading_order():
    out = render_blocks([_blk("ListItem", 6, "- Our Solarize program reached 5.4MW."),
                         _blk("ListItem", 6, "- Solarize was expanded to Grand Rapids.")],
                        {}, "T")
    assert out.index("Solarize program") < out.index("Grand Rapids")


# ── captions follow their picture's fate ───────────────────────────────────────────────

def test_the_caption_of_a_dropped_photograph_is_no_longer_deleted():
    """SUPERSEDED BEHAVIOUR, kept as the record of why it changed. This once asserted the
    caption was removed. Deleting cost three real content losses -- a sentence
    continuation, a staff roster and a bullet tail -- each found only by a human reading
    the output, so the branch now tags instead. A caption is content; ingest filters on
    the tag."""
    blocks = [_blk("PictureItem", 3, "", self_ref="#/pictures/1", worth_extraction=False,
                   top_label="photograph"),
              _blk("TextItem", 3, "Mayor Taylor on his e-bike.", caption_for="#/pictures/1")]
    out = render_blocks(blocks, {}, "T")
    assert "e-bike" in out and "CAPTION" in out


def test_the_caption_of_a_kept_figure_is_folded_into_its_description():
    blocks = [_blk("PictureItem", 5, "", self_ref="#/pictures/9", worth_extraction=True,
                   top_label="screenshot_from_computer", top_conf=0.543),
              _blk("TextItem", 5, "The Renewable Energy tab of the A2ZERO Dashboard.",
                   caption_for="#/pictures/9")]
    out = render_blocks(blocks, {figure_key({"page_no": 5,
                                       "top_label": "screenshot_from_computer",
                                       "top_conf": 0.543}):
                               "<figure_description>x</figure_description>"}, "T")
    # INSIDE the description, not above it. The caption is the one part of a figure block
    # the document itself printed, and keeping it in there is what stops a later stage --
    # or a reader -- from separating it from the figure it describes.
    assert "<caption_text>The Renewable Energy tab of the A2ZERO Dashboard.</caption_text>" in out
    assert out.index("<figure_description>") < out.index("Renewable Energy tab")
    assert out.index("Renewable Energy tab") < out.index("</figure_description>")


def test_a_kept_figure_renders_its_extracted_xml_at_its_reading_order_position():
    """The page 4 GHG chart sits after exactly three page 4 paragraphs in Docling's
    reading order -- which is where the review said it belonged."""
    blocks = [_blk("TextItem", 4, "First para."), _blk("TextItem", 4, "Second para."),
              _blk("PictureItem", 4, "", self_ref="#/pictures/3", worth_extraction=True,
                   top_label="bar_chart", top_conf=0.997),
              _blk("TextItem", 5, "After the chart.")]
    out = render_blocks(blocks, {figure_key({"page_no": 4, "top_label": "bar_chart",
                                       "top_conf": 0.997}):
                               "<figure_description>chart</figure_description>"}, "T")
    assert out.index("Second para.") < out.index("<figure_description>")
    assert out.index("<figure_description>") < out.index("After the chart.")


def test_a_dropped_photograph_contributes_nothing_at_all():
    blocks = [_blk("PictureItem", 3, "", self_ref="#/pictures/1", worth_extraction=False,
                   top_label="photograph", top_conf=0.99)]
    out = render_blocks(blocks, {}, "T")
    assert "photograph" not in out and "pictures/1" not in out


# ── recovered text is included but never passed off as clean ───────────────────────────

def test_uncovered_text_is_rendered_with_a_marker():
    """It was recovered by the coverage sweep precisely because Docling did not model it,
    so its placement is a guess -- say so rather than blending it into the prose."""
    out = render_blocks([_blk("UncoveredText", 2, "3 INTRODUCTION 4 GREENHOUSE GAS")],
                        {}, "T")
    assert "INTRODUCTION" in out
    assert "recovered" in out.lower()


def test_a_figure_with_no_extraction_still_announces_itself():
    """A chart we chose to keep but have no XML for must not vanish silently."""
    blocks = [_blk("PictureItem", 9, "", self_ref="#/pictures/4", worth_extraction=True,
                   top_label="bar_chart", top_conf=0.9)]
    out = render_blocks(blocks, {}, "T")
    assert "bar_chart" in out and "not extracted" in out.lower()


def test_page_markers_are_emitted_when_the_page_changes():
    """The human verification pass needs to trace any line to one page without hunting;
    with typed blocks the page is known per block, so no offset arithmetic is needed."""
    out = render_blocks([_blk("TextItem", 3, "On page three."),
                         _blk("TextItem", 4, "On page four.")], {}, "T",
                        page_markers=True)
    assert "<!-- p.4 -->" in out
    assert out.index("<!-- p.4 -->") < out.index("On page four.")


def test_the_first_page_is_marked_too():
    out = render_blocks([_blk("TextItem", 3, "First block.")], {}, "T", page_markers=True)
    assert "<!-- p.3 -->" in out


def test_page_markers_are_off_by_default():
    out = render_blocks([_blk("TextItem", 3, "x")], {}, "T")
    assert "<!-- p." not in out


# ── a mistyped heading renders AS a heading ────────────────────────────────────────────
# Year 4's audit: Docling typed the long-form strategy headings as SectionHeaderItem on
# pages 5, 13, 15 and 19 but as plain TextItem on 8, 11 and 17. Recovering them from
# deletion was step one; they still rendered as body prose. The document's own confirmed
# heading shapes say what they are, and the existing continuation-merge then correctly
# folds the short-form repeat that follows ("STRATEGY 6: RESILIENCE") into the same
# section instead of opening a second one.

def test_a_textitem_matching_a_confirmed_heading_shape_renders_as_a_heading():
    blocks = [_blk("SectionHeaderItem", 5, "STRATEGY 1: Powering Our Electrical Grid"),
              _blk("TextItem", 17, "STRATEGY 6: Enhance the Resilience of Our People")]
    out = render_blocks(blocks, {}, "T", heading_shapes={"strategy 1", "strategy 6"})
    assert "## STRATEGY 6: Enhance the Resilience of Our People" in out


def test_the_short_form_repeat_folds_into_the_promoted_heading():
    """Year 4 pages 17-18: the long form then 'STRATEGY 6: RESILIENCE' -- one section."""
    blocks = [_blk("TextItem", 17, "STRATEGY 6: Enhance the Resilience of Our People"),
              _blk("TextItem", 17, "Body prose about resilience."),
              _blk("SectionHeaderItem", 18, "STRATEGY 6: RESILIENCE"),
              _blk("TextItem", 18, "More body prose.")]
    out = render_blocks(blocks, {}, "T", heading_shapes={"strategy 6"})
    assert out.count("## ") == 1
    assert "Body prose about resilience." in out and "More body prose." in out


def test_ordinary_prose_mentioning_a_strategy_is_not_promoted():
    """'Strategy 2 of A2ZERO focuses on...' opens many body paragraphs; it is not a
    heading, and only the WORD-N-COLON shape marks one."""
    blocks = [_blk("TextItem", 9, "Strategy 2 of A2ZERO focuses on beneficial "
                                  "electrification, or the switching of appliances.")]
    out = render_blocks(blocks, {}, "T", heading_shapes={"strategy 2"})
    assert "## " not in out


def test_heading_shapes_are_optional():
    out = render_blocks([_blk("TextItem", 1, "STRATEGY 6: Something")], {}, "T")
    assert "## " not in out


# ── the document's own title is the title ──────────────────────────────────────────────
# Flagged on both years: the `#` line carried a title typed on the command line, while
# the document's REAL title appeared again below it -- as a `##` heading on Year 5
# ("A2ZER0 Annual Report Year Five") and as plain prose on Year 4 ("A2ZERO YEAR FOUR
# ANNUAL REPORT JULY 1, 2023 - JUNE 3, 2024"). Two titles, one of them not the
# document's. The first block on page 1 is the document's own title, verbatim.

def test_the_first_page_one_block_becomes_the_h1_title():
    blocks = [_blk("SectionHeaderItem", 1, "A2ZER0 Annual Report Year Five"),
              _blk("TextItem", 3, "Body prose.")]
    out = render_blocks(blocks, {}, "CLI Title")
    assert out.startswith("# A2ZER0 Annual Report Year Five")


def test_the_documents_title_is_not_also_repeated_below():
    blocks = [_blk("SectionHeaderItem", 1, "A2ZER0 Annual Report Year Five"),
              _blk("TextItem", 3, "Body prose.")]
    out = render_blocks(blocks, {}, "CLI Title")
    assert out.count("A2ZER0 Annual Report Year Five") == 1


def test_a_year_4_style_plain_text_title_is_promoted_too():
    blocks = [_blk("TextItem", 1,
                   "A2ZERO YEAR FOUR ANNUAL REPORT JULY 1, 2023 - JUNE 3, 2024"),
              _blk("TextItem", 3, "Body prose.")]
    out = render_blocks(blocks, {}, "CLI Title")
    assert out.startswith("# A2ZERO YEAR FOUR ANNUAL REPORT")


def test_the_cli_title_survives_as_provenance_not_as_the_heading():
    """It still names which run produced the file; it just is not the document's title."""
    out = render_blocks([_blk("SectionHeaderItem", 1, "Real Title")], {}, "CLI Title")
    assert "CLI Title" in out
    assert "# CLI Title" not in out


def test_a_document_with_no_page_one_text_falls_back_to_the_given_title():
    out = render_blocks([_blk("TextItem", 3, "Body only.")], {}, "Fallback Title")
    assert out.startswith("# Fallback Title")


def test_consecutive_recovered_regions_share_one_marker():
    """Year 5's table of contents recovers as 11 separate entries, each of which was
    emitting its own identical provenance comment. One run, one marker -- the caveat is
    about the run, not about each line in it.
    """
    blocks = [_blk("UncoveredText", 2, "3 INTRODUCTION"),
              _blk("UncoveredText", 2, "4 GREENHOUSE GAS"),
              _blk("UncoveredText", 2, "6 STRATEGY 1")]
    out = render_blocks(blocks, {}, "T")
    assert out.lower().count("recovered by coverage sweep") == 1
    for t in ("3 INTRODUCTION", "4 GREENHOUSE GAS", "6 STRATEGY 1"):
        assert t in out


def test_a_recovered_region_after_ordinary_prose_gets_its_own_marker():
    blocks = [_blk("UncoveredText", 2, "recovered one"),
              _blk("TextItem", 3, "Ordinary prose."),
              _blk("UncoveredText", 4, "recovered two")]
    out = render_blocks(blocks, {}, "T")
    assert out.lower().count("recovered by coverage sweep") == 2


# ── OCR provenance is stated, and the exception is what gets marked ────────────────────
# Year 2 is read 94% by OCR; Years 4 and 5 are 0%. Marking every OCR block in a 94%
# document buries the signal in noise, and marking none in a 5% document hides it. So the
# document-level fraction is always stated, and individual blocks are marked only when
# they DIFFER from the document's dominant source -- mark the exception, not the rule.

def test_the_ocr_fraction_is_always_stated():
    blocks = [_blk("TextItem", 3, "Read by OCR.", text_source="docling_ocr"),
              _blk("TextItem", 4, "Read from the text layer.", text_source="pdfplumber")]
    out = render_blocks(blocks, {}, "T")
    assert "50%" in out and "OCR" in out


def test_a_minority_ocr_block_is_marked_individually():
    blocks = [_blk("TextItem", 3, "one", text_source="pdfplumber"),
              _blk("TextItem", 3, "two", text_source="pdfplumber"),
              _blk("TextItem", 3, "three", text_source="pdfplumber"),
              _blk("TextItem", 4, "the odd one out", text_source="docling_ocr")]
    out = render_blocks(blocks, {}, "T")
    assert out.count("[OCR]") == 1


def test_in_a_majority_ocr_document_the_text_layer_block_is_the_one_marked():
    blocks = [_blk("TextItem", 3, "one", text_source="docling_ocr"),
              _blk("TextItem", 3, "two", text_source="docling_ocr"),
              _blk("TextItem", 3, "three", text_source="docling_ocr"),
              _blk("TextItem", 4, "the odd one out", text_source="pdfplumber")]
    out = render_blocks(blocks, {}, "T")
    assert out.count("[text layer]") == 1
    assert "[OCR]" not in out


def test_a_document_with_no_ocr_says_so_and_marks_nothing():
    blocks = [_blk("TextItem", 3, "clean", text_source="pdfplumber")]
    out = render_blocks(blocks, {}, "T")
    assert "0% OCR" in out
    assert "[OCR]" not in out


def test_a_page_one_heading_outranks_an_earlier_plain_text_block():
    """Year 2's first page-1 block is OCR of the city logo -- 'City Ann Arbor of', word
    order scrambled -- while the actual title follows it as a SectionHeaderItem. Docling's
    heading type is evidence about which block is the title; position alone is not.
    """
    blocks = [_blk("TextItem", 1, "City Ann Arbor of"),
              _blk("SectionHeaderItem", 1, "A²ZERO ANNUAL REPORT"),
              _blk("TextItem", 3, "Body.")]
    out = render_blocks(blocks, {}, "T")
    assert out.startswith("# A²ZERO ANNUAL REPORT")


def test_with_no_page_one_heading_the_first_block_is_still_the_title():
    """Year 4: no SectionHeaderItem on the cover at all."""
    blocks = [_blk("TextItem", 1, "A2ZERO YEAR FOUR ANNUAL REPORT JULY 1, 2023"),
              _blk("TextItem", 3, "Body.")]
    out = render_blocks(blocks, {}, "T")
    assert out.startswith("# A2ZERO YEAR FOUR ANNUAL REPORT")


# ── nesting and furniture in the rendered markdown ─────────────────────────────────────

def test_a_nested_list_item_is_indented_under_its_parent():
    blocks = [_blk("ListItem", 13, "Continually wrote grants, including:", list_level=0),
              _blk("ListItem", 13, "$25,000 from the U.S. EPA", list_level=1),
              _blk("ListItem", 13, "$75,000 from the McKnight Foundation", list_level=1)]
    out = render_blocks(blocks, {}, "T")
    assert "\n- Continually wrote grants" in out
    assert "\n  - $25,000 from the U.S. EPA" in out


def test_furniture_is_kept_but_marked_so_it_is_not_read_as_a_claim():
    blocks = [_blk("TextItem", 3, "1 For more information on activities to support "
                                  "Strategy 1, please contact Missy Stults",
                   is_furniture=True),
              _blk("TextItem", 3, "Real body prose making an actual claim.")]
    out = render_blocks(blocks, {}, "T")
    assert "Missy Stults" in out, "the staff roster is worth keeping"
    assert "FURNITURE" in out
    assert "Real body prose" in out


def test_ordinary_prose_carries_no_furniture_marker():
    out = render_blocks([_blk("TextItem", 3, "Installed 1.7MW of solar.")], {}, "T")
    assert "FURNITURE" not in out


def test_a_heading_whose_shape_is_mostly_body_text_renders_as_body():
    """Year 2's five DIVE DEEPER callouts: four TextItem, one SectionHeaderItem."""
    blocks = [_blk("TextItem", 4, "DIVE DEEPER into SOLAR: the city is exploring"),
              _blk("TextItem", 9, "DIVE DEEPER into TREES: since spring 2021"),
              _blk("SectionHeaderItem", 7, "DIVE DEEPER into COMMERCIAL BENCHMARKING")]
    out = render_blocks(blocks, {}, "T", body_shapes={"dive deeper into"})
    assert "## DIVE DEEPER" not in out
    assert "DIVE DEEPER into COMMERCIAL BENCHMARKING" in out


def test_a_real_heading_is_untouched_by_the_body_shape_set():
    out = render_blocks([_blk("SectionHeaderItem", 5, "STRATEGY 1: RENEWABLES")], {}, "T",
                        body_shapes={"dive deeper into"})
    assert "## STRATEGY 1: RENEWABLES" in out


# ── captions are TAGGED, never deleted ─────────────────────────────────────────────────
# Three of the last four content losses in this pipeline came from caption association
# deleting a block it had misjudged: Year 3's "the downtown, reducing vehicle/bicyclist
# conflicts.", Year 3's "households make health, safety, and quality of life
# improvements.", and Year 5's OSI staff roster. Each guard added afterwards was correct
# and each was discovered only because a human read the output.
#
# Tagging removes the failure class rather than narrowing it. A caption that turns out to
# be body prose is now mislabelled instead of missing -- visible, recoverable, and
# harmless to ingest, which can filter on the tag exactly as it filters furniture.

def test_a_dropped_photographs_caption_is_kept_and_tagged():
    blocks = [_blk("PictureItem", 3, "", self_ref="#/pictures/1", worth_extraction=False,
                   top_label="photograph"),
              _blk("TextItem", 3, "Mayor Taylor on his e-bike.", caption_for="#/pictures/1")]
    out = render_blocks(blocks, {}, "T")
    assert "Mayor Taylor on his e-bike." in out, "a caption is content, not noise"
    assert "CAPTION" in out


def test_a_mistagged_body_sentence_survives_in_full():
    """The exact Year 3 failure: a sentence continuation misjudged as a caption. Under
    tagging it is still in the document, merely labelled wrongly."""
    blocks = [_blk("PictureItem", 9, "", self_ref="#/pictures/13", worth_extraction=False,
                   top_label="photograph"),
              _blk("TextItem", 9, "the downtown, reducing vehicle/bicyclist conflicts.",
                   caption_for="#/pictures/13")]
    out = render_blocks(blocks, {}, "T")
    assert "reducing vehicle/bicyclist conflicts" in out


def test_a_kept_figures_caption_still_folds_into_its_description():
    """Unchanged: when the picture survives, its caption belongs with the figure."""
    blocks = [_blk("PictureItem", 5, "", self_ref="#/pictures/9", worth_extraction=True,
                   top_label="screenshot_from_computer", top_conf=0.5),
              _blk("TextItem", 5, "The Renewable Energy tab of the A2ZERO Dashboard.",
                   caption_for="#/pictures/9")]
    out = render_blocks(blocks, {figure_key({"page_no": 5,
                                       "top_label": "screenshot_from_computer",
                                       "top_conf": 0.5}):
                               "<figure_description>x</figure_description>"}, "T")
    assert out.index("<figure_description>") < out.index("Renewable Energy tab") \
        < out.index("</figure_description>")
    assert out.count("Renewable Energy tab") == 1, "folded in, not also emitted in place"


def test_the_caption_tag_names_the_picture_it_describes():
    blocks = [_blk("PictureItem", 3, "", self_ref="#/pictures/1", worth_extraction=False,
                   top_label="photograph"),
              _blk("TextItem", 3, "Community swap at Pittsfield Elementary.",
                   caption_for="#/pictures/1")]
    out = render_blocks(blocks, {}, "T")
    assert "photograph" in out and "page 3" in out


def test_ordinary_prose_carries_no_caption_tag():
    out = render_blocks([_blk("TextItem", 3, "Installed 1.7MW of solar.")], {}, "T")
    assert "CAPTION" not in out


def test_every_caption_of_a_kept_figure_is_emitted_not_just_the_first():
    """Year 3's pie chart has FOUR associated legend blocks. Keeping only the first left
    "Waste, 2%" in the document and silently dropped "Transportation, 29.72%",
    "Propane, 0.5%" and "Natural Gas, 27%" -- the last deletion path in the renderer,
    hiding behind a setdefault.
    """
    blocks = [_blk("PictureItem", 2, "", self_ref="#/pictures/3", worth_extraction=True,
                   top_label="pie_chart", top_conf=1.0),
              _blk("TextItem", 2, "Waste, 2%", caption_for="#/pictures/3"),
              _blk("TextItem", 2, "Transportation, 29.72%", caption_for="#/pictures/3"),
              _blk("TextItem", 2, "Natural Gas, 27%", caption_for="#/pictures/3")]
    out = render_blocks(blocks, {2: "<figure_description>x</figure_description>"}, "T")
    for label in ("Waste, 2%", "Transportation, 29.72%", "Natural Gas, 27%"):
        assert label in out, f"{label} was dropped"


def test_a_single_caption_still_reads_as_one_line():
    blocks = [_blk("PictureItem", 5, "", self_ref="#/pictures/9", worth_extraction=True,
                   top_label="bar_chart", top_conf=0.9),
              _blk("TextItem", 5, "The Renewable Energy tab.", caption_for="#/pictures/9")]
    out = render_blocks(blocks, {}, "T")
    assert "**Figure (bar_chart, page 5):** The Renewable Energy tab." in out


def test_a_recurring_lead_in_renders_as_plain_text_not_a_heading():
    """Year 1's "In Year One, we:" introduces each strategy's bullet list. As a `##` it
    cut every strategy in half; as plain text it reads as what it is."""
    blocks = [_blk("SectionHeaderItem", 2, "Strategy 1: Renewables"),
              _blk("SectionHeaderItem", 2, "In Year One, we:"),
              _blk("ListItem", 2, "- Installed 1.3MW of solar", list_level=0)]
    out = render_blocks(blocks, {}, "T", lead_ins={"in year one, we:"})
    assert "## Strategy 1: Renewables" in out
    assert "## In Year One, we:" not in out
    assert "In Year One, we:" in out, "the lead-in is text, not noise"


def test_a_real_heading_is_untouched_by_the_lead_in_set():
    out = render_blocks([_blk("SectionHeaderItem", 2, "Strategy 1: Renewables")], {}, "T",
                        lead_ins={"in year one, we:"})
    assert "## Strategy 1: Renewables" in out


def test_a_page_marker_is_not_emitted_for_a_block_that_renders_nothing():
    """Year 3 read "<!-- p.4 --> ... <!-- p.3 --> <!-- p.4 -->" -- an empty p.3 between
    page 4's bullets, because a photograph from page 3 that was not retained sits there in
    the stream. A marker naming a page that carries none of the text under it is worse
    than no marker: the checklist resolves every line reference against these."""
    blocks = [{"kind": "TextItem", "text": "Title", "page_no": 1},
              {"kind": "ListItem", "text": "Executed a contract for solar.", "page_no": 4},
              {"kind": "PictureItem", "page_no": 3, "worth_extraction": False,
               "self_ref": "#/pictures/1", "top_label": "photograph"},
              {"kind": "ListItem", "text": "Supported clean energy legislation.", "page_no": 4}]
    md = render_blocks(blocks, {}, "t", page_markers=True)
    assert "<!-- p.3 -->" not in md
    assert md.count("<!-- p.4 -->") == 1


def test_a_page_marker_still_appears_for_a_page_with_content():
    blocks = [{"kind": "TextItem", "text": "Title", "page_no": 1},
              {"kind": "ListItem", "text": "On page four.", "page_no": 4},
              {"kind": "ListItem", "text": "On page five.", "page_no": 5}]
    md = render_blocks(blocks, {}, "t", page_markers=True)
    assert "<!-- p.4 -->" in md and "<!-- p.5 -->" in md


def test_a_figure_with_no_extraction_keeps_its_caption_visible():
    """With no description to fold into, the caption is still text the document printed."""
    blocks = [_blk("PictureItem", 5, "", self_ref="#/pictures/9", worth_extraction=True,
                   top_label="bar_chart", top_conf=0.9),
              _blk("TextItem", 5, "Figure 7: Costs over ten years.",
                   caption_for="#/pictures/9")]
    out = render_blocks(blocks, {}, "T")
    assert "Figure 7: Costs over ten years." in out


def test_folding_a_caption_twice_does_not_double_it():
    """Re-rendering from a saved figures.json must be idempotent."""
    once = fold_caption("<figure_description>x</figure_description>", "Figure 4.")
    assert fold_caption(once, "Figure 4.") == once


def test_a_figure_with_no_caption_is_left_alone():
    xml = "<figure_description>x</figure_description>"
    assert fold_caption(xml, None) == xml
    assert fold_caption(xml, "   ") == xml


def test_a_table_is_emitted_verbatim_on_its_own_lines():
    """A rendered table must not be blockquoted, indented or prefixed, or it stops parsing."""
    md = "| STRATEGY 1 | Total Costs |\n|---|---|\n| Landfill Solar Project | $80,000 |"
    out = render_blocks([_blk("TableItem", 10, md)], {}, "T")
    assert md in out
    assert "> | STRATEGY 1" not in out


def test_a_table_is_separated_from_the_prose_around_it():
    """Butted against a paragraph, a markdown table does not render as a table."""
    md = "| a | b |\n|---|---|\n| c | d |"
    out = render_blocks([_blk("TextItem", 10, "Before."), _blk("TableItem", 10, md),
                         _blk("TextItem", 10, "After.")], {}, "T")
    assert f"\n\n{md}\n\n" in out
