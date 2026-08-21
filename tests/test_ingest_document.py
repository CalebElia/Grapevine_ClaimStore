"""What the dry run must get right before any row exists."""
from pipeline.ingest_document import assign_tier, read_document
from pipeline.canonical import build


def _units(*kinds):
    class U:
        def __init__(self, k): self.kind = k; self.flags = {}
    return [U(k) for k in kinds]


def test_a_masthead_or_closing_is_tier_c():
    """No enumerated content and under a couple of paragraphs: a label, not an assertion."""
    tier, _ = assign_tier("2021 - 2022 Annual Report", _units("heading"))
    assert tier == "C"


def test_a_short_section_WITH_bullets_is_not_tier_c():
    """Year 1's Strategy 4 is 308 characters and says four real things."""
    tier, _ = assign_tier("Reduce the miles we travel." * 4,
                          _units("heading", "list_item", "list_item"))
    assert tier != "C"


def test_a_currency_figure_makes_it_tier_a():
    tier, why = assign_tier("Won $500,000 to advance decarbonization." * 20,
                            _units("heading", "list_item"))
    assert tier == "A" and "currency" in why


def test_a_quantity_dense_section_is_tier_a_without_any_dollar_sign():
    """The GHG summaries carry 17-24 quantities and not one dollar sign, and they are the
    most claim-bearing pages in these reports."""
    # Long enough to clear the tier-C floor: the C rule runs FIRST, and a 560-character
    # fixture is a masthead however many numbers it contains.
    text = " ".join(f"Emissions were {n}% of the 20{n:02d} baseline for this sector."
                    for n in range(20))
    tier, why = assign_tier(text, _units("heading", "para"))
    assert tier == "A" and "quantities" in why


def test_ordinary_prose_with_bullets_is_tier_b():
    tier, _ = assign_tier("We did a number of things this year. " * 30,
                          _units("heading", "list_item"))
    assert tier == "B"


def test_the_period_is_read_as_stated_and_flagged_not_repaired():
    """Years 3 and 4 state 337 and 338 days because both print "June 3" where the page
    means June 30. It is the source's typo; repairing it here would store a date the
    document does not contain."""
    md = ("# t\n\n<!-- gate: PASS -->\n"
          "<!-- COVERAGE PERIOD: July 1, 2022-June 3, 2023 -> 2022-07-01..2023-06-03 "
          "(337 days) -- NOT A YEAR; treat as unconfirmed -->\n\n<!-- p.1 -->\nBody.\n")
    d = read_document(md, build(md))
    assert d.covers_period_end == "2023-06-03"      # as stated
    assert d.period_days == 337 and d.period_flagged


def test_a_document_with_no_stated_period_reports_none_rather_than_guessing():
    md = "# t\n\n<!-- gate: PASS -->\n\n<!-- p.1 -->\nBody.\n"
    d = read_document(md, build(md))
    assert d.covers_period_start is None and not d.period_flagged


def test_the_verdict_and_page_count_come_from_the_document():
    md = "# t\n\n<!-- gate: REVIEW -->\n\n<!-- p.1 -->\na\n\n<!-- p.9 -->\nb\n"
    d = read_document(md, build(md))
    assert d.verdict == "REVIEW" and d.page_count == 9
