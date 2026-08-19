"""render() is pure and testable without a live PDF/CU/vision call -- everything that
needs those lives in orchestrate(), a thin wrapper this file does not exercise.
"""
from __future__ import annotations

from pipeline.orchestrate_document import render
from pipeline.section_boundaries import Section


def test_a_located_section_renders_the_pdfplumber_span_verbatim():
    sec = Section(heading="INTRO", anchor="a", pdf_start=0, pdf_end=17, located=True)
    out = render([sec], "Hello verbatim text here.", "Doc")
    assert "Hello verbatim" in out
    assert "UNVERIFIED" not in out


def test_an_unlocated_section_falls_back_to_cu_body_and_is_marked():
    sec = Section(heading="STRATEGY 2", anchor="x", cu_body="CU's own reading.",
                 located=False)
    out = render([sec], "irrelevant pdfplumber text", "Doc")
    assert "UNVERIFIED" in out
    assert "CU's own reading." in out


def test_unverified_count_appears_once_near_the_top_not_per_section():
    secs = [Section(heading="A", anchor="", cu_body="x", located=False),
            Section(heading="B", anchor="", pdf_start=0, pdf_end=1, located=True)]
    out = render(secs, "y", "Doc")
    assert out.count("1 of 2 section(s) UNVERIFIED") == 1


def test_a_figure_is_placed_after_the_section_whose_page_range_contains_its_page():
    secs = [Section(heading="GREENHOUSE GAS EMISSIONS SUMMARY", anchor="",
                    pdf_start=0, pdf_end=5, located=True, page_start=4, page_end=4),
            Section(heading="CLOSING", anchor="", pdf_start=5, pdf_end=10, located=True,
                   page_start=23, page_end=24)]
    out = render(secs, "aaaabbbbbb", "Doc", figures=[{"page_no": 4, "block": "<fig/>"}])
    ghg_idx = out.index("GREENHOUSE GAS")
    closing_idx = out.index("## CLOSING")
    fig_idx = out.index("<fig/>")
    assert ghg_idx < fig_idx < closing_idx


def test_a_figure_whose_page_matches_no_section_is_appended_not_dropped():
    """The fail-closed rule applied to figures: a page range gap must not silently lose
    an already-extracted, already-paid-for vision result.
    """
    sec = Section(heading="A", anchor="", pdf_start=0, pdf_end=1, located=True,
                 page_start=1, page_end=1)
    out = render([sec], "x", "Doc", figures=[{"page_no": 99, "block": "<orphan/>"}])
    assert "<orphan/>" in out
    assert "FIGURES NOT MATCHED" in out


def test_a_figure_on_an_unlocated_sections_page_range_is_not_matched():
    """An unlocated section has no page_start/page_end (never computed for it), so it
    cannot claim a figure -- the figure falls through to the orphan section instead of
    being silently attached to a section whose own text is already unverified.
    """
    sec = Section(heading="UNVERIFIED SEC", anchor="", cu_body="x", located=False)
    out = render([sec], "irrelevant", "Doc", figures=[{"page_no": 4, "block": "<fig/>"}])
    assert "FIGURES NOT MATCHED" in out
    assert "<fig/>" in out


def test_no_figures_is_the_default_and_does_not_crash():
    sec = Section(heading="A", anchor="", pdf_start=0, pdf_end=1, located=True)
    out = render([sec], "x", "Doc")
    assert "## A" in out
    assert "FIGURES NOT MATCHED" not in out
