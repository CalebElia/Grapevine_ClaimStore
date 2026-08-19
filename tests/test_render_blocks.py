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

from pipeline.render_blocks import render_blocks


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

def test_the_caption_of_a_dropped_photograph_is_dropped_with_it():
    blocks = [_blk("PictureItem", 3, "", self_ref="#/pictures/1", worth_extraction=False),
              _blk("TextItem", 3, "Mayor Taylor on his e-bike.", caption_for="#/pictures/1")]
    out = render_blocks(blocks, {}, "T")
    assert "e-bike" not in out, "an orphaned caption for a removed photo is noise"


def test_the_caption_of_a_kept_figure_is_folded_into_its_description():
    blocks = [_blk("PictureItem", 5, "", self_ref="#/pictures/9", worth_extraction=True,
                   top_label="screenshot_from_computer", top_conf=0.543),
              _blk("TextItem", 5, "The Renewable Energy tab of the A2ZERO Dashboard.",
                   caption_for="#/pictures/9")]
    out = render_blocks(blocks, {5: "<figure_description>x</figure_description>"}, "T")
    assert "Renewable Energy tab" in out
    assert "<figure_description>" in out
    assert out.index("Renewable Energy tab") < out.index("<figure_description>")


def test_a_kept_figure_renders_its_extracted_xml_at_its_reading_order_position():
    """The page 4 GHG chart sits after exactly three page 4 paragraphs in Docling's
    reading order -- which is where the review said it belonged."""
    blocks = [_blk("TextItem", 4, "First para."), _blk("TextItem", 4, "Second para."),
              _blk("PictureItem", 4, "", self_ref="#/pictures/3", worth_extraction=True,
                   top_label="bar_chart", top_conf=0.997),
              _blk("TextItem", 5, "After the chart.")]
    out = render_blocks(blocks, {4: "<figure_description>chart</figure_description>"}, "T")
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
