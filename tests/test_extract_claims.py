"""The anchoring guard. This is the thing that must never regress.

v1 produced 71 fabricated claims from empty input. `verbatim NOT NULL` is the schema's
answer; anchor() is the pipeline's, and it only works if a verbatim that is not in the
document cannot be stored under any circumstance.
"""
from pipeline.extract_claims import anchor, anchor_all

SECTION = ("STRATEGY THREE: ENERGY EFFICIENCY\n\n"
           "Provided $300,000 in grants to local housing providers.\n\n"
           "Actively promoted the State’s adoption of the 2021 Model Building Code.")


def test_an_exact_verbatim_is_located_with_correct_offsets():
    hit = anchor("Provided $300,000 in grants to local housing providers.", SECTION)
    assert hit
    a, b, how = hit
    assert how == "exact" and SECTION[a:b] == "Provided $300,000 in grants to local housing providers."


def test_a_curly_quote_the_model_straightened_still_anchors():
    """Year 3 is full of typographic apostrophes. A model returning a straight one has
    still copied the sentence."""
    hit = anchor("Actively promoted the State's adoption of the 2021 Model Building Code.",
                 SECTION)
    assert hit
    a, b, how = hit
    assert how == "folded"
    assert SECTION[a:b] == "Actively promoted the State’s adoption of the 2021 Model Building Code."


def test_the_stored_verbatim_is_the_documents_characters_not_the_models():
    r = anchor_all([{"verbatim": "Actively promoted the State's adoption of the 2021 "
                                 "Model Building Code."}], SECTION)
    assert r.anchored[0].verbatim.count("’") == 1     # the curly quote is preserved


def test_collapsed_whitespace_still_anchors():
    hit = anchor("Provided  $300,000   in grants to local housing providers.", SECTION)
    assert hit and hit[2] == "folded"


def test_an_invented_sentence_is_rejected():
    """The failure this project exists to prevent."""
    r = anchor_all([{"verbatim": "Provided $900,000 in grants to local housing providers."}],
                   SECTION)
    assert r.anchored == [] and len(r.rejected) == 1
    assert "not found" in r.rejected[0]["_reason"]


def test_a_paraphrase_is_rejected():
    r = anchor_all([{"verbatim": "The City gave grants to housing providers."}], SECTION)
    assert r.anchored == [] and len(r.rejected) == 1


def test_a_sentence_from_another_document_is_rejected():
    r = anchor_all([{"verbatim": "Planted 10,000 trees in the Bryant neighborhood."}],
                   SECTION)
    assert r.anchored == []


def test_an_empty_verbatim_is_rejected():
    assert anchor("", SECTION) is None
    assert anchor("   ", SECTION) is None


def test_offsets_round_trip_for_every_anchored_claim():
    r = anchor_all([{"verbatim": "Provided $300,000 in grants to local housing providers."},
                    {"verbatim": "STRATEGY THREE: ENERGY EFFICIENCY"}], SECTION)
    assert len(r.anchored) == 2
    for x in r.anchored:
        assert SECTION[x.span_start:x.span_end] == x.verbatim


def test_the_located_rate_is_reported_and_a_zero_reject_rate_is_visible():
    r = anchor_all([{"verbatim": "Provided $300,000 in grants to local housing providers."},
                    {"verbatim": "invented"}], SECTION)
    assert r.located_rate == 0.5


def test_payloads_accept_a_list_and_still_accept_a_bare_object():
    """The contract asks for a list. A model returning the single object it used to return
    is understood rather than dropped."""
    from pipeline.extract_claims import _as_list
    assert _as_list([{"value_low": 17}, {"value_low": 113}]) == [{"value_low": 17},
                                                                 {"value_low": 113}]
    assert _as_list({"value_low": 17}) == [{"value_low": 17}]
    assert _as_list(None, None) == []
    assert _as_list([1, "x", {"value_low": 3}]) == [{"value_low": 3}]


def test_the_prompt_requires_one_claim_per_assertion_and_whole_sentences():
    from pipeline.extract_claims import PROMPT
    assert "ONE CLAIM PER ASSERTION, NOT ONE PER NUMBER" in PROMPT
    assert "never share the same verbatim" in PROMPT
    assert "VERBATIM MUST BE A COMPLETE SENTENCE" in PROMPT


# --- running twice must not silently double a section ------------------------------------

def test_already_extracted_sections_are_refused_by_default():
    """A REAL INCIDENT. Re-running extraction on section 82 as a smoke test stored a second
    copy of all nine claims -- 18 rows over 9 distinct spans. Nothing errored.

    'Claims are never deduplicated' is a schema rule, so these are not merge-able noise:
    each is a permanent row asserting the document said something twice. The guard belongs
    here rather than in a UNIQUE constraint, because two claims CAN legitimately share a
    span (one actor, one moment, two different assertions) -- what must not happen is a
    whole section being re-run by accident."""
    from pipeline.extract_claims import already_extracted
    assert already_extracted(9, [(1, 10, 20)]) is True
    assert already_extracted(0, []) is False


def test_a_section_with_no_heading_still_reports():
    """Front matter has a NULL heading -- it is a section with no '##' above it, which
    canonical.sections() creates deliberately so the title, coverage period and sign-off are
    not dropped. The CLI's summary line did r['heading'][:56] and crashed on None AFTER the
    claims were already committed, leaving a section that looked failed and was not."""
    from pipeline.extract_claims import heading_label
    assert heading_label(None) == "(front matter)"
    assert heading_label("") == "(front matter)"
    assert heading_label("STRATEGY 1: Renewables")[:8] == "STRATEGY"
