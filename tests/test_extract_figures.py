"""crop_box_px is the one function standing between a correct crop and a silently
mirrored one -- get the BOTTOMLEFT/TOP-LEFT sign wrong and the result still looks like a
plausible crop of a photo-heavy page, so this is pinned against the real Year 5 page 4
bar_chart bbox, not a synthetic round-number example that would hide a sign error.
"""
from __future__ import annotations

from pipeline.extract_figures import crop_box_px, render_figure_block

# Real bbox from the actual Docling run on a2zero-year5.pdf, page 4 (bar_chart, 99.7%
# confidence). page height 792pt (US Letter) confirmed independently via pdfplumber.
_REAL_BBOX = (49.219459533691406, 228.018310546875, 496.0024108886719, 46.468505859375)
_PAGE_H = 792.0


def test_bottomleft_origin_is_flipped_to_top_left_at_dpi_72():
    x0, y0, x1, y1 = crop_box_px(_REAL_BBOX, "CoordOrigin.BOTTOMLEFT", _PAGE_H, dpi=72)
    # l/r pass through unchanged; t/b flip via page_h - y.
    assert x0 == 49
    assert x1 == 496
    assert y0 == int(_PAGE_H - 228.018310546875)
    assert y1 == int(_PAGE_H - 46.468505859375)
    assert y0 < y1, "top-left y must be smaller than bottom-left y after the flip"


def test_scaling_to_600dpi_multiplies_every_coordinate_by_dpi_over_72():
    """Each box is independently truncated to int pixels at its own DPI, so a
    72dpi-truncation error (< 1 point) gets amplified by the scale factor once compared
    against the 600dpi box -- the tolerance accounts for that, not for a bug.
    """
    box_72 = crop_box_px(_REAL_BBOX, "CoordOrigin.BOTTOMLEFT", _PAGE_H, dpi=72)
    box_600 = crop_box_px(_REAL_BBOX, "CoordOrigin.BOTTOMLEFT", _PAGE_H, dpi=600)
    scale = 600 / 72
    for a, b in zip(box_72, box_600):
        assert abs(b - a * scale) <= scale, "600dpi crop must be the 72dpi crop scaled up"


def test_a_non_bottomleft_origin_is_not_flipped():
    """A defensive branch, not yet exercised by any real document -- Docling always
    reported BOTTOMLEFT in every run measured so far -- but a wrong flip on some future
    TOPLEFT-origin source would be exactly this bug's mirror image.
    """
    x0, y0, x1, y1 = crop_box_px((10, 20, 110, 120), "CoordOrigin.TOPLEFT", 792.0, dpi=72)
    assert (x0, y0, x1, y1) == (10, 20, 110, 120)


def test_an_unsorted_bbox_still_yields_a_valid_box():
    """l/r or t/b swapped must not silently produce a negative-width crop."""
    x0, y0, x1, y1 = crop_box_px((100, 50, 10, 150), "CoordOrigin.BOTTOMLEFT", 792.0, dpi=72)
    assert x0 < x1 and y0 < y1


def test_figure_block_carries_page_label_and_provenance():
    fig = {"page_no": 4, "top_label": "bar_chart", "top_conf": 0.997,
          "deployment": "gpt-5.6-sol", "extracted_at": "2026-08-19T00:17:47Z",
          "xml": "<figure_description>x</figure_description>"}
    block = render_figure_block(fig)
    assert "page 4" in block
    assert "bar_chart" in block
    assert "gpt-5.6-sol" in block
    assert "<figure_description>x</figure_description>" in block


def test_the_provenance_timestamp_is_the_extraction_time_not_the_render_time():
    """The real bug this pins, caught by diffing two renders of the SAME figures.json:
    the timestamp was generated at render time, so re-rendering markdown restamped every
    block with a moment when no vision call happened. A provenance comment that quietly
    updates itself looks like evidence and isn't.
    """
    fig = {"page_no": 4, "top_label": "bar_chart", "top_conf": 0.997,
          "deployment": "gpt-5.6-sol", "extracted_at": "2026-08-19T00:17:47Z", "xml": "<x/>"}
    assert "2026-08-19T00:17:47Z" in render_figure_block(fig)
    assert render_figure_block(fig) == render_figure_block(fig), "must be deterministic"


def test_a_record_with_no_extraction_time_says_so_rather_than_inventing_one():
    fig = {"page_no": 4, "top_label": "bar_chart", "top_conf": 0.997,
          "deployment": "gpt-5.6-sol", "xml": "<x/>"}
    assert "at ? -->" in render_figure_block(fig)
