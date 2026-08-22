"""Footnotes: a numbered body at the foot of a page, and the marker that points at it.

Measured on the Year 2 report, page 8: the body text reads "In Year Two, we⁴:" and the foot
of the page carries a rule and "⁴ For more information on activities to support Strategy 4,
please contact Simi Barr (sbarr@a2gov.org)."

The bodies were already being captured -- tagged FURNITURE, which kept them out of
claim-bearing prose and was right as far as it went. What furniture cannot say is that a
block is a footnote, which number it carries, or which words in the prose point at it. Seven
of these name the officer responsible for each strategy, so the link is worth having.
"""
from __future__ import annotations

from pipeline.footnotes import parse_footnote_body, strip_reference

_BODY = ("4 For more information on activities to support Strategy 4, "
         "please contact Simi Barr (sbarr@a2gov.org).")


def test_a_numbered_block_is_recognised_as_a_footnote():
    got = parse_footnote_body(_BODY)
    assert got is not None
    assert got["number"] == 4
    assert got["text"].startswith("For more information")
    assert "4 For" not in got["text"]


def test_a_page_number_alone_is_not_a_footnote():
    """Page numbers are furniture too and are also bare integers. A footnote has a body."""
    assert parse_footnote_body("8") is None
    assert parse_footnote_body("  12  ") is None


def test_a_year_is_not_a_footnote_number():
    """'2021 - 2022 Annual Report' begins with digits and is a heading, not a footnote."""
    assert parse_footnote_body("2021 - 2022 Annual Report") is None


def test_a_sentence_beginning_with_a_numeral_is_not_a_footnote():
    """'250 residents registered as participants' is prose. A footnote marker is small and
    is followed by a capitalised sentence."""
    assert parse_footnote_body("250 residents registered as participants") is None


# --- the reference side ------------------------------------------------------------------

def test_a_marker_confirmed_by_the_second_arm_is_stripped():
    """The primary arm's OCR mangled the superscript into a letter: 'wet:' where the second
    arm read 'we4:'. The digit matches a footnote that exists on the page, so the trailing
    character is a marker and not part of the word."""
    text, refs = strip_reference("In Year Two, wet:", "In Year Two, we4:", {1, 2, 3, 4})
    assert text == "In Year Two, we:"
    assert refs == [4]


def test_a_marker_the_primary_dropped_entirely_is_still_recorded():
    """Four of the six markers vanished from the primary read rather than being mangled."""
    text, refs = strip_reference("In Year Two, we:", "In Year Two, we2:", {2})
    assert text == "In Year Two, we:"
    assert refs == [2]


def test_a_digit_with_no_matching_footnote_is_left_alone():
    """Corroboration is not enough on its own. If no footnote carries that number, the digit
    is something else -- a quantity, a year -- and removing it would delete real text."""
    text, refs = strip_reference("we saved 9:", "we saved 9:", {1, 2})
    assert text == "we saved 9:"
    assert refs == []


def test_text_the_arms_agree_on_is_untouched():
    text, refs = strip_reference("In Year Two, we:", "In Year Two, we:", {1, 2})
    assert text == "In Year Two, we:"
    assert refs == []


def test_a_real_word_is_never_truncated_to_make_a_marker():
    """The guard against the failure this whole area keeps producing: 'week' must not lose
    its 'k' because some footnote happens to be numbered 4."""
    text, refs = strip_reference("a week long", "a week long", {4})
    assert text == "a week long"
    assert refs == []


# --- canonical.py: footnotes are their own kind -----------------------------------------

from pipeline.canonical import build

_MD = """# R

<!-- p.8 -->

In Year Two, we:

<!-- FURNITURE: page foot -->
> 4 For more information on activities to support Strategy 4, please contact Simi Barr (sbarr@a2gov.org).

<!-- FURNITURE: page number -->
> 8
"""


def test_a_footnote_body_gets_its_own_kind_and_number():
    u = next(u for u in build(_MD).units if "For more information" in u.text)
    assert u.kind == "footnote"
    assert u.flags["footnote_number"] == 4


def test_other_furniture_is_still_furniture():
    u = next(u for u in build(_MD).units if u.text.strip() == "8")
    assert u.kind == "furniture"


def test_retyping_a_footnote_does_not_move_the_canonical_text():
    """Load-bearing. Every stored claim span is an offset into this string. Recognising a
    footnote must change what we KNOW about a block, never where its characters sit -- a
    reclassification that shifted the text would invalidate the store."""
    c = build(_MD)
    assert "For more information" in c.text
    assert c.text.index("4 For more information") > 0


def test_a_footnote_still_carries_its_number_in_the_text():
    """The numeral stays in the canonical text. It is what the page prints, and removing it
    would move every offset after it for no gain -- the number is on the unit as a field."""
    c = build(_MD)
    u = next(u for u in c.units if "For more information" in u.text)
    assert c.text[u.char_start:u.char_end].startswith("4 For more information")


# --- turning a marker into a correction --------------------------------------------------

from pipeline.footnotes import marker_correction


def test_a_mangled_marker_becomes_a_correction():
    """End to end for the Year 2 case: the primary read 'wet:', the second arm read 'we4:',
    footnote 4 exists on that page, so the correction is to drop the stray character."""
    got = marker_correction("In Year Two, wet:", "In Year Two, we4:", {4})
    assert got == ("In Year Two, wet:", "In Year Two, we:")


def test_a_marker_the_primary_already_dropped_needs_no_correction():
    """Four of six markers vanished from the primary read. The prose is already right, so
    there is nothing to write -- recording the reference is enough."""
    assert marker_correction("In Year Two, we:", "In Year Two, we2:", {2}) is None


def test_no_footnote_means_no_correction():
    assert marker_correction("we saved 9:", "we saved 9:", {1, 2}) is None


def test_a_stray_punctuation_token_does_not_defeat_alignment():
    """The second arm renders bullets as a standalone '.', so its token count exceeds the
    primary's and the strict alignment check bailed out -- leaving both real Year 2 markers
    unresolved. A lone punctuation mark is not a word."""
    got = marker_correction("In Year Two, wet: Launched a",
                            "In Year Two, we4: . Launched a", {4})
    assert got == ("In Year Two, wet: Launched a", "In Year Two, we: Launched a")


def test_dropping_punctuation_tokens_never_drops_real_words():
    """The tokens removed carry no word characters at all, so nothing with content is lost
    and the two sides still have to align one-for-one afterwards."""
    text, refs = strip_reference("a b c", "a . b — c", set())
    assert text == "a b c" and refs == []


def test_a_correction_is_scoped_to_one_block():
    """A correction has to name a string that exists in the MARKDOWN, and the markdown puts
    comments, '>' prefixes and blank lines between blocks. A window spanning a block
    boundary -- 'least 50% In Year Two, wet: Launched a collaboration' -- exists only in the
    canonical text and can never be located in the file. The unit's own text is the largest
    anchor guaranteed to exist in both."""
    from pipeline.footnotes import block_correction
    got = block_correction("In Year Two, wet:", "In Year Two, we4: .", {4})
    assert got == ("In Year Two, wet:", "In Year Two, we:")


def test_a_block_needing_no_change_yields_nothing():
    from pipeline.footnotes import block_correction
    assert block_correction("In Year Two, we:", "In Year Two, we2:", {2}) is None
