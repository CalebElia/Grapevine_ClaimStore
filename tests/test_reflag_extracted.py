"""worth_extraction is derived from the extraction record, not trusted from the block.

A Docling re-run on the CAP overwrote blocks.json and silently dropped 18 already-extracted
figures out of the document -- the cost infographics on pages 105 to 121, whose dollar
figures the text layer decodes as "$$11,,016000,,000000" and which therefore exist in the
store ONLY as vision output. Nothing failed: 66 vision calls had been made, 48 reached a
reader, and the difference was invisible from the markdown.
"""
from __future__ import annotations

from pipeline.orchestrate_blocks import reflag_extracted
from pipeline.render_blocks import figure_key


def _pic(page, label, conf, worth=False):
    return {"kind": "PictureItem", "page_no": page, "top_label": label,
            "top_conf": conf, "worth_extraction": worth}


def _fig(page, label, conf):
    return {"page_no": page, "top_label": label, "top_conf": conf, "xml": "<x/>"}


def test_a_picture_with_an_extraction_is_reflagged():
    """CAP page 105: an "other" at 0.401 that the targeted pass had chosen to read."""
    blocks = [_pic(105, "other", 0.401)]
    assert reflag_extracted(blocks, [_fig(105, "other", 0.401)]) == 1
    assert blocks[0]["worth_extraction"] is True


def test_a_picture_with_no_extraction_is_left_alone():
    """A photograph nobody read stays unread; this restores, it does not promote."""
    blocks = [_pic(52, "photograph", 0.99)]
    assert reflag_extracted(blocks, []) == 0
    assert blocks[0]["worth_extraction"] is False


def test_a_picture_already_flagged_is_not_counted_twice():
    blocks = [_pic(12, "pie_chart", 0.803, worth=True)]
    assert reflag_extracted(blocks, [_fig(12, "pie_chart", 0.803)]) == 0


def test_the_match_is_exact_on_page_label_and_confidence():
    """figure_key is (page, label, conf) -- a different picture on the same page is not it."""
    blocks = [_pic(75, "photograph", 0.993)]
    assert reflag_extracted(blocks, [_fig(75, "photograph", 0.322)]) == 0
    assert reflag_extracted(blocks, [_fig(74, "photograph", 0.993)]) == 0


def test_a_non_picture_block_is_never_touched():
    blocks = [{"kind": "TextItem", "page_no": 105, "top_label": "other", "top_conf": 0.401}]
    assert reflag_extracted(blocks, [_fig(105, "other", 0.401)]) == 0
    assert "worth_extraction" not in blocks[0]


def test_running_it_twice_changes_nothing_the_second_time():
    """Idempotence is the whole point: this must survive any number of re-conversions."""
    blocks = [_pic(105, "other", 0.401)]
    figs = [_fig(105, "other", 0.401)]
    assert reflag_extracted(blocks, figs) == 1
    assert reflag_extracted(blocks, figs) == 0
    assert blocks[0]["worth_extraction"] is True


def test_the_real_eighteen_are_all_recoverable_by_key():
    """Every one of the lost figures matched an existing block exactly -- no fuzzy match."""
    lost = [(35, "full_page_image", 0.515), (75, "photograph", 0.993),
            (105, "other", 0.401), (117, "photograph", 0.435), (121, "full_page_image", 0.415)]
    blocks = [_pic(p, l, c) for p, l, c in lost]
    figs = [_fig(p, l, c) for p, l, c in lost]
    assert reflag_extracted(blocks, figs) == len(lost)
    assert all(b["worth_extraction"] for b in blocks)
