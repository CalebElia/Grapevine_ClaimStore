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
