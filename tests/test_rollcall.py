"""Roll-call extraction must never promote an absent person to present.

Attendance parsed from minutes is presence evidence for speaker naming, so a false
"present" is not a cosmetic bug -- it licenses attributing speech to someone who was not
in the room, which is the exact class of failure (`presence vs existence`) the naming
pipeline is built to prevent.

Legistar renders the same roll call in at least three layouts, because the HTML table's
column order leaks into the extracted text. The first implementation matched
`Present:(.*?)Absent` and silently reported 15 present / 0 absent for a meeting with 13
present and 3 absent -- it swallowed the absent list. Every layout below is taken from a
real Sustainability Commission document, and LAYOUT_B is that exact regression.

The parse is validated against the counts the clerk wrote down, so a parse that goes wrong
in a way these fixtures do not anticipate still reports `reliable: False` rather than data.
"""

from __future__ import annotations

import pytest

from pipeline.rollcall import parse

# Layout A -- label, count, names (2026-01-13)
LAYOUT_A = """ROLL CALL
Present: 13 - Jonathan Overpeck, Rita Mitchell, Sara Nedrich, Noah Levin, Mike Berkowitz,
Jon Mallek, Stephen C Brown, Christopher L. Graham, Anya Dale, Brooks Curtis, Olivia Smyth,
Mallika Kothari, and Mobin Mazloomian Absent: 3 - Mark Hanss, Carlene Colvin-Garcia, and
Jennifer Cornell
APPROVAL OF AGENDA"""

# Layout B -- count, label, names / then count, names, label (2026-03-10).
# This is the layout that defeated the original greedy regex.
LAYOUT_B = """ROLL CALL
13 -
Present
:
Jonathan Overpeck, Rita Mitchell, Sara Nedrich, Noah Levin, Carlene Colvin-Garcia,
Mike Berkowitz, Jon Mallek, Jennifer Cornell, Christopher L. Graham, Brooks Curtis,
Olivia Smyth, Mallika Kothari, and Mobin Mazloomian
3 -
Mark Hanss, Stephen C Brown, and Anya Dale
Absent
:
APPROVAL OF AGENDA"""

# Layout C -- both counts hoisted ahead of both labels (2026-05-12)
LAYOUT_C = """ROLL CALL
10 - 5 - Present : Jonathan Overpeck, Rita Mitchell, Mike Berkowitz, Jon Mallek,
Jennifer Cornell, Stephen C Brown, Christopher L. Graham, Anya Dale, Brooks Curtis, and
Olivia Smyth Absent : Sara Nedrich, Noah Levin, Carlene Colvin-Garcia, Mallika Kothari,
and Mobin Mazloomian
PUBLIC COMMENT"""


@pytest.mark.parametrize("text,n_present,n_absent", [
    (LAYOUT_A, 13, 3),
    (LAYOUT_B, 13, 3),
    (LAYOUT_C, 10, 5),
])
def test_every_layout_parses_with_correct_counts(text, n_present, n_absent):
    r = parse(text)
    assert r["reliable"], r["reason"]
    assert len(r["present"]) == n_present
    assert len(r["absent"]) == n_absent


@pytest.mark.parametrize("text,absent_name", [
    (LAYOUT_A, "Carlene Colvin-Garcia"),
    (LAYOUT_B, "Stephen C Brown"),
    (LAYOUT_C, "Noah Levin"),
])
def test_absent_never_leaks_into_present(text, absent_name):
    """The load-bearing assertion. A regression here fabricates attributions."""
    r = parse(text)
    assert absent_name in r["absent"]
    assert absent_name not in r["present"]


def test_same_person_is_present_in_one_meeting_and_absent_in_another():
    """Carlene Colvin-Garcia is absent in layout A and present in layout B.

    Guards against any implementation that caches or globally infers membership rather
    than reading each meeting's own roll call.
    """
    assert "Carlene Colvin-Garcia" in parse(LAYOUT_A)["absent"]
    assert "Carlene Colvin-Garcia" in parse(LAYOUT_B)["present"]


def test_count_mismatch_is_reported_unreliable_not_returned_as_data():
    """Declared 13, only 3 names present -- must refuse rather than under-report."""
    bad = """ROLL CALL
Present: 13 - Jonathan Overpeck, Rita Mitchell, and Sara Nedrich Absent: 3 - Mark Hanss,
Anya Dale, and Stephen C Brown
APPROVAL OF AGENDA"""
    r = parse(bad)
    assert not r["reliable"]
    assert r["present"] == []
    assert "declared 13" in r["reason"]


def test_missing_roll_call_block_is_unreliable():
    r = parse("APPROVAL OF AGENDA\nA motion was made by Commissioner Graham.")
    assert not r["reliable"]
    assert r["present"] == [] and r["absent"] == []


def test_sequence_length_disagreement_is_refused():
    """Two counts but one label: the zip would silently drop a block, so refuse."""
    r = parse("ROLL CALL\n10 - 5 - Present : Jon Mallek, and Rita Mitchell\nPUBLIC COMMENT")
    assert not r["reliable"]
