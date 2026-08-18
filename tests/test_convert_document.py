"""The conversion envelope: a span is only meaningful with its hash and page map.

These lock the three invariants a citation depends on. If any breaks, every claim in the
corpus silently starts pointing somewhere slightly different -- the failure this project
exists to prevent, occurring in the provenance layer itself.
"""

from __future__ import annotations

import hashlib

from pipeline.convert_document import PAGE_SEP, Conversion, _assemble, convert_from_markdown


def build(pages):
    text, page_map = _assemble(pages)
    return Conversion(source_path="t.pdf", converter="test", converter_version="0",
                      text=text, page_map=page_map,
                      content_hash=hashlib.sha256(text.encode()).hexdigest(),
                      n_pages=len(pages), converted_at="now")


def test_every_page_slice_returns_that_page():
    pages = ["first page text", "second page text", "third page text"]
    c = build(pages)
    for (n, lo, hi), original in zip(c.page_map, pages):
        assert c.slice(lo, hi) == original, f"page {n} does not round-trip"


def test_page_lookup_resolves_offsets_to_the_right_page():
    c = build(["alpha", "bravo", "charlie"])
    # page_map entries are (page_no, char_start, char_end) -- index 1 is the start offset.
    assert c.page_for(0) == 1
    assert c.page_for(c.page_map[1][1]) == 2
    assert c.page_for(c.page_map[2][1]) == 3
    assert c.page_for(c.page_map[2][2] - 1) == 3       # last char of the last page


def test_offsets_are_monotonic_and_non_overlapping():
    c = build([f"page {i} body" for i in range(1, 8)])
    prev_end = -1
    for _, lo, hi in c.page_map:
        assert lo >= prev_end and hi > lo
        prev_end = hi


def test_separator_falls_between_pages_and_belongs_to_neither():
    """The gap must be outside every page range, or a span could straddle a boundary."""
    c = build(["one", "two"])
    (_, _, end1), (_, start2, _) = c.page_map
    assert start2 - end1 == len(PAGE_SEP)
    assert c.page_for(end1) is None


def test_hash_changes_when_the_text_changes():
    """This is what makes a converter swap fail loudly instead of shifting offsets."""
    assert build(["same"]).content_hash != build(["same "]).content_hash


def test_empty_pages_still_produce_a_usable_map():
    """Year 2 has near-empty pages; the map must survive them rather than collapse."""
    c = build(["real content", "", "more content"])
    assert len(c.page_map) == 3
    assert c.slice(*c.page_map[1][1:]) == ""


def test_preexisting_markdown_is_wrapped_with_an_honest_single_page_map(tmp_path):
    """Corpus markdown carries no page anchors, and the envelope must not pretend it does."""
    p = tmp_path / "y5.md"
    p.write_text("# Report\n\nSome body text.")
    c = convert_from_markdown(p, "pdfplumber+claude")
    assert c.converter == "pdfplumber+claude"
    assert c.converter_version == "preexisting"
    assert c.page_map == [(1, 0, len(c.text))]
    assert c.page_for(3) == 1
