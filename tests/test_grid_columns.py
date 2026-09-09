"""Labels in an icon grid share a baseline and are not one line.

The CAP lists its ten co-benefits as an icon grid. Grouping the leftover words into lines by
baseline merged each ROW of the grid into a single run:

    "Improves local resilience Improves public health Cost savings accrued Supports
     biodiversity preservation Benefits the most vulnerable..."

which is what Caleb saw at line 250. The labels are not adjacent: measured on page 7, the
horizontal gaps between them are 127 to 163 points, where a word gap in 10pt type is two to
five. A gap that size is a column boundary, and the same test separates the two columns of a
two-column page.
"""
from __future__ import annotations

from pipeline.convert_blocks import split_row_on_gaps


def _w(text, x0, x1):
    return {"text": text, "x0": x0, "x1": x1, "top": 100, "bottom": 110, "size": 10.0}


def test_a_wide_gap_splits_a_row_into_columns():
    """The measured case: 147pt between "jobs" and "Benefits" on page 7, in 10pt type."""
    row = [_w("Creates", 70, 110), _w("jobs", 112, 135),
           _w("Benefits", 282, 330), _w("the", 332, 350)]
    got = split_row_on_gaps(row)
    assert [" ".join(w["text"] for w in g) for g in got] == ["Creates jobs", "Benefits the"]


def test_ordinary_word_spacing_is_never_split():
    row = [_w("the", 70, 85), _w("quick", 88, 120), _w("brown", 123, 160)]
    assert len(split_row_on_gaps(row)) == 1


def test_the_threshold_scales_with_the_type():
    """127pt is a column boundary in 10pt type and ordinary spacing in 60pt display."""
    small = [_w("a", 0, 10), {**_w("b", 137, 160), "size": 10.0}]
    big = [{**_w("a", 0, 10), "size": 60.0}, {**_w("b", 137, 160), "size": 60.0}]
    assert len(split_row_on_gaps(small)) == 2
    assert len(split_row_on_gaps(big)) == 1


def test_ordinary_prose_spacing_never_splits():
    """Real word gaps in this document run 1.5 to 5 points, nowhere near a column."""
    row = [_w("City", 10, 40), _w("officials", 43, 90), _w("break", 93, 120)]
    assert len(split_row_on_gaps(row)) == 1


def test_a_single_word_row_is_returned_whole():
    assert len(split_row_on_gaps([_w("Other", 70, 100)])) == 1


def test_an_empty_row_is_safe():
    assert split_row_on_gaps([]) == []


def test_the_tightest_real_gutter_still_splits():
    """CAP page 12, measured: a 64.3pt gutter between two columns of 10pt body text.

    This is the tightest genuine column boundary in the document, so it is what the
    threshold has to clear. Left column ends at x=266.9, right column starts at x=331.2.
    """
    row = [{**_w("our", 250, 266.9), "size": 10.0},
           {**_w("manner.", 331.2, 368.8), "size": 10.0}]
    assert len(split_row_on_gaps(row)) == 2


def test_a_wide_word_space_in_justified_text_does_not_split():
    """Justified prose stretches word spaces, but nowhere near five ems."""
    row = [{**_w("carbon", 100, 140), "size": 10.0},
           {**_w("neutrality", 152, 200), "size": 10.0}]
    assert len(split_row_on_gaps(row)) == 1
