"""Deriving funding_source from the awarding body that was already resolved.

funding_source is a CATEGORY; the body is awarding_org_id and the programme is program_id.
The extractor separates all three correctly -- what was missing was the step between them, so
45 of 75 rows sat on the fallback 'other' while the store knew the money came from the EPA.
"""
import json
from pathlib import Path

import pytest

from pipeline.resolve_orgs import category_for, funder_categories

REG = Path(__file__).parent.parent / "registries" / "ann_arbor" / "funder_categories.json"
RULES = funder_categories()


def test_the_registry_only_targets_approved_funding_sources():
    """A mapping to an unapproved term would land back on the fallback, and the whole
    derivation would be a no-op that looks like a fix."""
    import re
    vocab = (Path(__file__).parent.parent / "schema" / "vocabularies.sql").read_text()
    approved = {t for _, t in re.findall(r"\('(funding_source)','(\w+)','[^']*'\)", vocab)}
    assert approved, "could not parse funding_source terms"
    used = set(RULES["by_org"].values()) | set(RULES["by_org_type"].values())
    assert not used - approved, f"registry targets unapproved terms: {sorted(used - approved)}"


# ── the type rule covers only what org_type genuinely settles ─────────────────────────────

@pytest.mark.parametrize("org_type", ["nonprofit", "foundation", "advocacy_coalition"])
def test_a_charitable_body_gives_philanthropic(org_type):
    assert category_for("Some Trust", org_type, RULES) == "philanthropic"


def test_org_type_government_alone_decides_nothing():
    """The EPA and the State of Michigan are both org_type 'government', and funding_source
    turns entirely on which level. An unnamed government body must fall through."""
    assert category_for("Some Unlisted Agency", "government", RULES) is None


# ── named bodies ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("United States Environmental Protection Agency", "federal_grant"),
    ("U.S. Department of Energy", "federal_grant"),
    ("Federal Highway Administration", "federal_grant"),
    ("USDA Forest Service", "federal_grant"),
    ("MI-HOPE", "state_grant"),
    ("American Forests", "philanthropic"),
])
def test_named_bodies_resolve(name, expected):
    assert category_for(name, "government", RULES) == expected


def test_a_named_body_overrides_its_type():
    """American Forests is typed 'other' in orgs because the wiki had no actor-type for it;
    the name is what places it."""
    assert category_for("American Forests", "other", RULES) == "philanthropic"


# ── refusing is a real answer ─────────────────────────────────────────────────────────────

def test_an_unknown_body_is_left_alone():
    assert category_for("Wayne Enterprises", "other", RULES) is None


def test_no_body_at_all_is_left_alone():
    """26 fiscal rows name no funder. Inferring a category from the amount or the purpose
    would be invention dressed as derivation."""
    assert category_for(None, None, RULES) is None


def test_the_registry_documents_its_judgement_calls():
    reg = json.loads(REG.read_text())
    for key in reg["_judgement_calls"]:
        assert key in reg["by_org"], f"{key} documented but not mapped"
    assert reg["_never_guessed"], "the refusal rule should be written down"
