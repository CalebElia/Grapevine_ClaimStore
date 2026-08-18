"""Cross-converter disagreement must route attention, never adjudicate.

The single most important property here is what it does NOT do: pick a winner. Model
agreement correlates with correctness at only Spearman 0.20-0.59, and the ASR benchmark
produced the same lesson concretely -- consensus chose the misspelling 'dish' over the
correct 'Disch', and no system at all produced 'Radina'. So these tests pin the reporting
of a divergence, and pin that both directions are reported symmetrically.
"""

from __future__ import annotations

import hashlib

from pipeline.convert_compare import _norm_money, compare, extract
from pipeline.convert_document import Conversion, _assemble
from pipeline.parse_audit import MONEY


def conv(pages, label):
    text, pm = _assemble(pages)
    return Conversion(source_path="r.pdf", converter=label, converter_version="0",
                      text=text, page_map=pm,
                      content_hash=hashlib.sha256(text.encode()).hexdigest(),
                      n_pages=len(pages), converted_at="now")


def test_money_written_two_ways_is_one_value():
    """'$2.5 million' and '$2,500,000' both appear in this corpus, in the same report.

    Treating them as different values would manufacture a divergence that is really a
    formatting choice -- and a worklist full of those is one a reviewer learns to skip.
    """
    assert _norm_money("$2.5 million") == _norm_money("$2,500,000")
    assert _norm_money("$4,500,000") != _norm_money("$450,000")


def test_a_figure_missing_from_one_read_is_reported_with_its_page():
    a = conv(["intro page", "we awarded $75,000 to the McKnight Foundation"], "pdfplumber")
    b = conv(["intro page", "we awarded a grant to the McKnight Foundation"], "reference")
    divs = compare(a, b)
    assert len(divs) == 1
    d = divs[0]
    assert d.kind == "money" and d.present_in == "pdfplumber" and d.page == 2
    assert "McKnight" in d.context


def test_divergence_is_reported_in_both_directions():
    """A converter finding something the baseline missed matters as much as the reverse.

    Measured on Year 5: eight numeric facts (78 MW, 11.88 MW, $3.8 million ...) exist only
    in the reference's chart-derived data_points block and appear nowhere in the raw text.
    """
    a = conv(["only A has $500"], "A")
    b = conv(["only B has 78 MW"], "B")
    kinds = {(d.present_in, d.kind) for d in compare(a, b)}
    assert ("A", "money") in kinds
    assert ("B", "unit_number") in kinds


def test_agreement_produces_an_empty_worklist():
    """Year 4 measured exactly this: zero divergence against its reference."""
    a = conv(["we spent $1,000 and hit 5 MW"], "A")
    b = conv(["Spending: $1,000. Capacity: 5 MW."], "B")
    assert compare(a, b) == []


def test_money_outranks_percent_outranks_units_in_the_worklist():
    """Ranking is the whole value of a worklist -- an unranked one does not get finished."""
    a = conv(["$900 and 42% and 5 MW"], "A")
    b = conv(["nothing numeric here"], "B")
    assert [d.kind for d in compare(a, b)] == ["money", "percent", "unit_number"]


def test_repeated_values_do_not_inflate_the_worklist():
    """A figure repeated five times is one thing to check, not five."""
    a = conv(["$25,000 ... $25,000 ... $25,000"], "A")
    b = conv(["no figures"], "B")
    assert len(compare(a, b)) == 1


def test_extract_normalises_money_but_leaves_other_kinds_alone():
    c = extract("money", MONEY, "$1.5 million and $1,500,000")
    assert list(c.values()) == [2] and len(c) == 1
