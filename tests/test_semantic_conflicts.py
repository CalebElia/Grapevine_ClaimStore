"""The semantic pass over cross-arm conflicts geometry could not decide.

The contract is semantic_pass.py's existing one, applied to a third case: the model CHOOSES
among readings the arms produced and can never supply text of its own. The escape hatch is
"neither", which SURFACES an oddity rather than correcting it -- because the Year 2 cases
are footnote markers, where no candidate reading is right and the truth is structural.
"""
from __future__ import annotations

from pipeline.semantic_pass import pick_reading

_CASE = {"token": "careprogram", "primary": "Careprogram", "second": "Care program",
         "context": "Launched the Pollinator-Aware Yard Careprogram, a restructuring"}


def test_choosing_the_second_arms_reading_is_accepted():
    out = pick_reading([_CASE], [{"token": "careprogram", "choice": "second",
                                  "why": "Yard Care program reads as a programme name"}])
    assert out[0]["resolved"] == "Care program"
    assert out[0]["choice"] == "second"


def test_choosing_the_primary_reading_is_accepted():
    out = pick_reading([_CASE], [{"token": "careprogram", "choice": "primary", "why": "x"}])
    assert out[0]["resolved"] == "Careprogram"


def test_neither_surfaces_a_structural_oddity_and_resolves_nothing():
    """Year 2's 'wet'/'we2' is 'we' plus a footnote marker. Neither arm is right, and
    'correcting' it to 'we' would erase the discovery that the document has footnotes."""
    case = {"token": "wet", "primary": "wet", "second": "we2",
            "context": "In Year Two, wet:"}
    out = pick_reading([case], [{"token": "wet", "choice": "neither",
                                 "why": "looks like a footnote marker after 'we'"}])
    assert out[0]["choice"] == "neither"
    assert out[0]["resolved"] is None
    assert out[0]["surfaced"] is True


def test_free_text_the_model_invented_is_rejected():
    """THE LOAD-BEARING TEST. A reply supplying its own reading is not a choice between the
    arms -- it is fabrication wearing a choice's clothes, and it must not survive."""
    out = pick_reading([_CASE], [{"token": "careprogram", "choice": "Care Program",
                                  "why": "tidied it up"}])
    assert out == []


def test_a_reply_about_a_token_that_was_not_asked_is_dropped():
    out = pick_reading([_CASE], [{"token": "somethingelse", "choice": "second"}])
    assert out == []


def test_a_reply_missing_a_choice_is_dropped():
    out = pick_reading([_CASE], [{"token": "careprogram"}])
    assert out == []


def test_no_cases_makes_no_call():
    assert pick_reading([], []) == []


def test_every_decision_records_when_it_was_made():
    out = pick_reading([_CASE], [{"token": "careprogram", "choice": "second", "why": "x"}])
    assert out[0]["decided_at"].endswith("Z")


# --- building the cases: both arms' readings, aligned -----------------------------------

from pipeline.semantic_pass import align_window


def test_the_second_arms_counterpart_is_found_by_the_words_before_it():
    """The token alone is not a reading. '215' means nothing; 'March 215, 2022' against
    'March 21st, 2022' is a choice. The preceding words anchor the window."""
    primary = "the City Council's resolution passed on March 215, 2022. Currently working"
    second = "(a GUID leaked here.) passed on March 21st, 2022. Currently"
    got = align_window(primary, "215", second, before=3, after=2)
    assert got is not None
    assert "21st" in got


def test_an_anchor_that_does_not_appear_in_the_second_read_returns_none():
    """Never fabricate a counterpart. No anchor means no case to put to the model."""
    assert align_window("a totally unique phrase here xyz", "xyz",
                        "nothing alike at all", before=3, after=2) is None


def test_the_window_shrinks_its_anchor_until_it_matches():
    """The arms differ near the token -- that is why there is a conflict -- so a long anchor
    often fails where a short one succeeds."""
    primary = "alpha beta gamma delta target tail"
    second = "ZZZ gamma delta target-ish tail"
    got = align_window(primary, "target", second, before=4, after=1)
    assert got is not None and "target-ish" in got


def test_the_token_is_matched_as_a_word_not_a_substring():
    """Real bug, found building the Year 2 cases: the conflict token 'wee' located itself
    inside 'week-long' — an unrelated, correctly-read word several sentences away — and
    would have put a fabricated disagreement to the model. A conflict token is a whole
    word the second arm did not produce."""
    primary = "Launched a Resident Race to Zero, week-long event. In Year Two, wee: Installed"
    got = align_window(primary, "wee", "In Year Two, we3: Installed", before=3, after=1)
    assert got is not None
    assert "we3" in got, got
    assert "week" not in got


# --- preparing a case from the two arms --------------------------------------------------

from pipeline.semantic_pass import build_conflict_case


def test_a_case_carries_both_readings_and_the_sentence():
    primary = "Launched the Pollinator-Aware Yard Careprogram, a restructuring of last year"
    second = "Launched the Pollinator-Aware Yard Care program, a restructuring"
    c = build_conflict_case("careprogram", primary, second)
    assert c["primary"] == "Careprogram"
    assert c["second"] == "Care program"
    assert "Pollinator-Aware" in c["context"]


def test_markdown_links_in_the_second_arm_are_stripped():
    """CU emits markdown links, so the counterpart word arrives as
    '[program,](https://...)'. A URL is not a reading and must not reach the model as one."""
    primary = "Yard Careprogram, a restructuring"
    second = "Yard Care [program,](https://www.a2gov.org/x.aspx) a restructuring"
    c = build_conflict_case("careprogram", primary, second)
    assert "http" not in c["second"]
    assert "program" in c["second"]


def test_a_case_with_no_locatable_counterpart_is_not_built():
    """No counterpart means no choice to offer. Returning a case with one side empty would
    ask the model to pick between a reading and nothing."""
    assert build_conflict_case("xyz", "unique phrase xyz here", "nothing alike") is None
