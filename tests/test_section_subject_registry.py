"""Section-to-subject mapping, per jurisdiction and per document type.

WHY THIS IS NOT ONE RULE. The seven A2ZERO strategies are formal to Ann Arbor's plan, and
the STRATEGY heading language appears only in formal reports. Council minutes, dockets and
news coverage talk about the projects, policies and initiatives that SUPPORT a strategy and
almost never name the strategy itself. So a structural route from heading to subject exists
for annual reports and will simply not exist for most document types -- and a document type
with no entry here is the normal case, not a misconfiguration.

WHY THE NUMBER SEVEN IS NOT IN THE CODE. Seven is true of Ann Arbor and breaks at the first
other jurisdiction. The valid numbers come from the subjects that were actually seeded for
that jurisdiction, so a plan with nine strategies works without an edit.
"""
from __future__ import annotations

from pipeline.subjects import (load_section_rules, section_subject_key,
                               strategy_number)


def test_the_rules_load_for_a_known_jurisdiction_and_doc_type():
    r = load_section_rules("ann_arbor", "annual_report")
    assert r is not None
    assert r["front_matter"] == "a2zero"


def test_an_unmapped_doc_type_returns_nothing_and_that_is_normal():
    """Minutes and dockets have no structural route to a subject. Absence must be quiet."""
    assert load_section_rules("ann_arbor", "council_minutes") is None
    assert load_section_rules("ann_arbor", "news_article") is None


def test_an_unknown_jurisdiction_returns_nothing():
    assert load_section_rules("boulder", "annual_report") is None


# --- the number range is data, not code --------------------------------------------------

def test_strategy_number_accepts_any_number_when_no_range_is_supplied():
    """Pure parsing: what the heading SAYS, before anyone decides which exist."""
    assert strategy_number("STRATEGY 9: SOMETHING", valid=None) == 9


def test_a_number_outside_the_jurisdictions_plan_is_refused():
    """Ann Arbor has seven. Strategy 8 in an Ann Arbor report is a parse error."""
    assert strategy_number("STRATEGY 8: SOMETHING", valid={1, 2, 3, 4, 5, 6, 7}) is None
    assert strategy_number("STRATEGY 7: OTHER", valid={1, 2, 3, 4, 5, 6, 7}) == 7


def test_a_jurisdiction_with_nine_strategies_needs_no_code_change():
    """The thing that used to be MAX_STRATEGY = 7."""
    assert strategy_number("STRATEGY 9: SOMETHING", valid=set(range(1, 10))) == 9


def test_zero_and_negative_are_never_valid():
    assert strategy_number("STRATEGY 0: NOTHING", valid=None) is None


def _db():
    try:
        import psycopg
        psycopg.connect("host=/tmp port=5433 user=grapevine dbname=grapevine").close()
        return True
    except Exception:
        return False


import pytest


@pytest.mark.skipif(not _db(), reason="no database")
def test_the_valid_range_is_read_from_the_seeded_subjects():
    """THE THING THAT USED TO BE `MAX_STRATEGY = 7`. Ann Arbor has seven because seven
    strategy subjects were seeded from its plan, not because a constant says so. Seed nine
    for another jurisdiction and nine become valid, with no code change."""
    import psycopg
    from pipeline.subjects import seeded_series_numbers
    with psycopg.connect("host=/tmp port=5433 user=grapevine dbname=grapevine") as c, \
            c.cursor() as cur:
        got = seeded_series_numbers(cur)
    assert got == {1, 2, 3, 4, 5, 6, 7}


def test_the_registry_documents_why_absence_is_normal():
    """The file has to explain itself to whoever adds the second document type, or they will
    read a missing entry as a bug and invent a mapping that does not exist in the text."""
    import json
    from pathlib import Path
    raw = json.loads(Path("registries/ann_arbor/section_subjects.json").read_text())
    assert "_absence_is_normal" in raw
    assert "_what_is_NOT_here" in raw
    assert raw["doc_types"]["annual_report"]["numbered_series"]["subject_key"] == "strategy-{n}"


def test_no_maximum_is_written_anywhere_in_the_registry():
    """A number in this file would be the same bug one layer out."""
    from pathlib import Path
    text = Path("registries/ann_arbor/section_subjects.json").read_text().lower()
    assert '"max"' not in text and "max_strategy" not in text


# ── the CAP: the document that DEFINES the strategies the reports cite ────────────────────

# Keyed "plan": doc_type is a controlled vocabulary and "plan" is the approved
# term for this document. "cap" would fragment it.
CAP = load_section_rules("ann_arbor", "plan")
VALID = set(range(1, 8))


def test_the_cap_has_a_structural_route_to_its_strategies():
    """The CAP is where the seven strategies are defined; the reports merely cite them."""
    assert CAP is not None
    assert section_subject_key("Strategy 1: Power Our Electrical Grid with 100% Renewable "
                               "Energy", rules=CAP, valid=VALID) == "strategy-1"
    assert section_subject_key("Strategy 6: Enhance the Resilience of Our People and Our "
                               "Place", rules=CAP, valid=VALID) == "strategy-6"


def test_a_numbered_heading_that_is_not_a_strategy_is_not_one():
    """'Appendix 1' and 'ACTION 6' both carry numbers. The rule anchors on the keyword."""
    for h in ("Appendix 1 – List of Public Events", "TOPIC: ENERGY",
              "Appendix 4 - Council Resolution in Support of Creating a Plan"):
        assert section_subject_key(h, rules=CAP, valid=VALID) is None


def test_the_plan_talking_about_itself_maps_to_a2zero():
    for h in ("Executive Summary", "Introduction", "Closing", "A²ZERO Values",
              "A²ZERO Five Pillars of Planning"):
        assert section_subject_key(h, rules=CAP, valid=VALID) == "a2zero"


def test_front_matter_is_the_plan_itself():
    assert section_subject_key(None, is_front_matter=True, rules=CAP, valid=VALID) == "a2zero"


def test_an_action_gets_no_subject_from_the_registry():
    """DELIBERATE. An Action names an initiative that already exists as a subject; which
    initiative is a matching question against `subjects`, not a regex. Writing 44 patterns
    here would freeze that matching into config and hide the ones that fail."""
    for h in ("Implement Community Choice Aggregation", "Electrify City Fleet",
              "Launch Landfill Solar Project", "Update Building Codes"):
        assert section_subject_key(h, rules=CAP, valid=VALID) is None


def test_the_cap_and_annual_report_agree_about_strategy_numbering():
    """Both must resolve the same strategy to the same key, or cross-year aggregates split."""
    ar = load_section_rules("ann_arbor", "annual_report")
    for n in VALID:
        h = f"Strategy {n}: Something"
        assert (section_subject_key(h, rules=CAP, valid=VALID)
                == section_subject_key(h, rules=ar, valid=VALID) == f"strategy-{n}")


def test_no_strategy_number_is_hardcoded_for_the_cap_either():
    """The registry's own _numbers_are_not_in_the_code rule. A ninth strategy must work."""
    assert section_subject_key("Strategy 9: A new one", rules=CAP,
                               valid=set(range(1, 10))) == "strategy-9"
    assert section_subject_key("Strategy 9: A new one", rules=CAP, valid=VALID) is None
