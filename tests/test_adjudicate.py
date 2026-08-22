"""Mechanical adjudication of cross-arm conflicts, using glyph geometry as ground truth.

The evidence these tests encode, measured on AA_AnnualReport_2023.pdf page 12: inside the
token "Actgrant" the t->g gap is 2.16pt while every other gap in the token is 0.00pt, and
2.16 is the largest gap anywhere on that line. The PDF encodes no space CHARACTER there --
pdfplumber faithfully returns "Actgrant" -- but the glyphs are positioned with a real gap,
which is why an OCR arm reading pixels produced "Act grant".

Geometry is the third read, and unlike the text layer it cannot be lossy about spacing:
it IS the spacing.
"""
from __future__ import annotations

import pytest

from pipeline.adjudicate import (WINNER_PRIMARY, WINNER_SECOND, WINNER_UNRESOLVED,
                                 ambiguous_points, split_points, token_verdict)


# --- split_points: pure geometry ---------------------------------------------------------

def test_a_gap_far_larger_than_its_neighbours_is_a_space():
    """Measured from the real PDF: Actgrant = A c t | g r a n t."""
    gaps = [0.0, 0.0, 2.16, 0.0, 0.0, 0.0, 0.0]
    assert split_points(gaps, size=9.0) == [3]


def test_a_token_with_no_internal_gaps_is_one_word():
    """A genuine compound must not be split. 'watershed' is one word however much an OCR
    arm would like to make it two."""
    assert split_points([0.0] * 8, size=9.0) == []


def test_uniform_kerning_is_not_a_space():
    """Some fonts carry a small positive gap between every pair. A space has to stand out
    from its own token, not merely be non-zero."""
    assert split_points([0.6, 0.6, 0.6, 0.6], size=9.0) == []


def test_the_threshold_scales_with_font_size():
    """2.16pt is a space at 9pt and is not at 40pt display type."""
    gaps = [0.0, 0.0, 2.16, 0.0]
    assert split_points(gaps, size=9.0) == [3]
    assert split_points(gaps, size=40.0) == []


def test_two_spaces_in_one_token_are_both_found():
    assert split_points([0.0, 2.2, 0.0, 2.4, 0.0], size=9.0) == [2, 4]


# --- token_verdict: who wins -------------------------------------------------------------

def test_geometry_says_split_and_the_second_read_split_it_so_the_second_read_wins():
    """Class A. The primary is faithful to the text layer and the text layer is lossy."""
    w, why = token_verdict("actgrant", splits=["act", "grant"],
                           second_read_words={"act", "grant", "inflation", "reduction"})
    assert w == WINNER_SECOND
    assert "act grant" in why


def test_geometry_says_one_word_so_the_primary_wins():
    """Class B. The second read ran words together; geometry shows no gap."""
    w, why = token_verdict("successful", splits=["successful"],
                           second_read_words={"hostedasuccessfulsustainability"})
    assert w == WINNER_PRIMARY


def test_geometry_splits_but_the_second_read_disagrees_with_the_split():
    """Geometry says two words; the second read produced something else entirely. Nobody
    is corroborated, so this escalates rather than picking a winner."""
    w, _ = token_verdict("actgrant", splits=["act", "grant"],
                         second_read_words={"actual", "granted"})
    assert w == WINNER_UNRESOLVED


def test_no_geometry_available_is_unresolved_not_a_win():
    """Year 2 has no text layer, so no glyph run can be located. Absence of evidence must
    never be scored as evidence for the primary."""
    w, why = token_verdict("wet", splits=None, second_read_words={"we2"})
    assert w == WINNER_UNRESOLVED
    assert "no glyph" in why.lower()


def test_a_widely_tracked_token_is_not_all_spaces():
    """Display and small-caps type is often tracked wide enough that EVERY gap clears an
    absolute threshold. A token whose every gap looks like a space is not a token full of
    spaces -- it is a wide font, and splitting it would shatter one word into letters.
    A space has to stand out from its own token."""
    assert split_points([1.5] * 6, size=9.0) == []


def test_one_wide_gap_among_wide_kerning_is_still_found():
    """The guard must not blind the check on a tracked font: a clear outlier still splits."""
    assert split_points([1.5, 1.5, 4.5, 1.5, 1.5], size=9.0) == [3]


# --- the ambiguous band: geometry that decides nothing -----------------------------------

def test_a_gap_between_tight_and_a_space_is_ambiguous():
    """Measured on 'Careprogram' (Year 3, 11pt): the Care->program gap is 0.88pt where
    every other pair in the token is 0.00pt and a true space in the same document at the
    same size is 2.16-2.34pt. It is plainly not kerning and plainly not a full space.
    Geometry must say so rather than defaulting to the incumbent."""
    gaps = [0.0, 0.0, 0.0, 0.88, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert split_points(gaps, size=11.0) == []
    assert ambiguous_points(gaps, size=11.0) == [4]


def test_a_clean_space_is_not_also_ambiguous():
    gaps = [0.0, 0.0, 2.16, 0.0]
    assert split_points(gaps, size=11.0) == [3]
    assert ambiguous_points(gaps, size=11.0) == []


def test_true_kerning_is_not_ambiguous():
    """0.00pt gaps must not flood the escalation queue."""
    assert ambiguous_points([0.0] * 6, size=11.0) == []


def test_an_ambiguous_gap_makes_the_token_unresolved():
    """Even when the second read's split looks plausible, an ambiguous gap means geometry
    did not decide it -- and a plausible-looking answer is exactly what must not be
    rubber-stamped."""
    w, why = token_verdict("careprogram", splits=["careprogram"],
                           second_read_words={"care", "program"},
                           ambiguous=True)
    assert w == WINNER_UNRESOLVED
    assert "ambiguous" in why.lower()
