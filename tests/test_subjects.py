"""Assigning subjects from the reports' own structure.

Every one of the five reports organises itself into the same seven A2ZERO strategies, and
titles them differently each year: "Strategy 1: Power our electrical grid with 100% renewable
energy" in Year 1, "STRATEGY ONE: POWER OUR ELECTRICAL GRID..." in Year 3, "STRATEGY 1: 100%
RENEWABLES" in Year 5. Same subject, three titles.

That regularity is EVIDENCE, not inference: the document says which strategy a section is
about by putting it in that section. So the subject comes from the structure and is decided
by string matching, not by a model.
"""
from __future__ import annotations

from pipeline.subjects import load_section_rules, strategy_number

RULES = load_section_rules("ann_arbor", "annual_report")
SEVEN = {1, 2, 3, 4, 5, 6, 7}


def test_a_numbered_strategy_heading_yields_its_number():
    assert strategy_number("Strategy 1: Power our electrical grid with 100% renewable energy") == 1
    assert strategy_number("STRATEGY 5: CIRCULAR ECONOMY") == 5


def test_a_spelled_out_strategy_heading_yields_its_number():
    """Year 3 spells them out."""
    assert strategy_number("STRATEGY ONE: POWER OUR ELECTRICAL GRID WITH 100% RENEWABLE ENERGY") == 1
    assert strategy_number("STRATEGY SEVEN: OTHER STRATEGIES (NOTABLE ACCOMPLISHMENTS OR EFFORTS)") == 7


def test_a_dash_separator_works_as_well_as_a_colon():
    assert strategy_number("Strategy 7 - Other strategies/accomplishments") == 7


def test_a_year_heading_is_not_a_strategy():
    """THE TRAP. 'YEAR 5 PRIORITIES' contains a 5 and is not Strategy 5. The pattern has to
    anchor on the word STRATEGY, not on a digit anywhere in the heading."""
    assert strategy_number("YEAR 5 PRIORITIES") is None
    assert strategy_number("YEAR FOUR PRIORITIES") is None
    assert strategy_number("A2ZERO Year 3 Priorities") is None
    assert strategy_number("2021 - 2022 Annual Report") is None


def test_cross_cutting_headings_are_not_strategies():
    for h in ("INTRODUCTION", "GREENHOUSE GAS EMISSIONS SUMMARY", "CONTENTS",
              "CLOSING", "Overview", "Next Steps"):
        assert strategy_number(h) is None, h


def test_a_strategy_number_outside_the_seeded_set_is_refused():
    """A2ZERO has seven, and that fact now comes from the seeded subjects rather than from a
    constant in the code -- so another jurisdiction's plan needs no edit here."""
    assert strategy_number("STRATEGY 8: SOMETHING NEW", valid=SEVEN) is None
    assert strategy_number("STRATEGY 0: NOTHING", valid=SEVEN) is None


def test_missing_and_empty_headings_are_safe():
    assert strategy_number(None) is None
    assert strategy_number("") is None


# --- sections that are not strategies ----------------------------------------------------

from pipeline.subjects import cross_cutting_subject


def test_narrative_sections_belong_to_the_plan_itself():
    """Introductions, overviews, priorities and closings speak about A2ZERO as a whole
    rather than about one strategy, so they get the parent subject rather than none. A claim
    with no subject is invisible to every aggregate."""
    for h in ("INTRODUCTION", "Overview", "CLOSING", "Next Steps",
              "YEAR 5 PRIORITIES", "YEAR FOUR PRIORITIES", "A2ZERO Year 3 Priorities"):
        assert cross_cutting_subject(h, RULES) == "a2zero", h


def test_the_emissions_summary_is_its_own_subject():
    """Every report from Year 3 on opens with a community-wide GHG inventory. It is the
    measurement the whole plan is judged against, not one strategy's business."""
    assert cross_cutting_subject("GREENHOUSE GAS EMISSIONS SUMMARY", RULES) == "ghg_emissions"


def test_navigational_sections_get_no_subject():
    """A table of contents is not about anything. Neither is a repeated cover title -- and
    inventing a subject for them would put structural furniture into topic aggregates."""
    for h in ("CONTENTS", "2021 - 2022 Annual Report", "YEAR THREE ANNUAL REPORT", None, ""):
        assert cross_cutting_subject(h, RULES) is None, h


def test_a_strategy_heading_is_not_cross_cutting():
    """The two functions must not both claim the same section."""
    assert cross_cutting_subject("STRATEGY 5: CIRCULAR ECONOMY", RULES) is None


def test_front_matter_belongs_to_the_plan():
    """Everything before the first heading is the report's own framing of A2ZERO. Year 2's
    front matter carries five claims and all five are about the plan itself -- "A2ZERO is
    Ann Arbor's plan for achieving a just transition to community-wide carbon neutrality by
    2030". Other years put the same material under an INTRODUCTION heading.

    Front matter that holds only a title and a sign-off produces no claims, so the rule
    costs nothing where it does not apply."""
    from pipeline.subjects import section_subject_key
    assert section_subject_key(None, True, RULES, SEVEN) == "a2zero"
    assert section_subject_key("CONTENTS", False, RULES, SEVEN) is None


def test_section_subject_key_combines_both_rules():
    from pipeline.subjects import section_subject_key
    assert section_subject_key("STRATEGY 3: ENERGY EFFICIENCY", False, RULES, SEVEN) == "strategy-3"
    assert section_subject_key("INTRODUCTION", False, RULES, SEVEN) == "a2zero"
    assert section_subject_key("GREENHOUSE GAS EMISSIONS SUMMARY", False, RULES, SEVEN) == "ghg_emissions"
    assert section_subject_key("CONTENTS", False, RULES, SEVEN) is None

    # A doc type with no structural route maps NOTHING, which is the normal case.
    assert section_subject_key("STRATEGY 3: ENERGY EFFICIENCY", False, None, SEVEN) is None


# --- the assigned corpus -----------------------------------------------------------------

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
def test_every_claim_has_a_subject():
    import psycopg
    with psycopg.connect(DSN) as c:
        missing, total = c.execute(
            "SELECT count(*) FILTER (WHERE subject_id IS NULL), count(*) FROM claims"
        ).fetchone()
    assert missing == 0, f"{missing} of {total} claims have no subject"


@pytest.mark.skipif(not _db(), reason="no database")
def test_sections_without_a_subject_carry_no_claims():
    """Navigational sections get no subject on purpose. If one ever holds a claim, the claim
    is invisible to every topic aggregate -- so the two facts must stay consistent."""
    import psycopg
    with psycopg.connect(DSN) as c:
        rows = c.execute(
            "SELECT s.id, count(cl.*) FROM document_sections s "
            "LEFT JOIN claims cl ON cl.document_section_id = s.id "
            "WHERE s.subject_id IS NULL GROUP BY s.id HAVING count(cl.*) > 0").fetchall()
    assert rows == [], f"sections with claims but no subject: {rows}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_all_seven_strategies_are_present_in_all_five_reports():
    """The structural regularity this whole mapping rests on. If a report ever stops having
    seven strategy sections, the assignment is silently incomplete rather than wrong."""
    import psycopg
    with psycopg.connect(DSN) as c:
        rows = c.execute(
            "SELECT d.id, count(DISTINCT s.subject_id) FROM documents d "
            "JOIN document_sections s ON s.document_id = d.id "
            "JOIN subjects sub ON sub.id = s.subject_id "
            "WHERE sub.name LIKE 'Strategy %' GROUP BY d.id ORDER BY d.id").fetchall()
    assert len(rows) == 5
    assert all(n == 7 for _, n in rows), rows


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_strategy_subject_hangs_under_a2zero():
    """A subject with no parent is invisible to a roll-up that walks the hierarchy down from
    the plan -- which is how Bryant sat unreachable until it was reparented."""
    import psycopg
    with psycopg.connect(DSN) as c:
        orphans = c.execute(
            "SELECT name FROM subjects WHERE parent_subject_id IS NULL AND name <> 'A2ZERO'"
        ).fetchall()
    assert orphans == [], f"subjects with no parent: {orphans}"
