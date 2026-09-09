"""Duplicate-organisation proposals.

Org names are not initiative names. An initiative is a distinctive noun phrase; an org is
[Place] [Function] [Type], where two of three tokens are structural. Reusing the subjects'
fold and threshold produced a queue that was mostly false positives.
"""
from pipeline.dedup_subjects import jaccard, org_identity, org_pairs


def o(i, name, org_type="other", claims=0):
    return {"id": i, "name": name, "org_type": org_type, "claims": claims}


def paired(a, b, threshold=0.6):
    return jaccard(org_identity(a), org_identity(b)) >= threshold


# ── the shared jurisdiction carries no identity ───────────────────────────────────────────

def test_word_order_around_the_place_does_not_matter():
    assert org_identity("Ann Arbor SPARK") == org_identity("SPARK Ann Arbor")


def test_a_place_spelled_two_ways_collapses():
    """`U.S. Department of Energy` and `United States Department of Energy` are one body."""
    assert org_identity("U.S. Department of Energy") == \
           org_identity("United States Department of Energy")


# ── structural words are boilerplate only when they are SHARED ────────────────────────────
#
# An earlier fold stripped department / commission / alliance / agency outright. It reported
# `Ann Arbor Housing Commission` (government) and `Ann Arbor Housing Alliance` (nonprofit) as
# IDENTICAL, and paired `Michigan Public Service Commission` with `Ann Arbor Public Schools`.
# When those words differ, they are the entire difference.

def test_a_commission_is_not_an_alliance():
    assert not paired("Ann Arbor Housing Commission", "Ann Arbor Housing Alliance")


def test_a_commission_is_not_a_school_district():
    assert not paired("Michigan Public Service Commission", "Ann Arbor Public Schools")


def test_two_departments_of_the_same_city_are_not_one_department():
    """Fire and Finance differ only in the function word, which is exactly the identity."""
    assert not paired("Ann Arbor Fire Department", "Ann Arbor Finance Department")


def test_a_federal_agency_is_not_a_city_office():
    assert not paired("Federal Emergency Management Agency", "Office of Emergency Management")


def test_sibling_commissions_are_not_paired():
    for other in ("Ann Arbor Planning Commission", "Ann Arbor Transportation Commission"):
        assert not paired("Ann Arbor Housing Commission", other), other


# ── what should surface ───────────────────────────────────────────────────────────────────

def test_the_real_duplicate_is_found():
    rows = org_pairs([o(19, "Ann Arbor SPARK", "nonprofit"),
                      o(103, "SPARK Ann Arbor", "nonprofit")])
    assert len(rows) == 1 and rows[0]["identical"]


def test_the_org_carrying_claims_is_proposed_as_survivor():
    rows = org_pairs([o(113, "United States Department of Energy", "government"),
                      o(140, "U.S. Department of Energy", "other", claims=4)])
    assert rows[0]["survivor"]["id"] == 140


def test_a_type_disagreement_is_reported_not_disqualifying():
    """Two curators can type one org differently; that is a thing to weigh, not a veto."""
    rows = org_pairs([o(113, "United States Department of Energy", "government"),
                      o(140, "U.S. Department of Energy", "other", claims=4)])
    assert rows[0]["same_type"] is False


def test_an_org_named_only_by_place_and_kind_is_skipped():
    """`City of Ann Arbor` strips to nothing. No name comparison can speak to it, and pairing
    two such orgs would match every one of them to every other."""
    assert org_identity("City of Ann Arbor") == frozenset()
    assert org_pairs([o(1, "City of Ann Arbor"), o(2, "State of Michigan")]) == []


def test_every_proposal_starts_without_a_verdict():
    rows = org_pairs([o(19, "Ann Arbor SPARK"), o(103, "SPARK Ann Arbor")])
    assert all(r["verdict"] is None for r in rows)
