"""A contents page is metadata about the document, not content in it.

The CAP's three contents pages occupied 83 lines of output -- 69 blockquotes, 7 plain lines
and a heading -- because the conversion treated their dot-leader rows as prose. That is what
Caleb saw: "Some content is tagged as recovered by coverage sweep and other are just plain
text", and "Strategy 1 is recovered at line 16, but should be in the TOC following The Living
Carbon Neutrality Strategy".

Both complaints have one cause. The hierarchy those pages state is now held structurally in
the toc json, so their RENDERING -- the leaders, the page numbers, the reading order the
sweep guessed at -- has nothing left to contribute.

SUPPRESSED AS A COMMENT, NOT AS A PLACEHOLDER LINE. The hand-prepared standard writes
"[TABLE OF CONTENTS - SUMMARIZED IN METADATA]" into the text; here that would land in the
canonical text, where a claim could be extracted from it. Pipeline speech goes in comments,
which canonical.build() excludes by construction.
"""
from __future__ import annotations

from pipeline.render_blocks import render_blocks


def _blk(kind, page, text, **kw):
    return {"kind": kind, "page_no": page, "text": text, "self_ref": f"#/{page}/{text[:6]}",
            "bbox": (0, 0, 10, 10), **kw}


def test_blocks_on_a_contents_page_do_not_reach_the_prose():
    blocks = [_blk("UncoveredText", 2, "Welcome Letter ................... 5"),
              _blk("TextItem", 3, "Introduction ..................... 12"),
              _blk("TextItem", 5, "Even during the COVID-19 pandemic we face no greater threat.")]
    out = render_blocks(blocks, {}, "T", toc_pages={2, 3})
    assert "Welcome Letter" not in out
    assert "Introduction ......" not in out
    assert "COVID-19" in out


def test_each_contents_page_says_what_happened_to_it():
    """Silence would be indistinguishable from the pages having been lost."""
    blocks = [_blk("UncoveredText", 2, "Welcome Letter ... 5"),
              _blk("UncoveredText", 3, "Introduction ... 12")]
    out = render_blocks(blocks, {}, "T", toc_pages={2, 3})
    assert out.count("CONTENTS PAGE") == 2


def test_the_note_is_a_comment_so_canonical_text_never_sees_it():
    """A visible placeholder would be extractable as a claim."""
    from pipeline.canonical import build
    blocks = [_blk("UncoveredText", 2, "Welcome Letter ... 5"),
              _blk("TextItem", 5, "Real prose on a real page.")]
    md = render_blocks(blocks, {}, "T", toc_pages={2})
    assert "CONTENTS PAGE" in md
    assert "CONTENTS PAGE" not in build(md).text


def test_a_document_with_no_contents_page_is_untouched():
    blocks = [_blk("TextItem", 1, "First."), _blk("TextItem", 2, "Second.")]
    assert render_blocks(blocks, {}, "T") == render_blocks(blocks, {}, "T", toc_pages=set())
