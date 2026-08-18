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


def test_a_figure_block_is_spliced_after_the_matching_section_only():
    secs = [Section(heading="GREENHOUSE GAS EMISSIONS SUMMARY", anchor="",
                    pdf_start=0, pdf_end=5, located=True),
            Section(heading="CLOSING", anchor="", pdf_start=5, pdf_end=10, located=True)]
    out = render(secs, "aaaabbbbbb", "Doc", figure_blocks={"GREENHOUSE GAS": "<fig/>"})
    ghg_idx = out.index("GREENHOUSE GAS")
    closing_idx = out.index("## CLOSING")
    fig_idx = out.index("<fig/>")
    assert ghg_idx < fig_idx < closing_idx


def test_figure_block_matching_is_case_insensitive():
    sec = Section(heading="closing remarks", anchor="", pdf_start=0, pdf_end=3, located=True)
    out = render([sec], "abc", "Doc", figure_blocks={"CLOSING": "<fig/>"})
    assert "<fig/>" in out


def test_no_figure_blocks_is_the_default_and_does_not_crash():
    sec = Section(heading="A", anchor="", pdf_start=0, pdf_end=1, located=True)
    out = render([sec], "x", "Doc")
    assert "## A" in out
