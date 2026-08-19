"""Hyperlink harvesting: every URI the corpus itself points at, anchored to the verbatim
text that cites it.

WHY THIS IS A FIRST-CLASS EXTRACTION AND NOT A CURIOSITY. PLAN.md's corpus-growth rule
says new sources arrive via research_questions -> source_targets rather than by guessing
what to search for. A document's own outbound links are the highest-quality version of
that: the source is telling us, in its own words and at a specific claim, where its
evidence lives. Year 5 carries 109 of them.

WHAT MAKES A LINK CITABLE RATHER THAN JUST COLLECTED. A bare URL list is nearly useless
six months later -- it cannot answer "which sentence relied on this?" or "was this the
2024 or 2025 version of that page?". So each record carries the anchor text, the page,
and the character span of the BLOCK that contains it, which is the same span a claim
would cite.
"""
from __future__ import annotations

from pipeline.extract_links import anchor_span, merge_link_rects

# Real Year 5 page 7 data: ONE link whose anchor text wraps across two lines, so
# pdfplumber reports it as two rects sharing a URI.
_WRAPPED = [
    {"uri": "https://www.a2gov.org/fire-department/net-zero-fire-station/",
     "x0": 393.8, "x1": 564.0, "top": 113.6, "bottom": 125.7},
    {"uri": "https://www.a2gov.org/fire-department/net-zero-fire-station/",
     "x0": 154.8, "x1": 186.1, "top": 127.6, "bottom": 139.7},
]


def test_one_link_split_across_two_lines_merges_into_a_single_record():
    """Counting rects would double-count this link and imply the report cites the fire
    station page twice when it cites it once."""
    out = merge_link_rects(_WRAPPED)
    assert len(out) == 1
    assert out[0]["uri"].endswith("net-zero-fire-station/")


def test_a_merged_link_spans_the_union_of_its_rects():
    out = merge_link_rects(_WRAPPED)
    assert out[0]["top"] == 113.6
    assert out[0]["bottom"] == 139.7


def test_the_same_url_far_apart_on_a_page_stays_two_links():
    """Two genuinely separate citations of one source are two citations -- merging them
    would understate how often the document leans on it."""
    links = [{"uri": "https://example.org/a", "x0": 50, "x1": 120, "top": 100, "bottom": 112},
             {"uri": "https://example.org/a", "x0": 50, "x1": 120, "top": 600, "bottom": 612}]
    assert len(merge_link_rects(links)) == 2


def test_different_urls_never_merge_however_close():
    links = [{"uri": "https://example.org/a", "x0": 50, "x1": 120, "top": 100, "bottom": 112},
             {"uri": "https://example.org/b", "x0": 50, "x1": 120, "top": 113, "bottom": 125}]
    assert len(merge_link_rects(links)) == 2


# ── tying a link to the verbatim span that cites it ────────────────────────────────────

def test_anchor_text_is_located_inside_its_block_with_absolute_offsets():
    """The offsets must be absolute into the document text, not relative to the block,
    so a link record and a claim cite the same coordinate system."""
    block = {"text": "We supported the groundbreaking for the net zero fire station.",
             "char_start": 1000}
    s, e = anchor_span("net zero fire station", block)
    assert block["text"][s - 1000:e - 1000] == "net zero fire station"
    assert s >= 1000


def test_an_anchor_that_is_not_in_the_block_returns_none_rather_than_guessing():
    """pdfplumber's link rect and its text extraction can disagree at the margins. A
    wrong span is worse than an absent one: it would attribute a URL to a sentence that
    never cited it.
    """
    block = {"text": "Some unrelated sentence entirely.", "char_start": 0}
    assert anchor_span("net zero fire station", block) is None


def test_anchor_matching_tolerates_whitespace_differences():
    """Block text is flattened; the cropped anchor may carry a line break."""
    block = {"text": "the net zero fire station opened", "char_start": 0}
    assert anchor_span("net  zero\nfire station", block) is not None


# ── context_sentence ───────────────────────────────────────────────────────────────────
# Requested in review: anchor_text stays the exact hyperlinked words, and a separate
# context_sentence carries the whole sentence around it. Pulled from the COMPILED spine,
# where blocks are already flattened into clean prose -- not from any raw converter arm,
# where a sentence may still be split across visual lines or interleaved across columns.

from pipeline.extract_links import sentence_around


def test_the_sentence_containing_the_span_is_returned_whole():
    t = "First sentence here. The City secured $5,000,000 for the SEU. Third one."
    i = t.index("secured")
    assert sentence_around(t, i, i + 7) == "The City secured $5,000,000 for the SEU."


def test_a_decimal_inside_a_figure_does_not_end_the_sentence():
    """Real corpus text: '5.4MW' and '$5,000,000' must not split a sentence."""
    t = "Our Solarize program reached 5.4MW of solar installed. Next sentence."
    i = t.index("Solarize")
    assert sentence_around(t, i, i + 8).endswith("installed.")
    assert "5.4MW" in sentence_around(t, i, i + 8)


def test_an_abbreviation_does_not_end_the_sentence():
    """Real corpus text: 'Dr. Missy Stults' appears throughout."""
    t = "We met Dr. Missy Stults at the event. Then we left."
    i = t.index("Missy")
    assert sentence_around(t, i, i + 5) == "We met Dr. Missy Stults at the event."


def test_a_span_in_the_first_sentence_has_no_leading_bleed():
    t = "The first sentence. The second one."
    assert sentence_around(t, 4, 9) == "The first sentence."


def test_a_span_in_the_last_sentence_returns_to_the_end():
    t = "First. Learn more at www.a2gov.org/a2seu"
    i = t.index("Learn")
    assert sentence_around(t, i, i + 5) == "Learn more at www.a2gov.org/a2seu"


def test_a_span_crossing_a_block_boundary_does_not_swallow_the_next_block():
    """Blocks are separated by a blank line in the spine; a sentence never spans one."""
    t = "- First bullet ends here.\n\n- Second bullet begins."
    i = t.index("First")
    assert "Second bullet" not in sentence_around(t, i, i + 5)


def test_an_out_of_range_span_returns_empty_rather_than_raising():
    assert sentence_around("short", 900, 950) == ""
