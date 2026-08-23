"""Splitting a free-text unit into a comparable dimension and the thing being counted.

THE PROBLEM, MEASURED. 160 of 218 pending vocabulary proposals were quantity_unit values,
because the extraction put the COUNTED NOUN in `unit`: "Direct Current Fast Chargers
(DCFCs)", "pilot battery back-up Solarize bulk buys", "community organization, collaborators,
and partners". Real units appear when the text offers one -- percent (30), MW (21), years
(20) -- so the field degraded rather than failed.

THE DETAIL IS NOT NOISE AND MUST NOT BE DISCARDED. "18 air quality monitors" is a more useful
fact than "18 things". But `unit='air quality monitors'` will never aggregate with
`unit='AQMesh monitors'`, and a vocabulary with 160 pending members is not a vocabulary.

So the two facts are separated: `unit` carries the DIMENSION, which is comparable and
controlled, and `unit_basis` carries WHAT WAS COUNTED, verbatim and uncontrolled. That column
already existed for this -- "what a % or count is OF" -- and was populated zero times.
"""
from __future__ import annotations

from pipeline.units import split_unit


def test_a_real_measurement_unit_is_kept_and_has_no_basis():
    assert split_unit("MW") == ("MW", None)
    assert split_unit("percent") == ("percent", None)
    assert split_unit("%") == ("percent", None)


def test_synonyms_collapse_so_totals_can_be_taken():
    """percent and % were 30 and 28 separate rows of the same thing."""
    assert split_unit("%")[0] == split_unit("percent")[0]
    assert split_unit("year")[0] == split_unit("years")[0]
    assert split_unit("day")[0] == split_unit("days")[0]


def test_a_counted_noun_becomes_a_count_with_the_noun_preserved():
    assert split_unit("trees") == ("count", "trees")
    assert split_unit("air quality monitors") == ("count", "air quality monitors")
    assert split_unit("Direct Current Fast Chargers (DCFCs)") == \
        ("count", "Direct Current Fast Chargers (DCFCs)")


def test_the_longest_phrases_survive_intact():
    """These are the ones that made the proposal queue unusable. They are still the most
    informative thing about the row and are kept exactly as written."""
    phrase = "community organization, collaborators, and partners"
    assert split_unit(phrase) == ("count", phrase)


def test_carbon_units_normalise_to_one_dimension():
    for raw in ("metric tons of CO2 equivalent (CO2e)",
                "metric tons of CO2 equivalent pollution",
                "metric tons of carbon dioxide equivalent",
                "metrics tons of CO2 equivalent (CO2e)"):
        assert split_unit(raw) == ("metric_tons_co2e", None), raw


def test_bare_metric_tons_stays_generic():
    """'tons of material' diverted from landfill is not CO2e. The measure disambiguates;
    the unit must not assert carbon that the text did not."""
    assert split_unit("metric tons") == ("metric_tons", None)
    assert split_unit("tons of material") == ("metric_tons", "material")


def test_an_empty_unit_is_other_not_a_guess():
    """`unit` is NOT NULL, so something must be stored. `other` is visible; `count` would be
    a silent assertion that the row counts things."""
    assert split_unit(None) == ("other", None)
    assert split_unit("") == ("other", None)
    assert split_unit("   ") == ("other", None)


# --- the extraction path defends itself ---------------------------------------------------

from pipeline.extract_claims import _unit_pair


def test_a_model_answering_with_a_noun_is_still_split():
    """The prompt now asks for a dimension, but the split still runs over whatever arrives.
    A model that regresses must not be able to reopen the free-text problem."""
    assert _unit_pair({"unit": "air quality monitors"}) == ("count", "air quality monitors")


def test_a_model_answering_correctly_passes_through():
    assert _unit_pair({"unit": "count", "unit_basis": "residential roofs"}) == \
        ("count", "residential roofs")
    assert _unit_pair({"unit": "MW"}) == ("MW", None)


def test_an_explicit_basis_wins_over_a_derived_one():
    """If the model gives both, believe it: it read the sentence and the splitter only read
    the unit string."""
    assert _unit_pair({"unit": "trees", "unit_basis": "street trees planted"}) == \
        ("count", "street trees planted")


def test_a_missing_unit_does_not_crash_the_insert():
    """`unit` is NOT NULL in the schema, so the splitter must always yield something."""
    assert _unit_pair({})[0] == "other"
