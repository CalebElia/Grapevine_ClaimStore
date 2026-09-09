"""Numbered lists that arrive as bullets.

The CAP names its seven strategies as an ordered list -- the text layer reads
"1. Power our electrical grid with 100% renewable energy" -- and the conversion rendered
every one as a dash. Docling is what drops the numeral: it recognises the line as a ListItem
and consumes the marker, so its docling_text begins "Power our...". Trimming the pdfplumber
read to match Docling then loses it for good.

The number is not decoration. "Strategy 1" is referred to by number throughout the plan, by
its own contents page, and by five annual reports.
"""
from __future__ import annotations

from pipeline.convert_blocks import leading_ordinal


def test_an_ordinal_docling_dropped_is_recovered():
    assert leading_ordinal("1. Power our electrical grid with 100% renewable energy",
                           "Power our electrical grid with 100% renewable energy") == 1
    assert leading_ordinal("7. Other", "Other") == 7


def test_a_close_paren_marker_counts_too():
    assert leading_ordinal("3) Reduce the miles we travel", "Reduce the miles we travel") == 3


def test_an_ordinal_docling_kept_is_not_doubled():
    """If Docling kept the marker the text already has it, and returning it would render
    "1. 1. Power our..."."""
    assert leading_ordinal("1. Power our grid", "1. Power our grid") is None


def test_a_number_that_is_part_of_the_sentence_is_not_an_ordinal():
    """"2030 is the target year" starts with a number and is not a numbered item. Docling
    keeps it, so the pdfplumber and Docling reads agree and nothing is recovered."""
    assert leading_ordinal("2030 is the target year", "2030 is the target year") is None


def test_a_bare_bullet_is_not_an_ordinal():
    assert leading_ordinal("• Power our grid", "Power our grid") is None
    assert leading_ordinal("- Power our grid", "Power our grid") is None


def test_a_mismatch_that_is_not_a_leading_number_is_ignored():
    """Only a leading ordinal is recovered here; every other difference between the two
    reads is decided by the existing trim and choose_block_text."""
    assert leading_ordinal("The quick brown fox", "quick brown fox") is None


def test_missing_or_empty_input_is_safe():
    assert leading_ordinal("", "") is None
    assert leading_ordinal(None, None) is None


def test_an_implausibly_large_number_is_not_an_ordinal():
    """A list does not start at 4096, but a year or a quantity might lead a line."""
    assert leading_ordinal("4096 homes retrofitted", "homes retrofitted") is None
