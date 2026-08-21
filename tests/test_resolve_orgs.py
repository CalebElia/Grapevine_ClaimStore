"""An org is a referent: getting it wrong says a thing about the wrong body."""
from pipeline.resolve_orgs import mentions


def test_a_named_organisation_is_matched():
    assert mentions("In collaboration with Community Action Network (CAN), won $500,000",
                    "Community Action Network")


def test_matching_is_word_bounded():
    """SPARK must not match inside 'sparkling' or 'SPARKS'."""
    assert mentions("Began designing a Challenge with SPARK and local businesses.", "SPARK")
    assert not mentions("The sparkling water initiative", "SPARK")


def test_a_short_alias_must_stand_alone():
    """"CAN" is Community Action Network here and also an ordinary modal verb. Matching it
    as a substring would attach an org to every sentence containing 'can'."""
    assert not mentions("Residents can reduce their energy use.", "CAN")
    assert not mentions("A candidate program was proposed.", "CAN")
    assert mentions("In collaboration with Community Action Network (CAN), won funds.", "CAN")


def test_case_insensitive_for_longer_names():
    assert mentions("collaborated with the ann arbor housing commission to secure aid",
                    "Ann Arbor Housing Commission")


def test_a_hyphenated_neighbour_does_not_match():
    assert not mentions("the SPARK-adjacent program", "SPARK")


def test_an_empty_name_never_matches():
    assert not mentions("anything at all", "") and not mentions("anything", "   ")


# --- the funder registry ----------------------------------------------------------------

def test_every_funder_alias_declares_who_decided_it():
    """No anonymous pairs. Each row asserts two names are the same body, which is a
    judgement, and a judgement with no author cannot be audited or reversed."""
    import json
    from pathlib import Path
    p = Path("registries/ann_arbor/funder_aliases.json")
    for a in json.loads(p.read_text())["aliases"]:
        assert a.get("as_written") and a.get("org") and a.get("by"), a


def test_funder_aliases_are_read_case_insensitively():
    from pipeline.resolve_orgs import read_funder_aliases
    al = read_funder_aliases()
    assert al.get("semcog") == "Southeast Michigan Council of Governments"
    # keys are lowercased on read, so a document shouting the name still resolves
    assert all(k == k.lower() for k in al)


def test_a_missing_registry_file_is_empty_not_an_error(tmp_path):
    """A corpus with no curated funders resolves nothing and emits questions -- which is
    the system working. It must not raise."""
    from pipeline.resolve_orgs import read_funder_aliases
    assert read_funder_aliases(tmp_path / "nope.json") == {}


# --- programs are not organisations and not instruments ---------------------------------

def test_a_program_name_resolves_without_being_duplicated_as_an_alias():
    """The bug this pins: read_funder_programs first read only the `aliases` list, so
    "Energy Efficiency and Conservation Block Grant" -- declared as a programme and never
    as an alias -- resolved to nothing, silently, while the registry plainly contained it."""
    from pipeline.resolve_orgs import read_funder_programs
    pr = read_funder_programs()
    assert pr["energy efficiency and conservation block grant"] == \
        "Energy Efficiency and Conservation Block Grant"
    assert pr["eecbg"] == "Energy Efficiency and Conservation Block Grant"


def test_every_program_declares_who_decided_it():
    import json
    from pathlib import Path
    for p in json.loads(Path("registries/ann_arbor/funder_aliases.json").read_text())["programs"]:
        assert p.get("name") and p.get("by"), p


def test_a_program_may_have_no_administering_org():
    """MI-HOPE's administering agency is not stated anywhere in this corpus. A NULL there is
    a research question, not a row to guess at -- so the registry must permit it."""
    import json
    from pathlib import Path
    progs = json.loads(Path("registries/ann_arbor/funder_aliases.json").read_text())["programs"]
    mihope = next(p for p in progs if p["name"] == "MI-HOPE")
    assert mihope["administered_by"] is None
    assert "not stated" in (mihope.get("note") or "").lower()
