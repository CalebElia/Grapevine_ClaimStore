"""Adjudication against the real PDFs. Skips when the source files are not present.

Follows test_schema_runtime.py's precedent: an environment without the corpus skips rather
than fails, because the check is about the code, not about who has the files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.adjudicate import (WINNER_PRIMARY, WINNER_SECOND, find_token_gaps,
                                 split_points, split_token, token_verdict)

SRC = Path("/Users/calebjohnson/Desktop/Grapevine/Coding_Projects/docling-test/source_pdfs")
Y3 = SRC / "AA_AnnualReport_2023.pdf"
Y4 = SRC / "AA_AnnualReport_2024.pdf"

pytestmark = pytest.mark.skipif(not Y3.exists(), reason="source PDFs not present")


def test_actgrant_carries_a_measurable_internal_space():
    """The measured case. 2.16pt between t and g where every other gap is 0.00pt."""
    found = find_token_gaps(Y3, "Actgrant")
    assert found is not None, "the token should be locatable in the text layer"
    gaps, size = found
    assert len(gaps) == len("Actgrant") - 1
    assert split_points(gaps, size) == [3]


def test_year3_actgrant_is_won_by_the_second_read():
    """Class A end to end: the primary is faithful to a text layer that lost the space."""
    gaps, size = find_token_gaps(Y3, "Actgrant")
    parts = split_token("Actgrant", split_points(gaps, size))
    w, why = token_verdict("actgrant", parts, {"act", "grant", "reduction"})
    assert w == WINNER_SECOND, why


def test_year4_successful_is_won_by_the_primary():
    """Class B end to end: geometry shows one word; the second arm ran words together."""
    found = find_token_gaps(Y4, "successful")
    assert found is not None
    gaps, size = found
    parts = split_token("successful", split_points(gaps, size))
    w, why = token_verdict("successful", parts,
                           {"hostedasuccessfulsustainability"})
    assert w == WINNER_PRIMARY, why


def test_a_token_that_is_not_in_the_pdf_returns_none():
    """Year 2's conflicts have no text layer to locate. That must be None, not a guess."""
    assert find_token_gaps(Y3, "zzzznotarealtoken") is None


# --- over a whole document's stored conflicts -------------------------------------------

def _db():
    try:
        import psycopg
        psycopg.connect("host=/tmp port=5433 user=grapevine dbname=grapevine").close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db(), reason="no database")
def test_year3_conflicts_are_decided_by_geometry_except_the_ambiguous_one():
    """Five of six Year 3 conflicts are class A -- the text layer encodes no space where
    the glyphs plainly have one, so the OCR arm read the page correctly.

    The sixth, "Careprogram", is the case that made this layer honest: its internal gap is
    0.88pt at 11pt where a real space in the same document is 2.16-2.34pt. Geometry cannot
    call it, so it escalates rather than rubber-stamping either arm.
    """
    from pipeline.adjudicate import adjudicate_document, WINNER_SECOND, WINNER_UNRESOLVED
    rows = {r["token"]: r for r in adjudicate_document(
        10, Y3, Path("processing/a2zero-year3/cu/year3-cu.md"))}
    # Asserts about the tokens this cares about rather than the size of a live, mutable
    # list: "careprogram" was one of these until the write-back corrected the document, and
    # a count assertion turns every legitimate repair into a test failure. The ambiguous-gap
    # finding it used to carry is pinned data-independently in
    # test_a_gap_between_tight_and_a_space_is_ambiguous.
    for tok, reads in (("actgrant", "act grant"), ("airquality", "air quality"),
                       ("basedorganizations", "based organizations"),
                       ("fundan", "fund an"), ("launcha", "launch a")):
        if tok not in rows:
            continue                      # already corrected and written back
        assert rows[tok]["winner"] == WINNER_SECOND, rows[tok]
        assert rows[tok]["reads_as"] == reads
    assert "careprogram" not in rows or \
        rows["careprogram"]["winner"] == WINNER_UNRESOLVED


def test_year2_has_no_glyph_runs_to_measure():
    """Year 2 is 96% OCR: its text layer holds 234 words across 14 pages, so nothing the
    document plainly says can be located there. Geometry therefore adjudicates nothing in
    that document and must return None rather than a guess.

    Written against the PDF rather than against stored conflicts: the earlier version
    asserted Year 2 HAD conflicts, and every one of them has since been fixed by the
    footnote model, which turned a passing test into a failing one for the best possible
    reason."""
    from pipeline.adjudicate import find_token_gaps
    y2 = SRC / "AA_AnnualReport_2022.pdf"
    # Its 234 extractable words are the section HEADINGS, which are real text; the body
    # prose is images. So the check uses body words, which is where every claim comes from.
    for token in ("Launched", "Installed", "collaboration", "resilience", "AQMesh"):
        assert find_token_gaps(y2, token) is None, token
