"""Duplicate-subject proposals. The detector must surface real duplicates without
asserting that near-misses are the same programme."""
from pipeline.dedup_subjects import identity, jaccard, pairs, root


def s(i, name, mentions=0, claims=0):
    return {"id": i, "name": name, "mentions": mentions, "claims": claims}


# ── the fold ──────────────────────────────────────────────────────────────────────────────

def test_the_fleet_pair_folds_to_one_identity():
    """The case that motivated this: two spellings, one programme."""
    assert identity("Electrify City Fleet") == identity("City Fleet Electrification")


def test_the_matchers_fold_would_have_missed_it():
    """resolve_actions strips lead verbs, which erases the only difference. Documented so
    nobody 'simplifies' this module by reusing that function."""
    from pipeline.resolve_actions import key_terms
    assert key_terms("Electrify City Fleet") != key_terms("City Fleet Electrification")


def test_container_words_do_not_carry_identity():
    assert identity("Community Solar Program") == identity("Community Solar")


def test_root_leaves_short_words_alone():
    """Folding aggressively is safe here, but not at the cost of destroying short words."""
    assert root("gas") == "gas" and root("bus") == "bus" and root("ev") == "ev"


# ── proposals ─────────────────────────────────────────────────────────────────────────────

def test_identical_identities_are_flagged_identical():
    r = pairs([s(110, "Electrify City Fleet", 8, 6), s(75, "City Fleet Electrification")])
    assert len(r) == 1 and r[0]["identical"]


def test_the_subject_carrying_the_evidence_is_proposed_as_survivor():
    r = pairs([s(75, "City Fleet Electrification"), s(110, "Electrify City Fleet", 8, 6)])
    assert r[0]["survivor"]["id"] == 110 and r[0]["duplicate"]["id"] == 75


def test_local_and_regional_transit_are_never_called_identical():
    """Two real programmes differing by one word. The detector may raise them as a question;
    it must never answer it."""
    r = pairs([s(129, "Expand and Improve Local Transit", 0, 14),
               s(130, "Expand and Improve Regional Transit", 0, 16)])
    assert r and not r[0]["identical"]
    assert set(r[0]["differing"]) == {"local", "region"}


def test_unrelated_subjects_are_not_paired():
    assert pairs([s(1, "Community Choice Aggregation"), s(2, "Expand Composting Program")]) == []


def test_every_proposal_starts_without_a_verdict():
    """The module proposes; a person decides. A prefilled verdict would invite a bulk apply."""
    r = pairs([s(110, "Electrify City Fleet", 8, 6), s(75, "City Fleet Electrification")])
    assert all(x["verdict"] is None for x in r)


def test_identical_pairs_sort_before_near_ones():
    r = pairs([s(1, "Electrify City Fleet", 8, 6), s(2, "City Fleet Electrification"),
               s(3, "Community Solar Pilot"), s(4, "Community Solar Program", 1, 14)])
    assert r[0]["identical"]


def test_jaccard_is_symmetric_and_bounded():
    a, b = identity("Community Solar Pilot"), identity("Community Solar Program")
    assert jaccard(a, b) == jaccard(b, a) and 0.0 <= jaccard(a, b) <= 1.0
    assert jaccard(a, a) == 1.0
