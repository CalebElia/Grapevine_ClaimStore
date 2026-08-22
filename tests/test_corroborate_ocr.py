"""A second independent read, and what agreement is allowed to buy."""
from __future__ import annotations

from pipeline.corroborate_ocr import compare, figures, words, _norm
from pipeline.section_audit import machine_verdict
import hashlib

_HDR = ("# R\n<!-- 96% OCR: 9 of 10 text blocks were read by OCR (no usable text layer), "
        "not extracted character-exact. Dominant source: docling_ocr. -->\n\n")


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()


def test_thousands_separators_are_typography_but_digits_are_not():
    assert _norm("$4,000") == _norm("$4000")
    assert _norm("$45,800") != _norm("$45,300")


def test_a_figure_the_second_read_did_not_produce_is_a_conflict():
    prim = _HDR + "## S\n\nWe won $45,800 for the work.\n"
    second = "S We won $45,300 for the work."
    s = compare(prim, second)["sections"][-1]
    assert s["figure_conflicts"] == ["money:$45800"]
    assert not s["corroborated"]


def test_agreement_on_figures_and_words_corroborates():
    prim = _HDR + "## S\n\nWe won $45,800 for the work.\n"
    s = compare(prim, "S We won $45,800 for the work.")["sections"][-1]
    assert s["corroborated"] and not s["figure_conflicts"]


def test_the_pipelines_own_header_is_not_compared():
    """Comparing raw markdown reports the renderer's own '96% OCR' as a figure the second
    engine failed to corroborate — the pipeline manufacturing conflicts about itself.
    canonical.build() excludes it by construction."""
    prim = _HDR + "## S\n\nNothing numeric here.\n"
    s = compare(prim, "S Nothing numeric here.")["sections"][-1]
    assert s["corroborated"], s["figure_conflicts"] + s["word_conflicts"]


def test_prose_agreement_alone_does_not_corroborate_a_bad_figure():
    """The dangerous case is prose that matches while a figure does not — the figures are
    what claims carry."""
    prim = _HDR + "## S\n\nThe grant was $500,000 in total.\n"
    s = compare(prim, "S The grant was $900,000 in total.")["sections"][-1]
    assert not s["corroborated"]


# --- what the audit does with it --------------------------------------------------------

def test_corroboration_answers_has_ocr_blocks():
    body = "text"
    conf, why = machine_verdict(body, _h(body),
                                {"has_ocr_blocks": True,
                                 "ocr_corroborated": "azure_content_understanding"})
    assert conf == "clean" and "second independent read" in why


def test_uncorroborated_ocr_still_demotes():
    body = "text"
    conf, _ = machine_verdict(body, _h(body), {"has_ocr_blocks": True})
    assert conf == "suspect"


def test_conflicts_are_never_answered_by_corroboration():
    """ocr_conflicts records where the two engines DISAGREED. A stray ocr_corroborated flag
    must not cancel it -- that would let the exception erase the finding."""
    body = "text"
    conf, why = machine_verdict(body, _h(body),
                                {"has_ocr_blocks": True, "ocr_corroborated": "x",
                                 "ocr_conflicts": ["215"]})
    assert conf == "suspect" and "ocr_conflicts" in why


def test_placement_inferred_is_not_answered_by_an_ocr_second_read():
    """A second OCR engine says nothing about whether a block was placed correctly."""
    body = "text"
    conf, _ = machine_verdict(body, _h(body),
                              {"placement_inferred": True, "ocr_corroborated": "x"})
    assert conf == "suspect"


def test_the_heading_is_part_of_the_section_body():
    """Not a quirk worth hiding: a canonical section STARTS at its heading, so a second
    read that dropped headings would report every section as conflicting. It is a real
    signal — a converter that loses headings has lost structure — and the first version of
    these fixtures failed for exactly that reason."""
    prim = _HDR + "## Strategy 4\n\nNothing numeric here.\n"
    assert not compare(prim, "Nothing numeric here.")["sections"][-1]["corroborated"]
    assert compare(prim, "Strategy 4 Nothing numeric here.")["sections"][-1]["corroborated"]
