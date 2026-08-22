"""Applying a recorded correction to the converted text without breaking stored claims.

A claim is a character range in the canonical text. Any edit that changes length moves every
span after it, and a moved span still round-trips perfectly against the text it was written
from while pointing at different words in the text that is now there. That is the silent
failure this module exists to prevent, so almost all of it is refusals.
"""
from __future__ import annotations

import pytest

from pipeline.write_back import Edit, locate_unique, minimal_edit, shift_spans


# --- minimal_edit: the recorded reading is a WINDOW, not a replacement -------------------

def test_the_common_prefix_and_suffix_are_not_part_of_the_edit():
    """THE HAZARD THIS EXISTS FOR. The semantic pass recorded token '215' resolving to
    '21st, 2022', because the second arm's reading was captured as a WINDOW. Substituting
    the recorded string for the token yields 'March 21st, 2022, 2022'."""
    old, new = minimal_edit("March 215, 2022", "March 21st, 2022")
    assert (old, new) == ("5", "st")


def test_identical_readings_produce_no_edit():
    assert minimal_edit("Care program", "Care program") == ("", "")


def test_a_pure_insertion_is_expressed_as_one():
    old, new = minimal_edit("Careprogram", "Care program")
    assert old == "" and new == " "


# --- locate_unique: an ambiguous anchor is refused --------------------------------------

def test_a_needle_appearing_once_is_located():
    assert locate_unique("the cat sat", "cat") == 4


def test_a_needle_appearing_twice_is_refused():
    """Two candidates means the edit could land in the wrong sentence. Refusing is the only
    safe answer; there is no way to tell which the reviewer meant."""
    assert locate_unique("cat and cat", "cat") is None


def test_a_needle_that_is_absent_is_refused():
    assert locate_unique("the dog sat", "cat") is None


# --- shift_spans: the part that protects stored claims ----------------------------------

def test_a_span_entirely_before_the_edit_does_not_move():
    e = [Edit(at=100, old_len=11, new_len=12)]
    assert shift_spans([(10, 20)], e) == [(10, 20, "unchanged")]


def test_a_span_entirely_after_the_edit_shifts_by_the_delta():
    """Section 148's 41 claims start at 21621; the careprogram edit is at ~19145 and adds
    one character. Unmigrated, every one of them reads one character off."""
    e = [Edit(at=19145, old_len=11, new_len=12)]
    assert shift_spans([(21621, 21700)], e) == [(21622, 21701, "shifted")]


def test_a_span_containing_the_edit_is_flagged_not_silently_stretched():
    """A claim whose own verbatim contains the corrected words cannot be fixed by
    arithmetic -- its verbatim is now wrong too. Arithmetic would hide that."""
    e = [Edit(at=150, old_len=11, new_len=12)]
    assert shift_spans([(100, 200)], e) == [(100, 200, "needs_reanchor")]


def test_several_edits_accumulate_in_order():
    e = [Edit(at=100, old_len=1, new_len=3), Edit(at=200, old_len=1, new_len=3)]
    assert shift_spans([(300, 310)], e) == [(304, 314, "shifted")]


def test_a_deletion_shifts_backwards():
    e = [Edit(at=100, old_len=5, new_len=1)]
    assert shift_spans([(300, 310)], e) == [(296, 306, "shifted")]


def test_edits_must_be_supplied_in_ascending_position():
    """Accumulating deltas out of order silently mis-shifts everything after the first
    inversion."""
    with pytest.raises(ValueError):
        shift_spans([(300, 310)], [Edit(at=200, old_len=1, new_len=2),
                                   Edit(at=100, old_len=1, new_len=2)])


# --- the canonical delta is measured from the rebuilt text, never assumed ---------------

from pipeline.write_back import measure_edit

_MD = "# R\n\n<!-- p.1 -->\n\nLaunched the Yard Careprogram, a restructuring.\n"


def test_the_edit_is_measured_by_rebuilding_the_canonical_text():
    """The markdown and the canonical text have different coordinates -- comments, headings
    and block separators sit between them -- so the offset an edit lands on in the file is
    not the offset a span lives at. The delta has to come from the rebuilt text."""
    new_md = _MD.replace("Careprogram", "Care program")
    e, old_core, new_core = measure_edit(_MD, new_md)
    assert (old_core, new_core) == ("", " ")
    assert e.delta == 1
    # positioned in CANONICAL coordinates, which exclude the comment and the heading markup
    from pipeline.canonical import build
    assert build(_MD).text[:e.at].endswith("Yard Care")


def test_an_edit_with_side_effects_is_detectable():
    """If editing the markdown changed more than the intended words -- a heading absorbed,
    a block merged -- the measured region will not match what was intended, and the caller
    must be able to see that rather than migrate spans against a wrong delta."""
    new_md = _MD.replace("Careprogram", "Care program").replace("restructuring", "redesign")
    e, old_core, new_core = measure_edit(_MD, new_md)
    assert old_core != "" or new_core != " "
    assert e.delta != 1


def test_an_unchanged_rebuild_measures_nothing():
    assert measure_edit(_MD, _MD) == (None, "", "")


# --- plan_correction: every refusal that protects the store -----------------------------

from pipeline.write_back import plan_correction

_DOC = ("# R\n\n<!-- p.1 -->\n\nLaunched the Yard Careprogram, a restructuring.\n\n"
        "<!-- p.2 -->\n\nA later block that claims may point into.\n")


def test_a_clean_correction_plans_successfully():
    p = plan_correction(_DOC, "Yard Careprogram, a", "Yard Care program, a")
    assert p["ok"], p
    assert "Yard Care program," in p["new_md"]
    assert p["edit"].delta == 1


def test_an_anchor_that_appears_twice_is_refused():
    md = _DOC + "\nLaunched the Yard Careprogram, a restructuring.\n"
    p = plan_correction(md, "Yard Careprogram, a", "Yard Care program, a")
    assert not p["ok"]
    assert "once" in p["why"]


def test_an_anchor_that_is_absent_is_refused():
    p = plan_correction(_DOC, "Not In The Document", "Something Else")
    assert not p["ok"]


def test_a_replacement_that_changes_more_than_intended_is_refused():
    """If the substitution also merged blocks or absorbed a heading, the canonical delta is
    not the one we reasoned about, and migrating spans against it would be worse than
    doing nothing."""
    p = plan_correction(_DOC, "Yard Careprogram, a restructuring.",
                        "Yard Care program, a redesign.\n\n## Surprise Heading")
    assert not p["ok"]
    assert "more than" in p["why"] or "side effect" in p["why"]


def test_a_correction_that_changes_nothing_is_refused():
    p = plan_correction(_DOC, "Yard Careprogram, a", "Yard Careprogram, a")
    assert not p["ok"]
    assert "nothing" in p["why"].lower()


def test_populated_extra_spans_block_the_write():
    """claims.extra_spans holds additional ranges this migration does not touch. It is
    empty across the whole corpus today, which is exactly why a guard is needed: the day it
    is populated, a silent partial migration is the failure nobody would notice."""
    from pipeline.write_back import unmigratable_span_columns
    assert unmigratable_span_columns([{"id": 1, "extra_spans": None}]) == []
    assert unmigratable_span_columns([{"id": 1, "extra_spans": []}]) == []
    assert unmigratable_span_columns([{"id": 7, "extra_spans": [[1, 2]]}]) == [7]


# --- the file and the database must move together ---------------------------------------

from pipeline.write_back import atomic_apply


def test_the_file_is_restored_when_the_database_work_fails(tmp_path):
    """THE INCIDENT THIS ENCODES. The first version wrote the markdown, then ran the
    database updates. The final insert raised, the transaction rolled back, and the file
    kept its correction -- leaving 41 claims pointing one character off, silently, in a
    module whose entire purpose is preventing exactly that.

    The file write and the commit are one unit or they are a corruption waiting for an
    exception."""
    p = tmp_path / "doc.md"
    p.write_text("original text")

    def boom():
        raise RuntimeError("database said no")

    with pytest.raises(RuntimeError):
        atomic_apply(p, "corrected text", boom)
    assert p.read_text() == "original text"


def test_the_file_is_written_when_the_database_work_succeeds(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("original text")
    seen = []
    atomic_apply(p, "corrected text", lambda: seen.append("committed"))
    assert p.read_text() == "corrected text"
    assert seen == ["committed"]


def test_the_database_work_runs_before_the_file_is_left_changed(tmp_path):
    """Ordering matters: the callable must observe the file already updated, so anything
    that re-reads and re-hashes it inside the transaction sees what will be committed."""
    p = tmp_path / "doc.md"
    p.write_text("original")
    observed = []
    atomic_apply(p, "corrected", lambda: observed.append(p.read_text()))
    assert observed == ["corrected"]
