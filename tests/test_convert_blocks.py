"""Block-based conversion: Docling proposes block boundaries and reading order,
pdfplumber supplies the exact characters inside each block.

WHY THIS EXISTS. pdfplumber alone reads glyphs in x-then-y order with no layout model,
so on the real Year 5 page 6 it interleaves two columns on every line -- "- Our Solarize
program reached 5.4MW of - In deep collaboration with the Ann Arbor". Docling alone reads
the order correctly but fragments this corpus's central term, rendering A2ZERO as
"A 2 ZERO" 40 times of 53. Cropping pdfplumber to Docling's block boxes gets both.

Every fixture here is real geometry or real text from a2zero-year5.pdf, never invented
round numbers -- a synthetic bbox can be accidentally symmetric and pass a test that a
sign error would fail on the real thing.
"""
from __future__ import annotations

from pipeline.convert_blocks import (
    bbox_to_crop, build_evidence, decide_hyphen, flatten_block,
)


# ── coordinate conversion ──────────────────────────────────────────────────────────────
# Docling bbox is (l, t, r, b) in PDF points, BOTTOMLEFT origin. pdfplumber's crop wants
# (x0, top, x1, bottom) in points, TOP-LEFT origin. Real page 6 heading block below.

_REAL_HEADING_BBOX = (45.0, 477.0, 435.0, 462.0)   # "STRATEGY 1: 100% RENEWABLES"
_PAGE_H = 792.0


def test_bottomleft_bbox_becomes_a_topleft_crop_box():
    x0, top, x1, bottom = bbox_to_crop(_REAL_HEADING_BBOX, "CoordOrigin.BOTTOMLEFT",
                                       _PAGE_H, page_w=612.0, pad=0)
    assert (x0, x1) == (45.0, 435.0)
    assert top == _PAGE_H - 477.0
    assert bottom == _PAGE_H - 462.0
    assert top < bottom, "in top-left space the box's top must be above its bottom"


def test_padding_expands_the_box_without_leaving_the_page():
    x0, top, x1, bottom = bbox_to_crop((0.0, 792.0, 612.0, 0.0), "CoordOrigin.BOTTOMLEFT",
                                       _PAGE_H, page_w=612.0, pad=5)
    assert x0 == 0.0 and top == 0.0, "padding must clamp at the page edge, not go negative"
    assert x1 == 612.0 and bottom == 792.0, "padding must clamp at the far edge too"


def test_a_topleft_origin_box_is_not_flipped():
    assert bbox_to_crop((10.0, 20.0, 110.0, 120.0), "CoordOrigin.TOPLEFT", 792.0,
                        page_w=612.0, pad=0) == (10.0, 20.0, 110.0, 120.0)


# ── flattening one block's cropped text ────────────────────────────────────────────────

def test_line_wraps_inside_a_block_collapse_to_one_line():
    """A block IS a semantic paragraph, so its internal line breaks are the PDF's visual
    layout, not meaning. This is the fix for the reported "newline structure directly
    mirrors the PDF" problem.
    """
    raw = "Strategy 1 of A2ZERO focuses on powering our electrical grid\nwith 100% renewable energy."
    assert flatten_block(raw) == ("Strategy 1 of A2ZERO focuses on powering our "
                                  "electrical grid with 100% renewable energy.")


def test_runs_of_whitespace_collapse_to_single_spaces():
    assert flatten_block("a   b\n\n  c") == "a b c"


def test_an_empty_block_flattens_to_empty_string_not_none():
    assert flatten_block("") == ""


# ── de-hyphenation evidence, gathered independently ────────────────────────────────────

def test_evidence_never_counts_the_ambiguous_sequences_themselves():
    """The circular-evidence bug this pins, caught on the real document: building the
    vocabulary from text with the line-break hyphens already joined made EVERY joined
    form trivially "seen", so "science-based" scored as if "sciencebased" were a real
    word. Evidence must come only from text that never contained one of these splits.
    """
    text = "col-\nlaborations happen when people collaborate"
    words, _ = build_evidence(text)
    assert "collaborations" not in words, "the split itself must not vouch for itself"
    assert "collaborate" in words, "ordinary words are still evidence"


def test_a_hyphenated_compound_elsewhere_is_evidence_for_keeping_the_hyphen():
    _, hyph = build_evidence("our community-wide plan is community-wide indeed")
    assert "community-wide" in hyph


# ── the decision, per case ─────────────────────────────────────────────────────────────

def test_joined_form_seen_elsewhere_means_join():
    """Real case from Year 5: "col-\\nlaborations", with "collaborations" appearing
    elsewhere in the document on its own.
    """
    words, hyph = {"collaborations"}, set()
    assert decide_hyphen("col", "laborations", words, hyph) == "join"


def test_hyphenated_form_seen_elsewhere_means_keep_the_hyphen():
    """Real case: "community-\\nwide", with "community-wide" appearing elsewhere."""
    words, hyph = set(), {"community-wide"}
    assert decide_hyphen("community", "wide", words, hyph) == "hyphen"


def test_no_evidence_either_way_is_ambiguous_not_a_guess():
    """5 of Year 5's 9 cases land here (Collab-orator, science-based, zero-emission,
    five-e, to-Government). Guessing silently is how a wrong word enters a citation, so
    the honest outcome is to say so and let a later pass decide.
    """
    assert decide_hyphen("zero", "emission", set(), set()) == "ambiguous"


def test_evidence_on_both_sides_is_ambiguous_rather_than_arbitrary():
    """Both forms genuinely occur -- picking one by rule order would be a coin flip
    dressed as a decision."""
    assert decide_hyphen("well", "being", {"wellbeing"}, {"well-being"}) == "ambiguous"


def test_the_decision_is_case_insensitive():
    """Real case: "Collab-\\norator" -- the capital must not hide matching evidence."""
    assert decide_hyphen("Collab", "orator", {"collaborator"}, set()) == "join"


# ── assembling blocks into the Conversion envelope ─────────────────────────────────────
# The page map is the citation spine (see convert_document.py), so block assembly has to
# produce one that is exactly as trustworthy as pdfplumber's own.

from pipeline.convert_blocks import assemble_blocks


def _b(kind, page, text):
    return {"kind": kind, "page_no": page, "text": text}


def test_blocks_on_the_same_page_join_with_a_blank_line_between_them():
    """Breaks BETWEEN blocks are real structure and must survive, unlike the line wraps
    inside a block which flatten_block() collapses."""
    text, _, _ = assemble_blocks([_b("TextItem", 1, "First para."),
                                  _b("TextItem", 1, "Second para.")], n_pages=1)
    assert text == "First para.\n\nSecond para."


def test_every_block_gets_offsets_that_slice_back_to_its_own_text():
    """The round-trip that makes a block citable: text[start:end] IS the block."""
    blocks = [_b("SectionHeaderItem", 1, "STRATEGY 1: 100% RENEWABLES"),
              _b("TextItem", 1, "Strategy 1 of A2ZERO focuses on renewables."),
              _b("ListItem", 2, "- Our Solarize program reached 5.4MW.")]
    text, _, out = assemble_blocks(blocks, n_pages=2)
    for blk in out:
        assert text[blk["char_start"]:blk["char_end"]] == blk["text"]


def test_the_page_map_locates_each_block_on_its_own_page():
    blocks = [_b("TextItem", 1, "Page one content."),
              _b("TextItem", 2, "Page two content.")]
    text, page_map, out = assemble_blocks(blocks, n_pages=2)
    page_of = lambda off: next(p for p, lo, hi in page_map if lo <= off < hi)
    assert page_of(out[0]["char_start"]) == 1
    assert page_of(out[1]["char_start"]) == 2


def test_a_page_with_no_blocks_still_occupies_a_slot_in_the_page_map():
    """A photo-only page yields no text blocks. Dropping it would shift every later
    page's number by one -- silently misattributing every citation after it.
    """
    blocks = [_b("TextItem", 1, "One."), _b("TextItem", 3, "Three.")]
    _, page_map, out = assemble_blocks(blocks, n_pages=3)
    assert [p for p, _, _ in page_map] == [1, 2, 3]
    page_of = lambda off: next(p for p, lo, hi in page_map if lo <= off < hi)
    assert page_of(out[1]["char_start"]) == 3, "page 3 content must still report page 3"


def test_block_kind_survives_assembly_so_the_renderer_can_type_it():
    """Headings must render as headings rather than leaking into body text -- the
    reported duplicate-header problem."""
    _, _, out = assemble_blocks([_b("SectionHeaderItem", 1, "CLOSING")], n_pages=1)
    assert out[0]["kind"] == "SectionHeaderItem"


def test_reading_order_is_preserved_exactly_as_given():
    """Docling's order IS the fix for column interleaving; re-sorting here would undo it."""
    blocks = [_b("ListItem", 1, "- left column first"),
              _b("ListItem", 1, "- right column second")]
    text, _, _ = assemble_blocks(blocks, n_pages=1)
    assert text.index("left column") < text.index("right column")


# ── applying hyphen decisions ──────────────────────────────────────────────────────────
# ORDER MATTERS: this must run on the RAW cropped text, before flatten_block(), because
# flattening rewrites "col-\nlaborations" to "col- laborations" and destroys the newline
# the split is recognised by.

from pipeline.convert_blocks import apply_hyphen_decisions


def test_a_join_decision_removes_both_the_hyphen_and_the_break():
    out, amb = apply_hyphen_decisions("lots of new col-\nlaborations here",
                                      {"collaborations"}, set())
    assert "collaborations here" in out
    assert "col-" not in out and amb == []


def test_a_hyphen_decision_keeps_the_hyphen_and_removes_only_the_break():
    out, amb = apply_hyphen_decisions("our community-\nwide plan", set(), {"community-wide"})
    assert "community-wide plan" in out
    assert amb == []


def test_an_ambiguous_case_keeps_the_hyphen_and_is_reported():
    """Keeping the hyphen is the conservative choice -- it preserves a character the
    source actually contains, where joining deletes one. Either way the case is reported
    rather than silently resolved, since 5 of Year 5's 9 splits land here.
    """
    out, amb = apply_hyphen_decisions("a zero-\nemission fleet", set(), set())
    assert "zero-emission fleet" in out
    assert amb == [("zero", "emission")]


def test_several_splits_in_one_block_are_all_handled():
    out, amb = apply_hyphen_decisions("col-\nlaborations and zero-\nemission goals",
                                      {"collaborations"}, set())
    assert "collaborations" in out and "zero-emission" in out
    assert amb == [("zero", "emission")]


def test_text_with_no_splits_is_returned_unchanged():
    src = "An ordinary sentence with a mid-line hyphen in community-wide usage."
    out, amb = apply_hyphen_decisions(src, set(), set())
    assert out == src and amb == []


# ── the coverage sweep: no text may be silently dropped ────────────────────────────────
# Found by auditing the real Year 5 run against pdfplumber's own word list. Two captions
# the human review had already flagged were missing from the converted text entirely:
#   p.5 "The Renewable Energy tab of the A2ZERO Dashboard." -- in NO Docling block at all
#   p.6 "City officials break ground on Fire Station 4..."  -- absorbed INSIDE the photo's
#        own bounding box, so no text block was emitted for it
# Trusting Docling's block list alone loses both without erroring, which is precisely the
# founding failure mode of this project. The sweep re-attaches anything left over.

from pipeline.convert_blocks import group_uncovered, uncovered_words


def _w(text, x0, top):
    return {"text": text, "x0": x0, "x1": x0 + 10 * len(text),
            "top": top, "bottom": top + 10}


def test_a_word_inside_a_text_block_is_covered():
    assert uncovered_words([_w("hello", 10, 10)], [(0, 0, 200, 100)]) == []


def test_a_word_outside_every_block_is_reported():
    out = uncovered_words([_w("orphan", 10, 500)], [(0, 0, 200, 100)])
    assert [w["text"] for w in out] == ["orphan"]


def test_a_word_inside_a_picture_box_is_still_uncovered_if_no_text_block_holds_it():
    """The real p.6 case: the caption sits inside the photograph's bbox. A picture box
    must not count as coverage, or the caption vanishes with the photo.
    """
    out = uncovered_words([_w("caption", 10, 10)], text_boxes=[])
    assert [w["text"] for w in out] == ["caption"]


def test_words_on_the_same_visual_line_group_into_one_block():
    words = [_w("City", 10, 300), _w("officials", 60, 300), _w("break", 150, 300)]
    groups = group_uncovered(words, page_no=6)
    assert len(groups) == 1
    assert groups[0]["text"] == "City officials break"
    assert groups[0]["page_no"] == 6


def test_words_on_separate_lines_group_together_when_adjacent():
    """A two-line caption is one caption, not two fragments."""
    words = [_w("Michigan's first", 10, 300), _w("net-zero fire station.", 10, 312)]
    groups = group_uncovered(words, page_no=6)
    assert len(groups) == 1


def test_words_far_apart_vertically_stay_separate_blocks():
    words = [_w("header text", 10, 100), _w("footer text", 10, 700)]
    groups = group_uncovered(words, page_no=6)
    assert len(groups) == 2


def test_an_uncovered_group_is_typed_so_the_renderer_can_mark_it():
    groups = group_uncovered([_w("orphan", 10, 300)], page_no=5)
    assert groups[0]["kind"] == "UncoveredText"


def test_a_lone_page_number_is_not_promoted_into_a_block():
    """23 of 24 pages end in a bare footer number that Docling correctly excludes and
    pdfplumber's own backend already strips. The sweep must not resurrect them."""
    assert group_uncovered([_w("17", 300, 740)], page_no=17) == []


# ── associating a stray caption with its picture ───────────────────────────────────────
# The two real Year 5 cases behave differently and both must work:
#   p.6 "City officials break ground..." sits INSIDE the photograph's bbox  -> containment
#   p.5 "The Renewable Energy tab of the A2ZERO Dashboard." sits just BELOW the dashboard
#       image, outside its bbox                                            -> proximity
# Without association a caption cannot follow its picture's fate, which is the whole of
# the requested rule: drop the caption when the picture is dropped, fold it in when kept.

from pipeline.convert_blocks import associate_caption

# real geometry, top-left space
_PHOTO_P6 = {"kind": "PictureItem", "page_no": 6, "top": 280.0, "bottom": 520.0,
             "x0": 117.0, "x1": 565.0, "worth_extraction": False}
_DASH_P5 = {"kind": "PictureItem", "page_no": 5, "top": 384.0, "bottom": 734.0,
            "x0": 117.0, "x1": 565.0, "worth_extraction": True}


def test_a_caption_inside_a_picture_box_is_associated_with_it():
    cap = {"page_no": 6, "top": 300.0, "bottom": 312.0, "x0": 148.0, "x1": 499.0}
    assert associate_caption(cap, [_PHOTO_P6]) is _PHOTO_P6


def test_a_caption_just_below_a_picture_is_associated_with_it():
    cap = {"page_no": 5, "top": 740.0, "bottom": 752.0, "x0": 117.0, "x1": 400.0}
    assert associate_caption(cap, [_DASH_P5]) is _DASH_P5


def test_a_caption_far_from_every_picture_is_associated_with_none():
    """The page 2 table-of-contents entries are uncovered text but not captions --
    associating them with a distant picture would delete the TOC."""
    toc = {"page_no": 2, "top": 200.0, "bottom": 212.0, "x0": 50.0, "x1": 300.0}
    assert associate_caption(toc, [_DASH_P5]) is None


def test_a_picture_on_another_page_is_never_associated():
    cap = {"page_no": 9, "top": 300.0, "bottom": 312.0, "x0": 148.0, "x1": 499.0}
    assert associate_caption(cap, [_PHOTO_P6]) is None


def test_the_nearest_picture_wins_when_two_are_candidates():
    near = {**_PHOTO_P6, "top": 320.0, "bottom": 330.0}
    far = {**_PHOTO_P6, "top": 100.0, "bottom": 110.0}
    cap = {"page_no": 6, "top": 340.0, "bottom": 352.0, "x0": 148.0, "x1": 499.0}
    assert associate_caption(cap, [far, near]) is near


def test_a_trailing_page_number_is_stripped_from_a_recovered_group():
    """Real artifact: the p.18 caption came back as 'Bicentennial tree planting, 2024. 18'
    -- the footer number swept up with it. Same footer bleed that corrupted the first
    review workbook, so the same validated rule applies: strip the trailing number only
    when it equals THIS page's own number.
    """
    words = [_w("Bicentennial", 10, 700), _w("planting,", 130, 700),
             _w("2024.", 220, 700), _w("18", 300, 706)]
    g = group_uncovered(words, page_no=18)
    assert g[0]["text"] == "Bicentennial planting, 2024."


def test_a_trailing_number_that_is_not_the_page_number_is_kept():
    """'...reached 5.4MW in 2024' on page 7 must not lose its figure to a footer rule."""
    words = [_w("installed", 10, 700), _w("2024", 130, 700)]
    g = group_uncovered(words, page_no=7)
    assert g[0]["text"].endswith("2024")


# ── the citation spine carries no markup, by construction ──────────────────────────────
# Asked directly during review: "Do any of the HTML blocks in the document pose an ingest
# risk?" They cannot, PROVIDED ingest reads the Conversion rather than the rendered
# markdown. That proviso is the whole answer, so it is pinned here rather than left as a
# convention someone could unknowingly break.

def test_the_text_spine_is_exactly_block_text_plus_separators():
    """assemble_blocks may join and separate, never annotate. Every HTML comment, figure
    XML block and page marker in the review markdown is added by render_blocks and exists
    only in that artifact -- so no model-generated content can reach a verbatim span.
    """
    blocks = [_b("SectionHeaderItem", 1, "CLOSING"),
              _b("TextItem", 1, "A2ZERO is our community's plan."),
              _b("ListItem", 2, "- Solar reached 5.4MW.")]
    text, _, _ = assemble_blocks(blocks, n_pages=2)
    for ch in "<>":
        assert ch not in text, f"the spine must never contain {ch!r} -- it carries no markup"
    stripped = text
    for b in blocks:
        assert b["text"] in text
        stripped = stripped.replace(b["text"], "", 1)
    assert stripped.strip() == "", "nothing but separators may remain between blocks"


def test_figure_xml_never_enters_the_spine():
    """Vision output is model-generated. If it reached the citation text it could be
    cited as though the document had said it -- the exact fabrication mode `verbatim`
    exists to prevent.
    """
    text, _, _ = assemble_blocks([_b("PictureItem", 1, ""),
                                  _b("TextItem", 1, "Real prose.")], n_pages=1)
    assert "figure_description" not in text
    assert text.strip() == "Real prose."


def test_every_block_is_independently_addressable_for_chunking():
    """Raised during review: blank lines between bullets made semantic chunking hard on
    an earlier prototype. With typed blocks a consumer never needs to chunk on whitespace
    at all -- each block already carries its kind, page and character span, so chunking
    is a structural join, not a text-splitting heuristic.
    """
    blocks = [_b("ListItem", 1, "- First accomplishment."),
              _b("ListItem", 1, "- Second accomplishment."),
              _b("ListItem", 1, "- Third accomplishment.")]
    text, _, out = assemble_blocks(blocks, n_pages=1)
    assert len(out) == 3
    for blk in out:
        assert text[blk["char_start"]:blk["char_end"]] == blk["text"]
        assert blk["kind"] == "ListItem"


# ── captions Docling emits but does not LINK ───────────────────────────────────────────
# Second review found two surviving caption bleeds: "Michael Hagan from the Green Energy
# Neighbors..." (p.11) and "Emergency kit supplies distribution." (p.23). Both are proper
# Docling TextItems with caption_for=None, sitting 6pt and 17pt from a photograph -- well
# inside the association gap. The rule only ran on uncovered strays, never on Docling's
# own unlinked blocks. Widening it needs a guard: a body paragraph beside a photo must
# never be mistaken for a caption and silently deleted.

from pipeline.convert_blocks import looks_like_caption


def test_a_short_textitem_beside_a_photo_is_caption_shaped():
    assert looks_like_caption({"kind": "TextItem",
                               "text": "Emergency kit supplies distribution."})


def test_a_long_paragraph_is_never_caption_shaped_however_close_to_a_photo():
    """The guard that matters: dropping a body paragraph is invisible data loss."""
    body = ("Every year the Office of Sustainability and Innovations conducts a "
            "greenhouse gas emissions inventory for City operations and the community "
            "as a whole, measuring progress towards the community's climate goals and "
            "guiding science-based target setting across every strategy area.")
    assert not looks_like_caption({"kind": "TextItem", "text": body})


def test_a_list_item_is_never_a_caption():
    """Bullets are the document's substance; a photo beside one changes nothing."""
    assert not looks_like_caption({"kind": "ListItem",
                                   "text": "- Solar reached 5.4MW."})


def test_a_section_header_is_never_a_caption():
    assert not looks_like_caption({"kind": "SectionHeaderItem", "text": "CLOSING"})


def test_the_real_p11_caption_is_caption_shaped():
    assert looks_like_caption({"kind": "TextItem", "text":
        "Michael Hagan from the Green Energy Neighbors leading the Net-Zero Home "
        "Energy Tour, 2024."})
