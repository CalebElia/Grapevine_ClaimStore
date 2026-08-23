"""Legislative clauses that end on a conjunction by convention, not by truncation.

The CAP closes with two pages of the council resolution that adopted it, and every clause
ends the same way:

    "...WHEREAS, the Plan outlines the actions required to achieve carbon neutrality; and"
    "...directing the PCCN to create and realize the 2030 Carbon Neutral Ann Arbor Plan; and"

The truncation check flags them because the last word is "and". They are complete. This is
not a one-off to clear by hand either: it is the grammar of resolutions, ordinances and
findings, and the corpus is about to take on a lot more council material.
"""
from __future__ import annotations

from pipeline.quality_gate import is_enumerated_clause


def test_a_clause_ending_in_semicolon_and_is_complete():
    assert is_enumerated_clause(
        "WHEREAS, the Plan outlines the actions required to achieve neutrality; and")
    assert is_enumerated_clause("RESOLVED, that the City Administrator report back; and")


def test_semicolon_or_counts_too():
    assert is_enumerated_clause("...whichever the Council determines to be appropriate; or")


def test_an_ordinary_sentence_ending_in_and_is_still_truncated():
    """The check must keep catching the real thing: no semicolon, no exemption."""
    assert not is_enumerated_clause("Senator Stabenow, Senator Peters, and")
    assert not is_enumerated_clause("The Greenbelt reached 7,600 acres and")


def test_a_semicolon_earlier_in_the_line_does_not_exempt_it():
    """Only a semicolon IMMEDIATELY before the conjunction is the enumeration marker."""
    assert not is_enumerated_clause("Chapter; The American Institute of")
    assert not is_enumerated_clause("one; two; three and")


def test_whitespace_and_empty_input_are_safe():
    assert is_enumerated_clause("something ;  and  ")
    assert not is_enumerated_clause("")
    assert not is_enumerated_clause(None)
