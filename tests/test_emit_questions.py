"""Dark matter is a lead queue, never a finding — and never a question the corpus answers."""
from pipeline.emit_questions import is_received, names_a_source


def test_money_received_is_the_only_money_with_a_missing_funder():
    assert is_received("won $500,000 to advance neighborhood decarbonization")
    assert is_received("secured over $1,300,000 for the Bryant project")
    assert is_received("OSI submitted multiple grants and was successful in securing:")


def test_money_saved_or_spent_has_no_funder_to_be_missing():
    """"$45,800 in utility costs saved" is an outcome, not an award."""
    assert not is_received("helping to reduce energy burdens with $45,800 in utility costs saved")
    assert not is_received("Provided $300,000 in grants to local housing providers")
    # "Granted over $70,000 through our ... grant program" is the City giving money AWAY.
    # Money the City hands out has no missing funder: the City is the funder.
    assert not is_received("Granted over $70,000 through our Sustaining Ann Arbor Together")
    assert is_received("was awarded $25,000 by Solar Moonshot")


def test_a_vague_source_names_nobody():
    for empty in (None, "", "other", "unspecified", "unknown", "  OTHER  "):
        assert not names_a_source(empty)


def test_a_real_source_is_a_source():
    for named in ("MI-HOPE", "McKnight Foundation", "federal aid", "State of Michigan"):
        assert names_a_source(named)
