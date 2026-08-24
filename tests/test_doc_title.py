"""The document's title, assembled from its title page rather than one block of it.

The CAP's title page carries three text blocks:

    ANN ARBOR'S                       TextItem
    LIVING CARBON NEUTRALITY PLAN     SectionHeaderItem
    APRIL 2020                        TextItem

Taking the SectionHeaderItem alone -- which is what "find the heading" does -- titles the
document "LIVING CARBON NEUTRALITY PLAN" and drops the possessive that begins it.

WHAT THIS CANNOT RECOVER, and should not pretend to: the "A2Zero:" that opens the title as a
reader sees it is in the LOGO, an image classified at confidence 1.00 and deliberately not
read, because a logo is branding. The hand-prepared standard got it from hand-set metadata,
which is the honest place for it.
"""
from __future__ import annotations

from pipeline.render_blocks import assemble_title


def _b(kind, text, page=1):
    return {"kind": kind, "page_no": page, "text": text}


def test_the_title_joins_the_lines_above_the_date():
    blocks = [_b("PictureItem", ""), _b("TextItem", "ANN ARBOR’S"),
              _b("SectionHeaderItem", "LIVING CARBON NEUTRALITY PLAN"),
              _b("TextItem", "APRIL 2020")]
    assert assemble_title(blocks) == "ANN ARBOR’S LIVING CARBON NEUTRALITY PLAN"


def test_a_date_line_is_not_part_of_the_title():
    blocks = [_b("SectionHeaderItem", "YEAR FOUR ANNUAL REPORT"),
              _b("TextItem", "JULY 1, 2023 – JUNE 3, 2024")]
    assert assemble_title(blocks) == "YEAR FOUR ANNUAL REPORT"


def test_a_single_block_title_is_unchanged():
    assert assemble_title([_b("SectionHeaderItem", "ONE YEAR OF A2ZERO")]) == "ONE YEAR OF A2ZERO"


def test_prose_on_the_title_page_is_not_joined_in():
    """A title page can carry a strapline. A sentence is not part of the title."""
    blocks = [_b("SectionHeaderItem", "A2ZERO"),
              _b("TextItem", "This plan sets out how the community will reach carbon "
                             "neutrality by the year 2030 together.")]
    assert assemble_title(blocks) == "A2ZERO"


def test_only_the_first_page_contributes():
    blocks = [_b("SectionHeaderItem", "TITLE"), _b("TextItem", "SUBTITLE", page=2)]
    assert assemble_title(blocks) == "TITLE"


def test_no_title_page_text_is_safe():
    assert assemble_title([_b("PictureItem", "")]) is None
    assert assemble_title([]) is None


def test_scrambled_logo_text_before_the_title_is_not_joined_to_it():
    """Year 2's first page-1 block is OCR of the city logo -- "City Ann Arbor of", word order
    scrambled -- and the real title follows as a SectionHeaderItem. Joining every title-page
    line would produce "City Ann Arbor of A²ZERO ANNUAL REPORT".

    A title line does not end on a preposition. That is enough to separate it from
    "ANN ARBOR'S", which is a possessive leading into the line beneath it."""
    blocks = [_b("TextItem", "City Ann Arbor of"),
              _b("SectionHeaderItem", "A²ZERO ANNUAL REPORT")]
    assert assemble_title(blocks) == "A²ZERO ANNUAL REPORT"


def test_a_possessive_lead_in_is_joined():
    blocks = [_b("TextItem", "ANN ARBOR’S"), _b("SectionHeaderItem", "LIVING PLAN")]
    assert assemble_title(blocks) == "ANN ARBOR’S LIVING PLAN"
