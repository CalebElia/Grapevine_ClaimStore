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

from pipeline.convert_docling import _count_top_label


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
