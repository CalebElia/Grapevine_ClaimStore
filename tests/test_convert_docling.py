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


# ── per-block records: the text spine for convert_blocks.py ────────────────────────────
# Confirmed on the real Year 5 document: all 13 picture captions ALSO appear in the main
# item stream as ordinary TextItems. So a caption is not automatically distinguishable
# from body prose -- it has to be tagged by matching its self_ref against the refs its
# picture declares, or captions silently render as stray sentences (exactly what the
# review flagged: "This is a photo caption to a photo that was dropped").

from pipeline.convert_docling import _block_record


def test_a_body_block_is_not_marked_as_a_caption():
    rec = _block_record(kind="TextItem", page_no=3, bbox=(1.0, 2.0, 3.0, 4.0),
                        coord_origin="CoordOrigin.BOTTOMLEFT", page_w=612.0, page_h=792.0,
                        text="Ordinary body prose.", self_ref="#/texts/5",
                        caption_refs=set())
    assert rec["caption_for"] is None
    assert rec["kind"] == "TextItem"
    assert rec["page_no"] == 3


def test_a_caption_block_is_tagged_with_the_picture_it_belongs_to():
    """Real case: '#/texts/19' captions the page 3 photograph."""
    rec = _block_record(kind="TextItem", page_no=3, bbox=(1.0, 2.0, 3.0, 4.0),
                        coord_origin="CoordOrigin.BOTTOMLEFT", page_w=612.0, page_h=792.0,
                        text="The Office of Sustainability and Innovations pictured...",
                        self_ref="#/texts/19", caption_refs={"#/texts/19": "#/pictures/2"})
    assert rec["caption_for"] == "#/pictures/2"


def test_a_block_carries_the_geometry_convert_blocks_needs_to_crop_it():
    rec = _block_record(kind="ListItem", page_no=6, bbox=(54.0, 408.0, 493.0, 396.0),
                        coord_origin="CoordOrigin.BOTTOMLEFT", page_w=612.0, page_h=792.0,
                        text="- Our Solarize program reached 5.4MW", self_ref="#/texts/7",
                        caption_refs={})
    assert rec["bbox"] == [54.0, 408.0, 493.0, 396.0]
    assert rec["coord_origin"] == "CoordOrigin.BOTTOMLEFT"
    assert rec["page_h"] == 792.0 and rec["page_w"] == 612.0


def test_doclings_own_text_is_kept_alongside_for_cross_checking():
    """pdfplumber supplies the characters, but keeping Docling's reading of the same
    block makes a converter disagreement detectable instead of invisible.
    """
    rec = _block_record(kind="TextItem", page_no=1, bbox=(0.0, 1.0, 1.0, 0.0),
                        coord_origin="x", page_w=1.0, page_h=1.0,
                        text="A 2 ZERO Annual Report", self_ref="#/texts/0", caption_refs={})
    assert rec["docling_text"] == "A 2 ZERO Annual Report"


# ── OCR engine selection ───────────────────────────────────────────────────────────────
# Benchmarked on Year 2 (image-based). force_full_page_ocr is the variable that decides
# correctness -- both local engines drop wrapped continuation lines without it and are
# perfect with it -- so it is not optional and not exposed as a choice.
#
# Engine choice is only about speed and availability, because at full page the two local
# engines score IDENTICALLY: 0 truncations, 3/3 recovered tails, and all 15 currency
# figures matching the human-healed reference exactly.
#     ocrmac/full-page     24.0s   macOS Vision, no install beyond the wrapper
#     rapidocr/full-page   60.4s   pure python, runs anywhere
# Azure CU is faster still (6.8s) but is a paid network call that sends the document off
# the machine, and it reads text INSIDE images -- 233 extra token occurrences on Year 2,
# only 37% of them dictionary words ("aaid", "abost", "arnage"), none present in the human
# reference. That is garbled screenshot chrome, not recovered prose, so CU is opt-in
# rather than an automatic fallback.

from pipeline.convert_docling import choose_ocr_engine


def test_macos_with_vision_available_prefers_ocrmac():
    assert choose_ocr_engine(is_macos=True, ocrmac_available=True) == "ocrmac"


def test_macos_without_the_wrapper_falls_back_to_rapidocr():
    """ocrmac needs a pip wrapper around the system framework; absent it, the free
    cross-platform engine scores the same and merely takes longer."""
    assert choose_ocr_engine(is_macos=False, ocrmac_available=False) == "rapidocr"
    assert choose_ocr_engine(is_macos=True, ocrmac_available=False) == "rapidocr"


def test_a_non_mac_never_selects_ocrmac_even_if_the_module_imports():
    """The wrapper can install on other platforms; the Vision framework cannot."""
    assert choose_ocr_engine(is_macos=False, ocrmac_available=True) == "rapidocr"


def test_an_explicit_choice_always_wins():
    """Batch runs may prefer CU's 6.8s, accepting the cost and the image noise."""
    assert choose_ocr_engine(is_macos=True, ocrmac_available=True,
                             requested="rapidocr") == "rapidocr"
