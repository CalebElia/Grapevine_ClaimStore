"""Finding where a claim NAMES an initiative, and proving it with a span.

Every fixture is real annual-report text against the wiki's curated initiative names. The
arc this exists for: the CAP declined "Geothermal districts" in 2020, and the Year 4 and 5
reports describe the Bryant district geothermal system it became -- two facts that could not
meet because the reports' sections are strategies, not initiatives.
"""
from __future__ import annotations

from pipeline.detect_mentions import (MIN_NAME_WORDS, core_phrase,
                                      core_phrase_mentions, literal_mentions,
                                      proximity_candidates, window_mentions)

NAMES = [
    (1, "Community Choice Aggregation", "literal_name"),
    (2, "Green Rental Housing Program", "literal_name"),
    (3, "Landfill Solar Project", "literal_name"),
    (4, "Offsets", "literal_name"),                       # one word: never a mention
    (5, "Solarize Ann Arbor", "literal_name"),
    (6, "Ann Arbor", "literal_name"),                     # a shorter name inside a longer one
    (7, "Commercial Solarize Pilot Program", "literal_name"),
    (8, "CCA", "literal_alias"),                          # one word: never a mention
]


# ── a stored mention is provable by slicing the verbatim ─────────────────────────────────

def test_the_span_slices_back_to_the_matched_text():
    """The whole discipline in one assertion. If the offsets do not yield the text, the row
    is wrong, and this is what a reviewer or a later check can rely on."""
    v = "Drafted community choice aggregation legislation and supported microgrids."
    for m in literal_mentions(v, NAMES):
        assert v[m["span_start"]:m["span_end"]] == m["matched_text"]


def test_a_name_matches_however_the_page_cased_it():
    """The reports write "community choice aggregation legislation"; the wiki has title case.
    Same subject, different text, and matched_text keeps what was printed."""
    v = "Drafted community choice aggregation legislation."
    got = literal_mentions(v, NAMES)
    assert [g["subject_id"] for g in got] == [1]
    assert got[0]["matched_text"] == "community choice aggregation"


def test_every_occurrence_is_recorded_not_only_the_first():
    """A sentence naming a thing twice is naming it twice; the second is also evidence."""
    v = "The Landfill Solar Project began; the Landfill Solar Project continues."
    assert len(literal_mentions(v, NAMES)) == 2


# ── what must never be a mention ─────────────────────────────────────────────────────────

def test_a_one_word_name_is_never_a_mention():
    """The wiki really has an initiative called "Offsets". Matching it as a phrase would tag
    every sentence using the word."""
    assert MIN_NAME_WORDS == 2
    v = "Purchased offsets to cover the remaining emissions."
    assert [m["subject_id"] for m in literal_mentions(v, NAMES)] == []


def test_a_one_word_alias_is_refused_too():
    v = "The CCA program requires state legislation."
    assert [m["subject_id"] for m in literal_mentions(v, NAMES)] == []


def test_a_name_inside_a_longer_name_is_dropped():
    """"Solarize Ann Arbor" contains "Ann Arbor", which is a place and not what is named."""
    v = "Installed solar through the Solarize Ann Arbor program."
    assert [m["subject_id"] for m in literal_mentions(v, NAMES)] == [5]


def test_a_name_inside_a_longer_word_does_not_match():
    assert literal_mentions("Reoffsetsing is not a word.", NAMES) == []


def test_an_empty_verbatim_yields_nothing():
    assert literal_mentions("", NAMES) == []
    assert literal_mentions(None, NAMES) == []


# ── proximity proposes and is never stored ───────────────────────────────────────────────

def test_proximity_finds_what_a_literal_rule_cannot():
    """"pilot commercial Solarize program" really is the Commercial Solarize Pilot Program,
    and no literal match will ever see it."""
    v = "Initiated design of a pilot commercial Solarize program to support businesses."
    assert 7 in [c["subject_id"] for c in proximity_candidates(v, NAMES)]
    assert literal_mentions(v, NAMES) == []


def test_proximity_output_has_no_span_because_it_is_not_evidence():
    """A queued proposal carries a window to look at, never span_start/span_end -- those
    belong to rows that can be proved."""
    v = "Initiated design of a pilot commercial Solarize program."
    for cand in proximity_candidates(v, NAMES):
        assert "span_start" not in cand and "span_end" not in cand
        assert "window" in cand


def test_words_scattered_far_apart_are_not_proposed():
    """Sixty characters is the window; a name's words at opposite ends of a paragraph are
    two topics, not one reference."""
    v = ("Community engagement continued throughout the year with many partners and events, "
         "and separately the team reviewed how best to make a choice about aggregation.")
    assert 1 not in [c["subject_id"] for c in proximity_candidates(v, NAMES)]


# ── the core phrase: located by symbols, decided by meaning ──────────────────────────────

def test_only_the_leading_verb_is_stripped():
    """The wiki names an initiative for the DOING of it and a report writes the thing itself."""
    assert core_phrase("Move Toward a Circular Economy") == "Circular Economy"
    assert core_phrase("Support Aging in Place Efficiently") == "Aging in Place Efficiently"
    assert core_phrase("Offset Greenhouse Gas Emissions") == "Greenhouse Gas Emissions"


def test_interior_function_words_survive():
    """"Aging IN Place" -- removing the interior "in" leaves a phrase the document never
    printed, which no span could then prove."""
    assert "in Place" in core_phrase("Support Aging in Place Efficiently")


def test_a_name_with_no_leading_verb_is_unchanged():
    assert core_phrase("Solarize Ann Arbor") == "Solarize Ann Arbor"
    assert core_phrase("Geothermal Districts") == "Geothermal Districts"


def test_a_core_phrase_proposal_still_carries_a_provable_span():
    """It is a proposal about MEANING, not about location: where it is remains provable."""
    v = "Launched a City circular economy website."
    got = core_phrase_mentions(v, [(9, "Move Toward a Circular Economy", "literal_name")])
    assert len(got) == 1
    assert v[got[0]["span_start"]:got[0]["span_end"]] == got[0]["matched_text"]
    assert got[0]["matched_text"] == "circular economy"


def test_a_name_that_needed_no_stripping_is_left_to_the_literal_pass():
    """Otherwise the same occurrence would be proposed twice, once needing verification."""
    v = "Installed solar through the Solarize Ann Arbor program."
    assert core_phrase_mentions(v, [(5, "Solarize Ann Arbor", "literal_name")]) == []


def test_a_core_phrase_of_one_word_is_refused_like_any_other():
    """"Offset Offsets" would leave a single word, and one word is never a mention."""
    assert core_phrase_mentions("We purchased offsets.",
                                [(4, "Offset Offsets", "literal_name")]) == []


def test_the_run_stores_nothing_from_a_core_phrase_without_a_verifier():
    """verify_fn=None keeps the pass purely symbolic. A located phrase is not yet a fact."""
    import inspect
    from pipeline.detect_mentions import run
    assert inspect.signature(run).parameters["verify_fn"].default is None


# ── order-insensitive: the case no ordered rule can reach ────────────────────────────────

GEO = [(2, "Geothermal Districts", "literal_name")]


def test_a_reversed_name_with_a_plural_difference_matches():
    """The wiki curates "Geothermal Districts"; the reports write "a district geothermal loop
    in the Bryant neighborhood". Reversed order and one trailing s -- so literal and
    core-phrase both miss it, and the 2020 idea never meets what it became."""
    v = "Won a planning grant to design a district geothermal loop in the Bryant neighborhood."
    got = window_mentions(v, GEO)
    assert len(got) == 1
    assert got[0]["matched_text"] == "district geothermal"
    assert v[got[0]["span_start"]:got[0]["span_end"]] == got[0]["matched_text"]


def test_a_missing_identity_word_is_not_a_match():
    """"networked geothermal study" is geothermal work and not the districts programme."""
    assert window_mentions("Initiated a city-wide networked geothermal study.", GEO) == []


def test_words_separated_by_real_content_are_not_a_phrase():
    """A run may hold function words between the terms and nothing else. Otherwise a sentence
    that merely contains both words would read as naming one thing."""
    v = "The districts were mapped and geothermal was discussed at length."
    assert window_mentions(v, GEO) == []


def test_a_permitted_filler_is_a_function_word_only():
    assert window_mentions("Designed the geothermal for districts.", GEO)
    assert window_mentions("Geothermal heating for eleven districts.", GEO) == []


def test_the_stem_folds_plurals_and_nothing_more():
    """A real stemmer would fold "housing" to "hous" and match things sharing a root but not
    a referent -- the failure this module keeps refusing to make."""
    from pipeline.detect_mentions import _stem
    assert _stem("districts") == "district"
    assert _stem("policies") == "policy"
    assert _stem("housing") == "housing"
    assert _stem("gas") == "gas"


# ── the reviewer's tool ───────────────────────────────────────────────────────────────────
#
# WHY THESE EXIST. --drop rewrites the queue file in place. A bug here does not raise; it
# silently deletes candidates nobody ruled on, and the only record that they were ever
# proposed is the file it just overwrote.

def _queue(tmp_path):
    import json
    p = tmp_path / "q.json"
    p.write_text(json.dumps([
        {"claim_id": 1, "verbatim": "a", "candidates": [
            {"subject_id": 10, "name": "Alpha"}, {"subject_id": 11, "name": "Beta"}]},
        {"claim_id": 2, "verbatim": "b", "candidates": [{"subject_id": 10, "name": "Alpha"}]},
    ]))
    return p


def test_drop_one_candidate_keeps_its_siblings(tmp_path):
    import json
    from pipeline import review_mentions as rm
    p = _queue(tmp_path)
    rm.drop(p, 1, 10, quiet=True)
    q = json.loads(p.read_text())
    assert [x["claim_id"] for x in q] == [1, 2]
    assert [c["subject_id"] for c in q[0]["candidates"]] == [11]
    # The refusal is remembered, or the next automated run proposes it again.
    assert any(r["name"] == "Alpha" for r in q[0]["core_phrases_rejected"])


def test_drop_last_candidate_removes_the_claim(tmp_path):
    import json
    from pipeline import review_mentions as rm
    p = _queue(tmp_path)
    rm.drop(p, 2, 10, quiet=True)
    assert [x["claim_id"] for x in json.loads(p.read_text())] == [1]


def test_drop_without_subject_removes_the_whole_claim(tmp_path):
    import json
    from pipeline import review_mentions as rm
    p = _queue(tmp_path)
    rm.drop(p, 1, None, quiet=True)
    assert [x["claim_id"] for x in json.loads(p.read_text())] == [2]


def test_drop_of_an_unqueued_claim_changes_nothing(tmp_path):
    import json
    from pipeline import review_mentions as rm
    p = _queue(tmp_path)
    before = json.loads(p.read_text())
    rm.drop(p, 999, 10, quiet=True)
    assert json.loads(p.read_text()) == before


def test_missing_queue_names_the_directory_you_are_in(tmp_path):
    """The failure the instructions actually hit was a wrong working directory."""
    import pytest
    from pipeline import review_mentions as rm
    with pytest.raises(SystemExit) as e:
        rm.load(tmp_path / "nope.json")
    assert "grapevine-claim-store" in str(e.value)
