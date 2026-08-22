"""Which way the money moved. Without it, SUM(amount_low) is meaningless.

Measured on the corpus as extracted: the largest fiscal_reference is $1,000,000,000, and it
is not an award -- "the City has saved rate payers more than $1,000,000,000 through testimony
and advocacy". Summed alongside grants received, it turns a $110M funding picture into a
$1.1B one. Awards, savings, disbursements and authorisations are different facts about
different money and must never add together.

THE CLASSIFIER ABSTAINS. Every phrase it does not recognise is `unknown`, not `received`.
A default that guesses the commonest case would silently re-create the bug in a new place:
a mis-signed row still sums, still looks healthy, and is wrong.
"""
from __future__ import annotations

from pipeline.fiscal_direction import classify


def test_savings_are_not_income():
    """The $1B case that motivated the field."""
    assert classify("the City has saved rate payers more than $1,000,000,000 through "
                    "testimony and advocacy") == "saved"
    assert classify("This represents over $1.5 million in upfront cost savings") == "saved"
    assert classify("$45,800 in utility costs saved for residents") == "saved"


def test_money_the_city_won_is_received():
    assert classify("won $500,000 to advance neighborhood decarbonization") == "received"
    assert classify("secured over $1,300,000 for Bryant") == "received"
    assert classify("was awarded $580,000 by the Department of Energy") == "received"


def test_money_the_city_hands_out_is_disbursed():
    """'Granted' and 'provided' run the OTHER way in this corpus -- the City giving money
    away -- which is the distinction emit_questions already relies on."""
    assert classify("Granted over $70,000 through our Sustaining Ann Arbor Together "
                    "grant program") == "disbursed"
    assert classify("Provided $300,000 in grants to local housing providers") == "disbursed"


def test_a_passive_award_to_the_city_is_still_received():
    """'was granted' is received; a bare 'Granted' at the head of a sentence is the City
    awarding. The auxiliary is what separates them."""
    assert classify("was granted $50,000 by the state") == "received"


def test_authorised_money_has_not_moved_yet():
    assert classify("Authorized over $4,000,000 in funds to be used for rebates") \
        == "authorized"


def test_an_unrecognised_phrase_abstains():
    """Never default to the commonest case. A mis-signed row still sums and still looks
    healthy."""
    assert classify("The program involved $2,000,000") == "unknown"
    assert classify("") == "unknown"
    assert classify(None) == "unknown"


def test_savings_win_over_an_incidental_award_verb():
    """'won ... in savings' is a savings claim. The thing the money DID governs, not the
    first verb in the sentence."""
    assert classify("won recognition for $2 million in energy savings") == "saved"


def test_the_present_participle_of_saving_counts():
    """Found in the corpus: 'saving residents over $X' classified as unknown because the
    pattern only had 'saved' and 'savings'."""
    assert classify("Installed solar on 200 roofs, saving residents over $200,000") == "saved"


def test_the_infinitive_of_secure_counts():
    """'to secure $3 million in federal aid' -- the pattern had only 'secured'."""
    assert classify("Collaborated with the Housing Commission to secure $3 million "
                    "in federal aid") == "received"


def test_money_from_a_named_source_is_received():
    """'$4,500,000 from the American Rescue Plan Act' has no verb at all. 'from' is the
    preposition of receipt."""
    assert classify("$4,500,000 from the American Rescue Plan Act to support solar") \
        == "received"


def test_from_does_not_override_an_explicit_savings_claim():
    assert classify("$50,000 in savings from the retrofit program") == "saved"


# --- an amount that was never stated is not zero ------------------------------------------

from pipeline.fiscal_direction import normalise_amount


def test_a_stated_zero_survives():
    assert normalise_amount(0.0, "the program cost $0 to residents") == 0.0


def test_zero_with_no_figure_in_the_text_becomes_null():
    """Two references in the corpus carry amount_low = 0.00 for sentences that state no
    amount: 'Won a planning grant from the U.S. Department of Energy'. Zero is a real
    amount; 'not stated' is not zero, and storing it as zero makes an unfunded-looking
    award that sums cleanly."""
    assert normalise_amount(0.0, "Won a planning grant from the U.S. Department of "
                                 "Energy to design a district geothermal") is None


def test_a_real_amount_is_untouched():
    assert normalise_amount(500000.0, "won $500,000") == 500000.0
    assert normalise_amount(None, "no amount here") is None
