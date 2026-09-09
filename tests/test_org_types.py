"""Translating the wiki's actor-type into the store's org_type vocabulary.

81 of 142 orgs were typed 'other' because resolve_orgs passed the wiki's spelling straight to
a column whose vocabulary uses different words. The trigger did its job -- stored the fallback,
filed a proposal -- and nobody read the proposals, so the drift compounded silently.
"""
import json
from pathlib import Path

import pytest

from pipeline.resolve_orgs import map_org_type, org_type_map

REG = Path(__file__).parent.parent / "registries" / "ann_arbor" / "org_types.json"
MAP = org_type_map()


def test_the_registry_only_targets_approved_terms():
    """A mapping to an unapproved term would land on the fallback exactly as before, and the
    whole exercise would be a no-op that looks like a fix."""
    vocab = (Path(__file__).parent.parent / "schema" / "vocabularies.sql").read_text()
    approved = {t for v, t in
                __import__("re").findall(r"\('(org_type)','(\w+)','[^']*'\)", vocab)}
    assert approved, "could not parse org_type terms"
    unknown = set(MAP.values()) - approved
    assert not unknown, f"registry maps to unapproved org_type terms: {sorted(unknown)}"


def test_every_actor_type_the_wiki_uses_is_mapped():
    """An unmapped value is not passed through -- it is reported and left NULL. This asserts
    there is nothing to report for the corpus as it stands."""
    from pipeline.resolve_orgs import read_actors
    assert [a["name"] for a in read_actors() if a["unmapped_type"]] == []


def test_an_unknown_kind_is_a_finding_not_a_fallback():
    """Returning 'other' here is how eighty rows went wrong quietly."""
    org_type, unmapped = map_org_type("intergalactic-federation", MAP)
    assert org_type is None and unmapped == "intergalactic-federation"


def test_quoting_and_case_from_the_wiki_are_tolerated():
    """The wiki writes both `actor-type: business` and `actor-type: 'business'`."""
    assert map_org_type("'government-agency'", MAP)[0] == "government"
    assert map_org_type("Government-Agency", MAP)[0] == "government"


def test_a_missing_kind_is_neither_a_type_nor_a_finding():
    assert map_org_type(None, MAP) == (None, None)
    assert map_org_type("", MAP) == (None, None)


@pytest.mark.parametrize("wiki_value,expected", [
    ("government-office", "government"),
    ("city-department", "government_dept"),
    ("company", "business"),
    ("university", "academic"),
    ("labor-union", "union"),
    ("religious-organization", "faith_group"),
])
def test_the_spellings_collapse_onto_existing_categories(wiki_value, expected):
    """Sixteen of seventeen proposals were spellings, not categories. Approving them would
    have left the store with two dialects for one idea."""
    assert map_org_type(wiki_value, MAP)[0] == expected


def test_the_registry_documents_its_judgement_calls():
    """Several mappings are debatable. They belong somewhere a person can see and change
    them, not buried in a dict in code."""
    reg = json.loads(REG.read_text())
    for key in reg["_judgement_calls"]:
        assert key in reg["map"], f"{key} documented but not mapped"
    assert reg["_new_term"], "the one genuinely new category should say why it is new"
