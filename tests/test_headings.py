"""Heading level from the document's own contents, and what is not a heading at all.

Docling marks 604 blocks as SectionHeaderItem in the CAP. Some are headings the contents
page names, some are real sub-headings it does not, and a few are not headings: a letter's
salutation, its sign-off, and one ordinary paragraph.

EVERY RULE HERE WAS CALIBRATED AGAINST ALL 604, not against an example. The comma/dash test
fires on exactly two blocks; the multi-sentence test on exactly one.
"""
from __future__ import annotations

from pipeline.headings import heading_level, is_heading_shaped, match_toc, norm_heading

_TOC = [
    {"title": "Welcome Letter", "level": 1},
    {"title": "The Living Carbon Neutrality Strategy", "level": 1},
    {"title": "Strategy 1: Power Our Electrical Grid with 100% Renewable Energy", "level": 2},
    {"title": "Implement Community Choice Aggregation", "level": 3},
]


def test_a_salutation_is_not_a_heading():
    """"Friends –" opens the welcome letter. Across all 604 blocks, ending on a comma or a
    dash fires on exactly this and the sign-off below -- no false positives."""
    assert not is_heading_shaped("Friends –")


def test_a_sign_off_is_not_a_heading():
    assert not is_heading_shaped("Sincerely,")


def test_a_paragraph_of_prose_is_not_a_heading():
    """A sentence break inside the text: "...in the Plan. In total, 10 co-benefits..."."""
    assert not is_heading_shaped(
        "Additionally, we have identified an array of co-benefits associated with each "
        "action in the Plan. In total, 10 co-benefits were identified, including:")


def test_a_numbered_action_heading_survives():
    """THE FALSE POSITIVE THIS AVOIDS. "1. IMPLEMENT COMMUNITY CHOICE AGGREGATION" contains
    a period followed by a capital, so a naive multi-sentence test demoted 44 of the CAP's
    45 flagged blocks -- every one of its named actions. A period preceded by a DIGIT is an
    ordinal, not a sentence."""
    assert is_heading_shaped("1. IMPLEMENT COMMUNITY CHOICE AGGREGATION")
    assert is_heading_shaped("2. ELECTRIFY BUSES")


def test_a_long_heading_is_still_a_heading():
    """Length cannot demote: the CAP's longest contents entry is 23 words."""
    assert is_heading_shaped(
        "Strategy 3: Significantly Improve the Energy Efficiency in our Homes, "
        "Businesses, Schools, Places of Worship, Recreational Sites and Government "
        "Facilities")


# --- matching against the contents ---------------------------------------------------------

def test_an_exact_title_matches_its_entry():
    assert match_toc("Welcome Letter", _TOC)["level"] == 1


def test_case_and_punctuation_do_not_matter():
    assert match_toc("WELCOME LETTER", _TOC)["level"] == 1


def test_the_body_s_numbered_form_matches_the_contents_title():
    """The contents says "Implement Community Choice Aggregation"; the body writes
    "1. IMPLEMENT COMMUNITY CHOICE AGGREGATION". Stripping the ordinal and matching by
    containment lifted reachable entries from 30 of 72 to 65."""
    assert match_toc("1. IMPLEMENT COMMUNITY CHOICE AGGREGATION", _TOC)["level"] == 3


def test_a_heading_the_contents_does_not_name_has_no_match():
    assert match_toc("Vision for Implementing Community Choice Aggregation", _TOC) is None


def test_normalisation_strips_ordinals_and_punctuation():
    assert norm_heading("1. IMPLEMENT CCA!") == norm_heading("Implement CCA")


# --- the level a block ends up with ----------------------------------------------------------

def test_a_contents_heading_takes_the_contents_level():
    assert heading_level("Welcome Letter", _TOC, deepest=3) == 1
    assert heading_level("1. IMPLEMENT COMMUNITY CHOICE AGGREGATION", _TOC, deepest=3) == 3


def test_a_heading_the_contents_omits_sits_below_the_deepest_named_level():
    """Sub-headings inside a section -- "Party Responsible for Implementation" -- are real
    headings the contents does not name. They belong beneath everything it does."""
    assert heading_level("Party Responsible for Implementation", _TOC, deepest=3) == 4


def test_something_that_is_not_a_heading_gets_no_level():
    assert heading_level("Sincerely,", _TOC, deepest=3) is None


def test_a_sentence_ending_in_a_colon_is_a_lead_in_not_a_heading():
    """Caleb's rule from Year 1: "That colon is a huge, obvious clue for 'here comes a
    list.'" The CAP has two -- "Three public surveys were administered as part of A²ZERO:"
    introduces the surveys beneath it."""
    assert not is_heading_shaped("Three public surveys were administered as part of A²ZERO:")


def test_a_heading_that_merely_ends_in_a_colon_survives():
    """THE PAIR THAT KEEPS THIS HONEST. "STRATEGY 3:" and "OTHER STRATEGIES:" also end in a
    colon and are real headings. A lead-in is a SENTENCE: several words, most of them
    lowercase. A heading is a label."""
    assert is_heading_shaped("STRATEGY 3:")
    assert is_heading_shaped("OTHER STRATEGIES:")
    assert is_heading_shaped("Party Responsible for Implementation:")
