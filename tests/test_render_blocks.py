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
    out = render_blocks([_blk("SectionHeaderItem", 1, "CLOSING")], {}, "T")
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
