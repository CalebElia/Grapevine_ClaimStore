"""Rejoining a list entry that wrapped across a line.

rejoin_open_sentences requires the continuation to begin LOWERCASE, and says why: "a
well-formed list item begins with a capital, so a lowercase opening is not a new item under
any reading". The CAP breaks that assumption, because its wrapped entries continue into
proper nouns:

    "• Ann Arbor – Ypsilanti Regional Chamber of"   +   "Commerce"
    "The American Institute of"                     +   "Architects Huron Valley Chapter"
    "Senator Stabenow, Senator Peters, and"         +   "Congressman Dingell"

The stronger evidence is the FIRST block, not the second: a complete list entry never ends on
"of", "and" or "with". That is the same _DANGLING set the truncation check already uses to
decide something is unfinished -- so the check that RAISES the warning and the rule that
CLEARS it agree by construction.
"""
from __future__ import annotations

from pipeline.convert_blocks import continues_dangling_entry


def test_a_list_entry_ending_on_a_preposition_is_unfinished():
    assert continues_dangling_entry("• Ann Arbor – Ypsilanti Regional Chamber of", "Commerce")
    assert continues_dangling_entry("The American Institute of", "Architects Huron Valley")
    assert continues_dangling_entry("Senator Stabenow, Senator Peters, and", "Congressman")
    assert continues_dangling_entry("Others as co-determined with", "the community")


def test_two_complete_entries_are_never_merged():
    """The failure this must not cause: 'Ann Arbor SPARK' and 'Public Services' are separate
    collaborators and joining them would invent an organisation."""
    assert not continues_dangling_entry("Ann Arbor SPARK", "Public Services")
    assert not continues_dangling_entry("DTE Energy", "Consumers Energy")
    assert not continues_dangling_entry("Local banks", "Ecology Center")


def test_a_finished_sentence_is_not_continued():
    """Terminal punctuation ends it whatever follows."""
    assert not continues_dangling_entry("Few equity impacts.", "Assumptions")
    assert not continues_dangling_entry("Launch the committee:", "2020")


def test_a_dangling_word_inside_the_entry_does_not_count():
    """Only the LAST word decides. 'Office of Sustainability' contains 'of' and is finished."""
    assert not continues_dangling_entry("Office of Sustainability", "Ann Arbor SPARK")


def test_an_empty_continuation_is_refused():
    assert not continues_dangling_entry("Regional Chamber of", "")
    assert not continues_dangling_entry("", "Commerce")


def test_a_continuation_that_is_itself_a_bullet_is_refused():
    """A new bullet marker is a new entry however the previous one ended."""
    assert not continues_dangling_entry("Regional Chamber of", "• Ann Arbor SPARK")


def test_a_continuation_that_is_itself_unfinished_is_refused():
    """THE REGRESSION THIS CAUSED, caught by an existing test. A block that ends on a
    dangling word is its OWN unfinished entry and cannot be another block's completion --
    otherwise two open bullets and one continuation collapse into a single sentence, and
    nothing records which of the two the continuation actually belonged to.

    "Commerce" completes something. "Another bullet that also ends open and" does not."""
    assert not continues_dangling_entry("The Greenbelt reached 7,600 acres and",
                                        "Another bullet that also ends open and")
    assert continues_dangling_entry("The Greenbelt reached 7,600 acres and",
                                    "natural areas permanently protected.")
