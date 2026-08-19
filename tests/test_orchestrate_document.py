"""render() is pure and testable without a live PDF/CU/vision call -- everything that
needs those lives in orchestrate(), a thin wrapper this file does not exercise.
"""
from __future__ import annotations

from pipeline.orchestrate_document import insert_page_markers, render
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


# ── page markers: the anchors the human verification pass depends on ───────────────────

def test_every_page_boundary_inside_a_span_gets_its_own_marker():
    text = "page one text" + "page two text" + "page three text"
    page_map = [(1, 0, 13), (2, 13, 26), (3, 26, 41)]
    out = insert_page_markers(text, 0, 41, page_map)
    assert "<!-- p.2 -->" in out
    assert "<!-- p.3 -->" in out


def test_markers_land_at_the_right_offsets_not_shifted_by_earlier_insertions():
    """The trap this pins: splicing front-to-back makes every insertion shift the
    offsets of the ones after it, so marker N lands N*len(marker) characters early.
    Both markers here must sit immediately before their own page's first word.
    """
    text = "aaaa" + "bbbb" + "cccc"
    page_map = [(1, 0, 4), (2, 4, 8), (3, 8, 12)]
    out = insert_page_markers(text, 0, 12, page_map)
    assert "<!-- p.2 -->\nbbbb" in out
    assert "<!-- p.3 -->\ncccc" in out


def test_no_marker_at_the_spans_own_start_because_the_heading_already_says_it():
    text = "aaaabbbb"
    page_map = [(1, 0, 4), (2, 4, 8)]
    out = insert_page_markers(text, 4, 8, page_map)
    assert "<!-- p.2 -->" not in out, "the section starts on page 2; its heading says so"


def test_a_boundary_outside_the_span_is_not_pulled_in():
    text = "aaaabbbbcccc"
    page_map = [(1, 0, 4), (2, 4, 8), (3, 8, 12)]
    out = insert_page_markers(text, 0, 8, page_map)
    assert "<!-- p.2 -->" in out
    assert "<!-- p.3 -->" not in out, "page 3 belongs to the next section, not this one"


def test_the_text_itself_is_unchanged_apart_from_the_inserted_markers():
    """Markers must be additive -- a verbatim span that lost or gained a character while
    being annotated would break the one property this whole file exists to preserve.
    """
    text = "The City secured $5,000,000 for the SEU." + "Second page content here."
    page_map = [(1, 0, 40), (2, 40, 65)]
    out = insert_page_markers(text, 0, 65, page_map)
    assert out.replace("<!-- p.2 -->\n", "") == text


def test_heading_carries_a_page_range_when_the_section_spans_several_pages():
    sec = Section(heading="STRATEGY 7: OTHER", anchor="", pdf_start=0, pdf_end=4,
                 located=True, page_start=19, page_end=23)
    out = render([sec], "text", "Doc", page_map=[(19, 0, 4)])
    assert "pages 19–23" in out


def test_a_single_page_section_says_page_not_pages():
    sec = Section(heading="CLOSING", anchor="", pdf_start=0, pdf_end=4, located=True,
                 page_start=24, page_end=24)
    out = render([sec], "text", "Doc", page_map=[(24, 0, 4)])
    assert "(page 24)" in out
    assert "pages" not in out


def test_omitting_the_page_map_yields_a_body_with_no_markers():
    """The --no-page-markers path: a machine consumer gets the text unadorned."""
    sec = Section(heading="A", anchor="", pdf_start=0, pdf_end=8, located=True)
    out = render([sec], "aaaabbbb", "Doc", page_map=None)
    assert "<!-- p." not in out
