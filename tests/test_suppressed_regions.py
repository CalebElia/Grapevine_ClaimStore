"""Text we refused to trust, and whether anything read it instead.

THE GAP THIS CLOSES. `has_ocr_blocks` triggers a second read when a page has NO usable text
layer. The CAP has a text layer that is WRONG -- its ACTION cost cards render "$1,000,000"
and decode as "$$11,,016000,,000000", a broken ToUnicode map in a subset font. The pipeline
correctly refuses to emit that, but the OCR-fraction trigger is structurally blind to it:
the CAP is 0% OCR, so `corroborate_ocr --pending` would never list it.

Suppressing bad characters is only half a fix. The other half is reading the region some
other way, and on the first CAP run nothing did: 21 runs were suppressed and seven ACTION
pages lost their entire economics silently. Vision recovered them, but only because a person
noticed.

So the gate now asks the question the machinery could not: a region whose characters were
suppressed and which NO figure covers is a hole, and it says so.
"""
from __future__ import annotations

from pipeline.quality_gate import unread_suppressed


def test_a_suppressed_region_with_no_figure_is_a_hole():
    runs = [{"page_no": 117, "text": "$$11,,016000,,000000"}]
    assert unread_suppressed(runs, figure_pages=set()) == [117]


def test_a_suppressed_region_a_figure_covers_is_fine():
    """Vision read the page; the data is in the store even though the prose is not."""
    runs = [{"page_no": 117, "text": "$$11,,016000,,000000"}]
    assert unread_suppressed(runs, figure_pages={117}) == []


def test_only_the_uncovered_pages_are_reported():
    runs = [{"page_no": 105, "text": "x,,y"}, {"page_no": 117, "text": "a,,b"}]
    assert unread_suppressed(runs, figure_pages={105}) == [117]


def test_several_runs_on_one_page_report_it_once():
    """A page is a hole or it is not; three suppressed runs on it is still one hole."""
    runs = [{"page_no": 12, "text": "a,,b"}, {"page_no": 12, "text": "c,,d"},
            {"page_no": 12, "text": "e,,f"}]
    assert unread_suppressed(runs, figure_pages=set()) == [12]


def test_nothing_suppressed_is_never_a_finding():
    assert unread_suppressed([], figure_pages=set()) == []
    assert unread_suppressed(None, figure_pages=set()) == []


def test_pages_are_reported_in_order():
    runs = [{"page_no": 117, "text": "a,,b"}, {"page_no": 12, "text": "c,,d"}]
    assert unread_suppressed(runs, figure_pages=set()) == [12, 117]


# --- the finding itself, not just the predicate -------------------------------------------

from pipeline.quality_gate import assess


def _findings(runs, figure_pages):
    return assess("some text", [(1, 0, 9)], [], reference_words=0,
                  reference_numbers=set(), suppressed_runs=runs, figure_pages=figure_pages)


def test_an_uncovered_suppression_produces_a_finding():
    """THE BUG THIS PINS. The predicate was tested and the Finding was not, so the crash --
    a positional argument in the wrong slot -- only appeared when a hole actually existed.
    The main CAP run had none by then, so it passed; the counterfactual raised TypeError."""
    f = [x for x in _findings([{"page_no": 117, "text": "a,,b"}], set())
         if x.check == "unread_suppressed_region"]
    assert len(f) == 1
    assert f[0].severity == "medium"
    assert "117" in f[0].evidence
    assert f[0].items == [{"page_no": 117}]


def test_a_covered_suppression_produces_none():
    assert not [x for x in _findings([{"page_no": 117, "text": "a,,b"}], {117})
                if x.check == "unread_suppressed_region"]


def test_no_suppression_produces_none():
    assert not [x for x in _findings([], set())
                if x.check == "unread_suppressed_region"]


# --- coverage is geometric, not per page --------------------------------------------------

from pipeline.quality_gate import unread_suppressed_boxes


def test_a_figure_elsewhere_on_the_page_does_not_cover_the_hole():
    """THE LIMITATION THIS REMOVES. Page-level coverage cleared six of the CAP's seven
    lost ACTION cards, because each of those pages ALSO carries a GHG pie chart that vision
    did read. A figure at the top of the page says nothing about a suppressed cost card at
    the bottom, and the first version of this check reported 1 hole where there were 7."""
    runs = [{"page_no": 105, "text": "a,,b", "bbox": [90, 480, 530, 500]}]
    covered = {105: [(70, 100, 540, 300)]}          # a chart, far above
    assert unread_suppressed_boxes(runs, covered) == [105]


def test_a_figure_overlapping_the_region_covers_it():
    runs = [{"page_no": 105, "text": "a,,b", "bbox": [90, 480, 530, 500]}]
    covered = {105: [(70, 460, 540, 520)]}
    assert unread_suppressed_boxes(runs, covered) == []


def test_partial_overlap_counts_as_covered():
    """A crop rarely lines up exactly with a text run; touching it means vision saw it."""
    runs = [{"page_no": 7, "text": "a,,b", "bbox": [100, 200, 300, 220]}]
    assert unread_suppressed_boxes(runs, {7: [(250, 210, 400, 400)]}) == []


def test_a_run_with_no_bbox_falls_back_to_the_page():
    """Older conversion reports carry no geometry. Fall back rather than crash, and prefer
    reporting a hole to missing one."""
    runs = [{"page_no": 9, "text": "a,,b"}]
    assert unread_suppressed_boxes(runs, {9: [(0, 0, 100, 100)]}) == []
    assert unread_suppressed_boxes(runs, {}) == [9]


def test_nothing_suppressed_is_still_never_a_finding():
    assert unread_suppressed_boxes([], {}) == []
    assert unread_suppressed_boxes(None, {}) == []


def test_bottom_left_origin_is_recognised_however_it_is_spelled():
    """THE BUG THIS PINS. Docling records coord_origin as the enum CoordOrigin.BOTTOMLEFT,
    which serialises to the STRING "CoordOrigin.BOTTOMLEFT". Testing startswith("BOTTOM")
    is False on it, so the y-flip never ran, every figure box landed in the wrong half of
    the page, and the gate reported six covered regions as holes.

    This is the third time this coordinate space has bitten: Docling is BOTTOMLEFT and
    pdfplumber is TOP-LEFT, and every comparison between them has to say so out loud."""
    from pipeline.quality_gate import is_bottom_left
    assert is_bottom_left("CoordOrigin.BOTTOMLEFT")
    assert is_bottom_left("BOTTOMLEFT")
    assert is_bottom_left("bottomleft")
    assert not is_bottom_left("CoordOrigin.TOPLEFT")
    assert not is_bottom_left("TOPLEFT")
    assert not is_bottom_left(None)
