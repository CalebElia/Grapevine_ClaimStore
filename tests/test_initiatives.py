"""Reading the wiki's initiative registry into the store.

WHAT COMES FROM THE WIKI AND WHAT DOES NOT. The wiki is a curated secondary source. Its
REFERENTS -- an initiative's name, its slug, which strategies it advances, where it happens,
who is involved -- are the vocabulary of things, and importing them is the same act as
seeding 137 orgs and 7 strategies from it. Its ASSERTIONS -- status, launch year, milestone
targets -- are claims about the world, and those need a verbatim and a document behind them.
Only the referents are read here.
"""
from __future__ import annotations

from pipeline.initiatives import parse_initiative, strategy_codes, ROLE_BY_FIELD


_FM = """---
funding-events: []
launched: 2024
locations:
- '[[locations/ann-arbor]]'
- '[[locations/bryant-neighborhood]]'
parent-strategy: '[[strategies/strategy-1-renewable-grid]]'
partners:
- '[[actors/community-action-network]]'
party-responsible: '[[actors/office-of-sustainability-and-innovations]]'
related-strategies:
- '[[strategies/strategy-1-renewable-grid]]'
- '[[strategies/strategy-3-building-efficiency]]'
status: active
title: 100% Renewable Energy Pathways Study
type: initiative
---
Body text about the study.
"""


def test_the_title_and_slug_become_the_subject():
    got = parse_initiative(_FM, "renewable-pathways-study")
    assert got["title"] == "100% Renewable Energy Pathways Study"
    assert got["wiki_slug"] == "initiatives/renewable-pathways-study"


def test_an_initiative_can_advance_more_than_one_strategy():
    """CALEB'S POINT, AND THE SCHEMA'S. An initiative "pushes forward a Strategy or sometimes
    two". parent-strategy is one; related-strategies may add others, and the union is what
    subject_framework_categories records. A tree could hold only the first."""
    got = parse_initiative(_FM, "x")
    assert strategy_codes(got) == ["strategy-1", "strategy-3"]


def test_the_parent_strategy_is_included_even_when_related_is_empty():
    fm = "---\nparent-strategy: '[[strategies/strategy-5-materials-waste]]'\ntitle: T\n---\n"
    assert strategy_codes(parse_initiative(fm, "x")) == ["strategy-5"]


def test_an_initiative_with_no_strategy_yields_none():
    assert strategy_codes(parse_initiative("---\ntitle: T\n---\n", "x")) == []


def test_places_are_read_as_slugs():
    got = parse_initiative(_FM, "x")
    assert got["places"] == ["ann-arbor", "bryant-neighborhood"]


def test_actors_carry_the_role_their_field_implies():
    """party-responsible is the LEAD; partners are community partners. The field names the
    role, so the role is not guessed."""
    got = parse_initiative(_FM, "x")
    assert got["actors"] == [
        ("office-of-sustainability-and-innovations", "lead"),
        ("community-action-network", "community_partner"),
    ]
    assert ROLE_BY_FIELD["party-responsible"] == "lead"


def test_wiki_assertions_are_not_imported():
    """status and launched are the wiki's JUDGEMENTS as of its last edit, not things this
    store observed. They are read for reference and deliberately not returned as facts."""
    got = parse_initiative(_FM, "x")
    assert "status" not in got and "launched" not in got


def test_a_file_that_is_not_an_initiative_is_refused():
    assert parse_initiative("---\ntype: actor\ntitle: T\n---\n", "x") is None
    assert parse_initiative("---\ntitle: \n---\n", "x") is None


# --- the loaded registry -------------------------------------------------------------------

def _db():
    try:
        import psycopg
        psycopg.connect("host=/tmp port=5433 user=grapevine dbname=grapevine").close()
        return True
    except Exception:
        return False


import pytest
DSN = "host=/tmp port=5433 user=grapevine dbname=grapevine"


@pytest.mark.skipif(not _db(), reason="no database")
def test_an_initiative_may_advance_several_strategies():
    """84 of 229 do -- 37%. Every one of those links is something parent_subject_id could
    not have held, which is why the schema put strategies in a framework."""
    import psycopg
    with psycopg.connect(DSN) as c:
        n = c.execute(
            "SELECT count(*) FROM (SELECT subject_id FROM subject_framework_categories "
            "GROUP BY subject_id HAVING count(*) > 1) t").fetchone()[0]
    assert n >= 80


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_subject_declares_what_kind_of_thing_it_is():
    import psycopg
    with psycopg.connect(DSN) as c:
        unset = c.execute("SELECT count(*) FROM subjects WHERE subject_kind IS NULL").fetchone()[0]
    assert unset == 0


@pytest.mark.skipif(not _db(), reason="no database")
def test_a_place_is_never_its_own_location():
    """The CHECK on subject_places. A self-link would make any recursive place query loop."""
    import psycopg
    with psycopg.connect(DSN) as c:
        n = c.execute("SELECT count(*) FROM subject_places "
                      "WHERE subject_id = place_subject_id").fetchone()[0]
    assert n == 0


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_coalition_member_names_exactly_one_actor():
    """coalition_members could not accept ANY row before migration 021: its primary key
    forced all three actor columns NOT NULL while its CHECK demanded exactly one non-null."""
    import psycopg
    with psycopg.connect(DSN) as c:
        bad = c.execute(
            "SELECT count(*) FROM coalition_members "
            "WHERE num_nonnulls(person_id, org_id, body_id) <> 1").fetchone()[0]
        total = c.execute("SELECT count(*) FROM coalition_members").fetchone()[0]
    assert bad == 0 and total > 400


@pytest.mark.skipif(not _db(), reason="no database")
def test_roles_come_from_the_controlled_vocabulary():
    import psycopg
    with psycopg.connect(DSN) as c:
        roles = {r for (r,) in c.execute("SELECT DISTINCT role FROM coalition_members")}
        approved = {t for (t,) in c.execute(
            "SELECT term FROM vocabulary_terms WHERE vocabulary='involvement_role'")}
    assert roles <= approved, roles - approved


@pytest.mark.skipif(not _db(), reason="no database")
def test_the_annual_report_claims_still_have_their_subjects():
    """The initiative layer must not disturb what already worked."""
    import psycopg
    with psycopg.connect(DSN) as c:
        missing = c.execute("SELECT count(*) FROM claims WHERE subject_id IS NULL").fetchone()[0]
    assert missing == 0
