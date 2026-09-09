"""Linking a document's named efforts to initiative subjects that already exist.

Every fixture is a real pairing from cap-2020 against the wiki's 229 curated initiatives:
the CAP writes "Implement Community Choice Aggregation" where the wiki has "Community Choice
Aggregation", and its ideas appendix writes "Geothermal districts" where the wiki has
"Geothermal Districts" -- the idea the 2020 plan declined and the venture it became.
"""
from __future__ import annotations

from pipeline.resolve_actions import candidates, key_terms, resolve, slugify

# (id, name, wiki_slug) exactly as the subjects table holds them.
SUBS = [
    (1, "Community Choice Aggregation", "initiatives/community-choice-aggregation"),
    (2, "Geothermal Districts", "initiatives/geothermal-districts"),
    (3, "Landfill Solar Project", "initiatives/landfill-solar-project"),
    (4, "Sustaining Ann Arbor Together Grant Program",
        "initiatives/sustaining-ann-arbor-together-grants"),
    (5, "Expand and Improve Local Transit", "initiatives/local-transit"),
    (6, "Expand and Improve Regional Transit", "initiatives/regional-transit"),
    (7, "Community Solar Program", "initiatives/community-solar-program"),
]


# ── the lead verb the CAP adds and the wiki drops ────────────────────────────────────────

def test_an_action_matches_through_its_lead_verb():
    """The single change that took matching from 3 of 50 to 42 of 56."""
    assert candidates("Implement Community Choice Aggregation", SUBS) == [1]
    assert candidates("Launch Landfill Solar Project", SUBS) == [3]
    assert candidates("Develop Community Solar Program", SUBS) == [7]


def test_a_verb_that_is_part_of_the_real_name_still_matches():
    """Stripped from BOTH sides, so an initiative genuinely named "Expand ..." is reachable."""
    assert candidates("Expand and Improve Local Transit", SUBS) == [5]


def test_an_exact_name_matches_without_help():
    assert candidates("Geothermal districts", SUBS) == [2]
    assert candidates("Geothermal Districts", SUBS) == [2]


# ── name and slug are ALTERNATIVES, not a union ──────────────────────────────────────────

def test_a_plural_in_the_slug_does_not_break_the_name_match():
    """Unioning the name's terms with the slug's ADDED "grants" from
    initiatives/sustaining-ann-arbor-together-grants, a word the heading could never contain,
    and the match was lost. They are two spellings of one thing, not one bigger thing."""
    assert candidates("Promote Sustaining Ann Arbor Together Grant Program", SUBS) == [4]


# ── one candidate or none ────────────────────────────────────────────────────────────────

def test_two_candidates_are_queued_rather_than_guessed():
    """resolve_orgs' rule: choosing between two is not string matching's job."""
    linked, queue = resolve([(99, "Develop light rail transit")], SUBS)
    assert linked == {}
    assert queue and queue[0]["reason"] in ("ambiguous", "no candidate")


def test_nothing_matched_is_a_finding_not_a_new_subject():
    """An effort with no subject means the wiki is missing it or the name changed. Both want
    a human; neither licenses minting a canonical key."""
    linked, queue = resolve([(99, "WELCOME LETTER")], SUBS)
    assert linked == {}
    assert queue[0]["reason"] == "no candidate"
    assert queue[0]["candidates"] == []


def test_a_single_shared_word_cannot_carry_a_match():
    """Containment requires two identity words, or "energy" would link half the corpus."""
    assert candidates("Energy", SUBS) == []
    assert candidates("Community", SUBS) == []


def test_headings_that_are_not_efforts_match_nothing():
    for h in ("Appendix 1 – List of Public Events", "Public Engagement", "Other",
              "The Living Carbon Neutrality Strategy", "Postponed or Delayed Events"):
        assert candidates(h, SUBS) == [], h


# ── the pieces ───────────────────────────────────────────────────────────────────────────

def test_slugify_matches_the_wikis_own_convention():
    assert slugify("Home & Business Electrification") == "home-and-business-electrification"
    assert slugify("A²ZERO Ambassadors") == "a-zero-ambassadors"


def test_key_terms_drops_verbs_function_words_and_bare_numbers():
    assert key_terms("Implement the Community Choice Aggregation") == frozenset(
        {"community", "choice", "aggregation"})
    # A number would make "Strategy 1" and "Strategy 2" differ by a token that is not a name.
    assert key_terms("Strategy 1: Power") == key_terms("Strategy 2: Power")


def test_an_empty_or_missing_heading_matches_nothing():
    assert candidates("", SUBS) == []
    assert candidates(None, SUBS) == []


def test_resolve_reports_every_row_exactly_once():
    rows = [(1, "Implement Community Choice Aggregation"), (2, "WELCOME LETTER"),
            (3, "Geothermal districts")]
    linked, queue = resolve(rows, SUBS)
    assert len(linked) + len(queue) == len(rows)
    assert linked == {1: 1, 3: 2}
