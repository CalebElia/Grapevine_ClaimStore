"""A hand-prepared wiki markdown, put into the coordinate space ingest reads.

Every fixture is the real shape of a2zero-wiki/prepared/cap/cap-2020.md, whose text is the
quality standard this pipeline is measured against and whose page markers are written inline
where canonical.py cannot see them.
"""
from __future__ import annotations

from pipeline.prepare_wiki_md import (convert, fix_spaced_superscript,
                                      lift_heading_markers, split_frontmatter)


# ── frontmatter ──────────────────────────────────────────────────────────────────────────

FM = '''---
uuid: cap-2020
title: "Ann Arbor A2Zero Living Carbon Neutrality Plan"
covers-period-start: "2020-04"
ingest_date: ""
---

Body text here. [Source: Page 1]
'''


def test_frontmatter_is_read_and_removed_from_the_body():
    meta, body = split_frontmatter(FM)
    assert meta["uuid"] == "cap-2020"
    assert meta["covers-period-start"] == "2020-04"
    assert body.strip().startswith("Body text here.")


def test_an_empty_frontmatter_value_is_skipped_not_stored_blank():
    """`ingest_date: ""` means unknown, and storing "" would look like an answer."""
    meta, _ = split_frontmatter(FM)
    assert "ingest_date" not in meta


def test_a_file_with_no_frontmatter_is_returned_unchanged():
    assert split_frontmatter("Just text.\n") == ({}, "Just text.\n")


def test_the_frontmatter_survives_as_a_comment_not_as_prose():
    """It carries covers-period-start, which the wiki once lost by treating it as decoration.

    As a comment it reaches ingest and cannot reach a claim's verbatim, because canonical.py
    drops comments from the canonical text.
    """
    out, _ = convert(FM)
    assert out.startswith("<!-- wiki frontmatter:")
    assert "uuid=cap-2020" in out
    assert "covers-period-start=2020-04" in out


# ── the markers ──────────────────────────────────────────────────────────────────────────

def test_an_inline_marker_becomes_a_page_comment_and_leaves_the_text():
    """1,351 of these were landing inside claim verbatims."""
    out, recs = convert("The City begins working. [Source: Page 22]\n")
    assert "<!-- p.22 -->" in out
    assert "[Source:" not in out
    assert recs[0]["text"] == "The City begins working."
    assert recs[0]["page_source"] == "explicit"


def test_an_untagged_block_carries_the_last_page_forward():
    """45% of blocks have no marker. The markers are ordered -- 1 inversion in 1,355 -- so
    the last page named before a block is the page it sits on."""
    out, recs = convert("First. [Source: Page 5]\n\nSecond, untagged.\n\nThird. [Source: Page 6]\n")
    assert [r["page_no"] for r in recs] == [5, 5, 6]
    assert [r["page_source"] for r in recs] == ["explicit", "inferred", "explicit"]


def test_every_block_ends_up_with_a_page():
    """canonical.py assigns a unit its page from the marker before it; a gap means no page."""
    _, recs = convert("A. [Source: Page 3]\n\nB.\n\nC.\n\nD.\n")
    assert all(r["page_no"] is not None for r in recs)


def test_a_block_before_the_first_marker_gets_no_page_rather_than_a_guess():
    """There is nothing to carry forward FROM, and page 1 is an assumption, not a reading."""
    _, recs = convert("Front matter with no marker.\n\nBody. [Source: Page 4]\n")
    assert recs[0]["page_no"] is None
    assert recs[1]["page_no"] == 4


def test_a_block_spanning_a_page_break_takes_the_earlier_page():
    """Two markers in one block means its text crosses a break; its opening words are on the
    first page, and a citation should point where the sentence starts."""
    _, recs = convert("A sentence that runs on [Source: Page 7] and finishes. [Source: Page 8]\n")
    assert recs[0]["page_no"] == 7
    assert recs[0]["n_markers"] == 2


def test_a_block_that_is_only_a_marker_is_dropped():
    """It carries no words, and an empty unit would still claim a position in the document."""
    _, recs = convert("Real text. [Source: Page 2]\n\n[Source: Page 3]\n\nMore text.\n")
    assert [r["text"] for r in recs] == ["Real text.", "More text."]


# ── what must not change ─────────────────────────────────────────────────────────────────

def test_not_one_word_of_the_document_is_altered():
    """The whole justification for using this file is that a human validated its text."""
    src = ("Community Choice Aggregation (CCA) program is an agreement among stakeholders "
           "to allow local governments to procure power. [Source: Page 22]\n")
    _, recs = convert(src)
    assert recs[0]["text"] == (
        "Community Choice Aggregation (CCA) program is an agreement among stakeholders "
        "to allow local governments to procure power.")


def test_markdown_structure_is_left_alone():
    """Headings, lists and tables pass through untouched; only the markers move."""
    src = ("## Strategy 1 [Source: Page 21]\n\n"
           "- State legislature [Source: Page 22]\n\n"
           "| a | b |\n|---|---|\n| c | d | [Source: Page 10]\n")
    out, recs = convert(src)
    assert "## Strategy 1" in out
    assert "- State legislature" in out
    assert "| c | d |" in out
    assert [r["page_no"] for r in recs] == [21, 22, 10]


def test_blank_blocks_do_not_produce_units():
    _, recs = convert("A. [Source: Page 1]\n\n\n\n   \n\nB.\n")
    assert len(recs) == 2


# ── a marker marks a CHANGE, and marks what follows it ───────────────────────────────────

def test_a_marker_is_emitted_only_where_the_page_changes():
    """canonical.py keeps a running page, so a marker per block is pure redundancy -- and on
    cap-2020 it produced 1,190 markers where 100 carry the same information, doubling the
    file a human has to hand-correct."""
    out, _ = convert("A. [Source: Page 5]\n\nB.\n\nC.\n\nD. [Source: Page 6]\n")
    assert out.count("<!-- p.5 -->") == 1
    assert out.count("<!-- p.6 -->") == 1


def test_the_marker_precedes_the_text_it_applies_to():
    """The tag names the page of what comes AFTER it, which is what a reviewer editing one
    needs to know."""
    out, _ = convert("Alpha. [Source: Page 7]\n\nBravo. [Source: Page 8]\n")
    assert out.index("<!-- p.7 -->") < out.index("Alpha.") < out.index("<!-- p.8 -->")
    assert out.index("<!-- p.8 -->") < out.index("Bravo.")


def test_a_page_that_recurs_after_another_is_marked_again():
    """Suppression is against the LAST emitted page, not every page seen -- a document that
    returns to an earlier page must say so."""
    out, _ = convert("A. [Source: Page 5]\n\nB. [Source: Page 6]\n\nC. [Source: Page 5]\n")
    assert out.count("<!-- p.5 -->") == 2


def test_every_block_still_records_its_own_page():
    """The sidecar is per block even though the markdown is not, so a reviewer can still see
    which citations were asserted and which were carried forward."""
    _, recs = convert("A. [Source: Page 5]\n\nB.\n\nC. [Source: Page 6]\n")
    assert [r["page_no"] for r in recs] == [5, 5, 6]
    assert [r["page_source"] for r in recs] == ["explicit", "inferred", "explicit"]


# ── a heading belongs to the page its content starts on ──────────────────────────────────

def test_a_marker_following_a_heading_is_lifted_above_it():
    """Real cap-2020 shape: the source puts [Source: Page 22] on the BODY block, so the
    heading inherited p.21 while its own text got p.22. Both are printed on page 22."""
    md = ("<!-- p.21 -->\n\nEnd of the previous action.\n\n"
          "## Implement Community Choice Aggregation\n\n"
          "<!-- p.22 -->\n\nCommunity Choice Aggregation (CCA) program is an agreement.\n")
    out, moved = lift_heading_markers(md)
    assert moved == 1
    assert out.index("<!-- p.22 -->") < out.index("## Implement Community Choice")
    assert out.index("## Implement Community Choice") < out.index("Community Choice Aggregation (CCA)")


def test_lifting_moves_no_words():
    """A marker is a comment; canonical.py drops it from the canonical text entirely."""
    md = ("## A heading\n\n<!-- p.7 -->\n\nBody text here.\n")
    assert lift_heading_markers(md)[0].split() != md.split()      # order changed
    assert sorted(lift_heading_markers(md)[0].split()) == sorted(md.split())


def test_the_heading_and_its_body_end_up_on_the_same_page():
    from pipeline.canonical import build
    md = ("<!-- p.21 -->\n\nPrevious.\n\n## The Action\n\n<!-- p.22 -->\n\nIts body.\n")
    before = {u.text: u.page_no for u in build(md).units}
    after = {u.text: u.page_no for u in build(lift_heading_markers(md)[0]).units}
    assert before["The Action"] == 21 and before["Its body."] == 22
    assert after["The Action"] == 22 and after["Its body."] == 22


def test_a_heading_with_no_following_marker_is_untouched():
    md = "<!-- p.3 -->\n\n## A heading\n\nBody.\n"
    assert lift_heading_markers(md) == (md, 0)


def test_two_headings_in_a_row_each_keep_their_own_marker():
    md = ("## First\n\n<!-- p.5 -->\n\n## Second\n\n<!-- p.6 -->\n\nBody.\n")
    out, moved = lift_heading_markers(md)
    assert moved == 2
    assert out.index("<!-- p.5 -->") < out.index("## First") < out.index("<!-- p.6 -->")
    assert out.index("<!-- p.6 -->") < out.index("## Second")


def test_a_fresh_conversion_already_lifts_them():
    """The source writes the marker on the body, so convert() must not reproduce the bug."""
    out, _ = convert("## The Action\n\nIts body. [Source: Page 22]\n")
    assert out.index("<!-- p.22 -->") < out.index("## The Action")


# ── the stranded superscript ─────────────────────────────────────────────────────────────

def test_a_spaced_superscript_is_restored():
    """The PDF prints A²ZERO; a transcription that loses the superscript strands the 2."""
    out, n = fix_spaced_superscript("guide the A 2 ZERO initiative: Equity")
    assert out == "guide the A²ZERO initiative: Equity"
    assert n == 1


def test_the_spellings_the_document_really_uses_are_left_alone():
    """Page 14 prints A²ZERO and A2Zero within two lines. Both are the document's own."""
    src = "A²ZERO and A2ZERO and A2Zero and A2zero"
    assert fix_spaced_superscript(src) == (src, 0)


def test_a_bare_number_two_between_words_is_not_touched():
    assert fix_spaced_superscript("Strategy A 2 of the plan")[1] == 0
    assert fix_spaced_superscript("in 2 ZERO emissions")[1] == 0
