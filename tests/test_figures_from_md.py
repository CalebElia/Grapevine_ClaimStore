"""Inline figure readings from a hand-prepared markdown.

cap-2020 carries 88 <figure_description> blocks over 49 pages with 410 data bullets, against
figures.json's 66 blocks over 38 pages with 61 <point/> tags. The inline set is both richer
and the one the ingested prose belongs to.
"""
from __future__ import annotations

from pipeline.figures_from_md import PROMPT_VERSION, SOURCE, parse_inline_figures

MD = """<!-- p.7 -->

Some prose about the plan.

<figure_description>
  <type>Infographic</type>
  <title>Project Cost and Greenhouse Gas Reduction Potential</title>
  <summary>Estimated ten-year cost and reduction potential.</summary>
  <data_points>
    - The estimated total cost over 10 years is $605,000.
    - This cost includes $540,000 for staffing.
  </data_points>
</figure_description>

<!-- p.9 -->

<figure_description>
  <type>Chart</type>
  <title>Emissions by Sector</title>
  <data_points>
    - Residential accounts for 23 percent.
  </data_points>
</figure_description>
"""

FIGS = parse_inline_figures(MD, "2026-07-09T00:00:00Z")


def test_one_record_per_block():
    assert len(FIGS) == 2


def test_the_page_comes_from_the_running_marker():
    """The same rule canonical.py applies to text, so a figure and its prose agree."""
    assert [f["page_no"] for f in FIGS] == [7, 9]


def test_the_whole_block_is_preserved_as_raw_xml():
    """document_figures.raw_xml is where the 410 bullets survive until the semantic pass."""
    assert "$605,000" in FIGS[0]["xml"] and "$540,000" in FIGS[0]["xml"]
    assert FIGS[0]["xml"].startswith("<figure_description>")
    assert FIGS[0]["xml"].rstrip().endswith("</figure_description>")


def test_the_wiki_type_is_recorded_not_a_classifier_label():
    """There was no classifier. Calling <type> a classifier_label would invent provenance."""
    assert [f["top_label"] for f in FIGS] == ["Infographic", "Chart"]
    assert all(f["top_conf"] is None for f in FIGS)


def test_geometry_is_left_empty_rather_than_guessed():
    """pictures.json holds 196 pictures over 82 pages against 88 figures over 49; only four
    pages match one-to-one, so a bbox would be a guess about which picture is meant."""
    assert all(f["bbox"] == [] for f in FIGS)
    assert all(f["crop_path"] is None and f["crop_dpi"] is None for f in FIGS)


def test_provenance_names_what_is_actually_known():
    assert all(f["deployment"] == SOURCE for f in FIGS)
    assert all(f["prompt_version"] == PROMPT_VERSION for f in FIGS)
    assert all(f["extracted_at"] == "2026-07-09T00:00:00Z" for f in FIGS)


def test_a_figure_before_any_marker_gets_page_zero_not_one():
    """page_no is NOT NULL, and 0 is visibly not a page; 1 would be a guess."""
    figs = parse_inline_figures("<figure_description>\n<type>X</type>\n"
                                "</figure_description>\n", "2026-01-01T00:00:00Z")
    assert figs[0]["page_no"] == 0


def test_a_page_marker_inside_a_block_does_not_move_the_page():
    """A marker can fall inside a long figure block; it belongs to the prose after it."""
    md = ("<!-- p.3 -->\n\n<figure_description>\n<type>Chart</type>\n"
          "<!-- p.4 -->\n</figure_description>\n\nProse.\n")
    figs = parse_inline_figures(md, "2026-01-01T00:00:00Z")
    assert figs[0]["page_no"] == 3


def test_no_figures_in_a_document_without_any():
    assert parse_inline_figures("<!-- p.1 -->\n\nJust prose.\n", "2026-01-01T00:00:00Z") == []


def test_the_title_is_carried_for_review_convenience():
    assert FIGS[0]["title"] == "Project Cost and Greenhouse Gas Reduction Potential"
