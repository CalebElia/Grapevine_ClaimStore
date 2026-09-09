"""Fitting a heading tree to the two levels canonical.py has.

Every fixture is the real shape of the CAP: a plain-paragraph title, part headings at `#`,
strategies at `##`, 44 Actions at `###`, and the eight per-Action fields at `####`.
"""
from __future__ import annotations

from pipeline.canonical import build, sections
from pipeline.reheading import prose_words, reheading

CAP = """<!-- p.1 -->

ANN ARBOR'S LIVING CARBON NEUTRALITY PLAN APRIL 2020

# EXECUTIVE SUMMARY

Some summary prose.

## Strategy 1: Power Our Electrical Grid with 100% Renewable Energy

Strategy body.

### Implement Community Choice Aggregation

Action body.

#### Vision for Implementing Community Choice Aggregation

Vision body.

#### Party Responsible for Implementation

Office of Sustainability and Innovations
"""


def test_the_first_prose_block_becomes_the_one_title():
    out, counts = reheading(CAP)
    assert "# ANN ARBOR'S LIVING CARBON NEUTRALITY PLAN APRIL 2020" in out
    assert counts["title_promoted"] == 1
    assert len([l for l in out.splitlines() if l.startswith("# ")]) == 1


def test_a_part_heading_becomes_a_section():
    """`# EXECUTIVE SUMMARY` makes a `title` unit, which does NOT start a section -- so its
    content was being absorbed into whatever `##` came before it."""
    out, _ = reheading(CAP)
    assert "## EXECUTIVE SUMMARY" in out


def test_an_action_becomes_its_own_section():
    """The point of the whole exercise: a claim about CCA must cite the CCA section, not
    Strategy 1's 1,770 words."""
    out, _ = reheading(CAP)
    assert "## Implement Community Choice Aggregation" in out
    heads = [s["heading"] for s in sections(build(out))]
    assert "Implement Community Choice Aggregation" in heads


def test_the_per_action_fields_become_labels_not_sections():
    out, counts = reheading(CAP)
    assert "**Vision for Implementing Community Choice Aggregation**" in out
    assert "**Party Responsible for Implementation**" in out
    assert counts["to_label"] == 2
    assert "####" not in out


def test_no_hash_marks_survive_into_the_canonical_text():
    """120 units on the real file were headings stored as paragraphs, hash marks included."""
    c = build(reheading(CAP)[0])
    assert "###" not in c.text
    assert not any(u.text.lstrip().startswith("#") for u in c.units)


def test_a_level_two_heading_is_left_alone():
    out, counts = reheading(CAP)
    assert "## Strategy 1: Power Our Electrical Grid with 100% Renewable Energy" in out
    assert counts["unchanged"] == 1


# ── the guard ────────────────────────────────────────────────────────────────────────────

def test_not_one_word_of_prose_changes():
    """The whole justification for using a hand-prepared file is its validated text."""
    assert prose_words(CAP) == prose_words(reheading(CAP)[0])


def test_prose_words_ignores_heading_level_but_not_content():
    assert prose_words("# A B\n") == prose_words("## A B\n") == ["A", "B"]
    assert prose_words("## A B\n") != prose_words("## A C\n")


def test_a_label_with_an_asterisk_still_closes_its_emphasis():
    out, _ = reheading("#### Costs * Savings\n")
    assert out.strip() == r"**Costs \* Savings**"


# ── what must not be touched ─────────────────────────────────────────────────────────────

def test_a_hash_inside_a_figure_description_is_not_a_heading():
    """Vision output is fenced off from the document; rewriting inside it would edit a
    model's reading as though it were the page."""
    md = ("<figure_description>\n"
          "  <summary># 1 in the region</summary>\n"
          "</figure_description>\n")
    assert reheading(md)[0] == md


def test_page_markers_and_tables_pass_through():
    md = ("<!-- p.10 -->\n\n| a | b |\n|---|---|\n| c | d |\n\n> A quoted caption.\n")
    assert reheading(md)[0] == md


def test_a_document_already_at_two_levels_is_only_titled():
    """The five annual reports are already 1 x '#' and N x '##'."""
    md = "# Year Five Annual Report\n\n## Strategy 1\n\nBody.\n"
    out, counts = reheading(md)
    assert counts["to_label"] == 0
    assert counts["title_promoted"] == 0      # a heading came first, so nothing to promote
    assert out.count("# Year Five Annual Report") == 1


def test_a_leading_comment_does_not_become_the_title():
    md = "<!-- wiki frontmatter: uuid=cap-2020 -->\n\nReal title here.\n\n# A part\n"
    out, _ = reheading(md)
    assert out.startswith("<!-- wiki frontmatter: uuid=cap-2020 -->")
    assert "# Real title here." in out


def test_a_list_or_quote_is_never_promoted_to_the_title():
    for lead in ("- an item", "> a caption", "| a | b |"):
        out, counts = reheading(f"{lead}\n\nProse after.\n")
        assert counts["title_promoted"] == 1
        assert out.startswith(lead)
