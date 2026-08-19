"""Docling picture classification: count the TOP label per picture, nothing else.

Testable without docling installed -- `_count_top_label` is a pure function over plain
mock objects, matching the real shape confirmed by a raw dump on Year 2: `.predictions`
is a list of objects with `.class_name`, sorted by confidence descending, one entry per
CLASS (not per picture) -- 26 entries per picture, not one.

The bug this pins: the first version assumed `.predicted_classes` (wrong attribute name,
so it silently fell through to treating the container object itself as the one
prediction), then iterated it, producing one dict key per picture holding the ENTIRE
repr of all 26 ranked predictions as a string. On the real Year 2 run this surfaced as
every one of 26 class names appearing exactly 13 times -- the picture count, not a label
distribution.
"""

from __future__ import annotations

from pipeline.convert_docling import CLASSIFY_LABELS, _count_top_label, _picture_record


class _Pred:
    def __init__(self, class_name, confidence):
        self.class_name = class_name
        self.confidence = confidence


class _Cls:
    def __init__(self, predictions):
        self.predictions = predictions


def test_only_the_top_ranked_prediction_is_counted():
    """A picture's classification carries ~26 ranked predictions. Only the first
    (highest-confidence) one is a label; the rest are the model's uncertainty, not
    additional pictures or additional labels.
    """
    cls = _Cls([_Pred("photograph", 0.48), _Pred("icon", 0.27), _Pred("logo", 0.10)])
    labels: dict[str, int] = {}
    _count_top_label(cls, labels)
    assert labels == {"photograph": 1}


def test_counts_accumulate_across_multiple_pictures():
    """Measured on Year 2: 13 pictures resolved to {'photograph': 8, 'logo': 2, ...} --
    a real label distribution, not a per-picture dump.
    """
    labels: dict[str, int] = {}
    _count_top_label(_Cls([_Pred("photograph", 0.9)]), labels)
    _count_top_label(_Cls([_Pred("photograph", 0.7)]), labels)
    _count_top_label(_Cls([_Pred("logo", 0.8)]), labels)
    assert labels == {"photograph": 2, "logo": 1}


def test_a_picture_with_no_classification_is_skipped_not_crashed():
    """Not every DocItem carries classification metadata; None must be a no-op."""
    labels: dict[str, int] = {}
    _count_top_label(None, labels)
    assert labels == {}


def test_an_empty_prediction_list_is_skipped_not_crashed():
    labels: dict[str, int] = {}
    _count_top_label(_Cls([]), labels)
    assert labels == {}


def test_the_whole_object_is_never_used_as_a_dict_key():
    """The exact regression: stringifying the container instead of reading .class_name
    off predictions[0] produced one giant repr string as a label.
    """
    cls = _Cls([_Pred("table", 0.6)])
    labels: dict[str, int] = {}
    _count_top_label(cls, labels)
    assert all(len(k) < 40 for k in labels), "a label key should be a short class name"
    assert "predictions=" not in "".join(labels)


# ── per-picture page/location records ──────────────────────────────────────────────────
# Confirmed against the real Year 5 PDF (see convert_docling.py's module docstring): 32 of
# 33 pictures clear 5% of page area, and of those, exactly two are worth extraction -- a
# page 4 bar_chart and a page 5 screenshot_from_computer (the GHG dashboard, 0.543
# confidence). Both were hand-confirmed as the report's only two data-bearing images.

def test_a_picture_record_carries_page_bbox_and_area_fraction():
    rec = _picture_record(4, (49.2, 228.0, 496.0, 46.5), "CoordOrigin.BOTTOMLEFT",
                          page_w=612.0, page_h=792.0, cls=_Cls([_Pred("bar_chart", 0.997)]))
    assert rec["page_no"] == 4
    assert rec["bbox"] == [49.2, 228.0, 496.0, 46.5]
    assert rec["coord_origin"] == "CoordOrigin.BOTTOMLEFT"
    assert 0 < rec["area_frac"] < 1
    assert rec["top_label"] == "bar_chart"
    assert rec["top_conf"] == 0.997


def test_a_bar_chart_is_flagged_worth_extraction():
    rec = _picture_record(4, (0, 0, 100, 100), "x", 612.0, 792.0,
                          _Cls([_Pred("bar_chart", 0.997)]))
    assert rec["worth_extraction"] is True


def test_screenshot_from_computer_is_flagged_worth_extraction():
    """The real gap this pins: the Year 5 GHG dashboard classifies as
    screenshot_from_computer, not as any chart label. A chart-only allowlist would have
    silently dropped the one image most worth the extraction cost it exists to gate.
    """
    assert "screenshot_from_computer" in CLASSIFY_LABELS
    rec = _picture_record(5, (0, 0, 100, 100), "x", 612.0, 792.0,
                          _Cls([_Pred("screenshot_from_computer", 0.543)]))
    assert rec["worth_extraction"] is True


def test_a_photograph_is_not_flagged_worth_extraction():
    """Photographs are most of a report's pictures and carry no data -- they must not
    trigger a vision-extraction call.
    """
    rec = _picture_record(6, (0, 0, 100, 100), "x", 612.0, 792.0,
                          _Cls([_Pred("photograph", 0.98)]))
    assert rec["worth_extraction"] is False


def test_a_picture_with_no_classification_is_not_worth_extraction_but_does_not_crash():
    rec = _picture_record(1, (0, 0, 10, 10), "x", 612.0, 792.0, None)
    assert rec["worth_extraction"] is False
    assert rec["top_label"] is None


def test_a_missing_page_size_yields_no_area_fraction_rather_than_a_zero_division():
    rec = _picture_record(1, (0, 0, 10, 10), "x", page_w=None, page_h=None,
                          cls=_Cls([_Pred("photograph", 0.9)]))
    assert rec["area_frac"] is None
