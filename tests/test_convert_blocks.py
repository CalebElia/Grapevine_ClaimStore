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

from pipeline.convert_blocks import (is_caption_candidate, is_page_footer,
                                     looks_like_caption, split_index_lines)


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


# ── a caption sits BELOW its picture, or inside it -- never above ──────────────────────
# Measured on all 13 Docling-LINKED captions in Year 5: 13 below, 0 above, 0 beside. The
# false positive this pins was mine, caught by the association audit trail on its first
# run: the introduction letter's sign-off ("Missy, Simi, Steve, DeAndre'...") sits 27pt
# ABOVE the team photo, was short enough to look caption-shaped, and got dropped as a
# caption -- deleting the OSI staff roster, which is real content and a seed for the
# actor registry besides.

def test_a_block_above_a_picture_is_not_its_caption():
    """The real Year 5 page 3 sign-off case."""
    pic = {"page_no": 3, "self_ref": "#/pictures/5", "worth_extraction": False,
           "x0": 119.0, "x1": 566.0, "top": 544.0, "bottom": 712.0}
    signoff = {"page_no": 3, "x0": 119.0, "x1": 566.0, "top": 494.0, "bottom": 517.0}
    assert associate_caption(signoff, [pic]) is None


def test_a_block_below_a_picture_is_its_caption():
    """The shape all 13 linked Year 5 captions take -- e.g. p.3's real caption at
    y720-741 under the photo at y544-712."""
    pic = {"page_no": 3, "self_ref": "#/pictures/5", "worth_extraction": False,
           "x0": 119.0, "x1": 566.0, "top": 544.0, "bottom": 712.0}
    cap = {"page_no": 3, "x0": 119.0, "x1": 566.0, "top": 720.0, "bottom": 741.0}
    assert associate_caption(cap, [pic]) is pic


def test_a_block_inside_a_picture_is_still_its_caption():
    """Page 6: Docling absorbed the caption region into the photograph's own bbox."""
    pic = {"page_no": 6, "self_ref": "#/pictures/8", "worth_extraction": False,
           "x0": 117.0, "x1": 565.0, "top": 280.0, "bottom": 520.0}
    cap = {"page_no": 6, "x0": 148.0, "x1": 499.0, "top": 300.0, "bottom": 312.0}
    assert associate_caption(cap, [pic]) is pic


def test_front_matter_on_the_cover_is_never_treated_as_a_caption():
    """Geometry cannot separate this one, which is why it is a structural exemption
    rather than another threshold. Year 5's cover subtitle "June 1, 2024 - May 31, 2025"
    occupies x111-375, y435-452; a cover collage photo occupies x293-599, y398-580. The
    date sits INSIDE the photo on both axes -- dy=0, dx=0 -- exactly like a caption. Four
    successive geometric rules each fixed one case and broke another; a cover page simply
    has no captions to find, because it has no body prose to caption.
    """
    assert not is_caption_candidate({"kind": "TextItem", "page_no": 1,
                                     "text": "June 1, 2024 - May 31, 2025"})


def test_the_same_text_shape_on_a_body_page_is_still_a_candidate():
    assert is_caption_candidate({"kind": "TextItem", "page_no": 11,
                                 "text": "Michael Hagan leading the tour, 2024."})


# ── a heading Docling mistyped must never be deleted as a caption ──────────────────────
# The first real generalization failure, found by running Year 4 unchanged. Docling typed
# Year 4's long-form strategy headings as SectionHeaderItem on pages 5, 13, 15 and 19 --
# but typed the page 11 one as an ordinary TextItem. At 20 words it slipped under the
# 25-word caption ceiling (measured against Year 5, whose longest caption is 15 words),
# sat adjacent to a photo, and was DELETED. A word-count threshold cannot separate these;
# the document's own confirmed headings can.

from pipeline.convert_blocks import heading_shapes


def test_heading_shapes_are_learned_from_the_documents_own_headings():
    blocks = [{"kind": "SectionHeaderItem", "text": "STRATEGY 1: 100% RENEWABLES"},
              {"kind": "SectionHeaderItem", "text": "STRATEGY 2: BENEFICIAL ELECTRIFICATION"},
              {"kind": "TextItem", "text": "Ordinary prose about renewables."}]
    assert heading_shapes(blocks) == {"strategy 1", "strategy 2"}


def test_a_mistyped_heading_sharing_a_confirmed_shape_is_not_a_caption_candidate():
    """The exact Year 4 page 11 case."""
    shapes = {"strategy 1", "strategy 2", "strategy 3", "strategy 4"}
    blk = {"kind": "TextItem", "page_no": 11, "text":
           "STRATEGY 3: Significantly Improve the Energy Efficiency in our Homes, "
           "Businesses, Schools, Places of Worship, Recreational Sites, and "
           "Government Facilities"}
    assert not is_caption_candidate(blk, shapes)


def test_a_real_caption_is_unaffected_by_the_heading_shapes():
    shapes = {"strategy 1", "strategy 2"}
    blk = {"kind": "TextItem", "page_no": 23, "text": "Emergency kit supplies distribution."}
    assert is_caption_candidate(blk, shapes)


def test_shapes_are_optional_so_existing_callers_keep_working():
    assert is_caption_candidate({"kind": "TextItem", "page_no": 5, "text": "A caption."})


# ── every caption drop is validated and audited, whatever proposed it ──────────────────
# Year 4's line-by-line human audit found two SECTION HEADINGS deleted as captions, and
# the audit trail reported "0 unlinked captions dropped" while it happened -- because both
# arrived through paths that bypassed both the guard and the log:
#   STRATEGY 6  DOCLING ITSELF linked "STRATEGY 6: Enhance the Resilience of Our People
#               and Our Place" as the caption of picture 16. Docling's structural linkage
#               was being trusted as ground truth; it is a proposal like any other.
#   STRATEGY 2  the coverage sweep's own association loop, which applied geometry with no
#               shape guard and recorded nothing.
# A guard that only covers the path you were thinking about is not a guard.

def test_a_docling_supplied_caption_link_is_still_validated():
    """The STRATEGY 6 case: a heading Docling declared to be a caption stays a heading."""
    shapes = {"strategy 6"}
    blk = {"kind": "TextItem", "page_no": 17, "caption_for": "#/pictures/16",
           "text": "STRATEGY 6: Enhance the Resilience of Our People and Our Place"}
    assert not is_caption_candidate(blk, shapes)


def test_a_swept_region_matching_a_heading_shape_is_still_validated():
    """The STRATEGY 2 case: recovered by the sweep, then dropped by unguarded geometry."""
    shapes = {"strategy 2"}
    blk = {"kind": "UncoveredText", "page_no": 8, "caption_for": "#/pictures/5",
           "text": "STRATEGY 2: Switch our Appliances and Vehicles from Gasoline, "
                   "Diesel, Propane, Coal, and Natural Gas to Electric"}
    assert not is_caption_candidate(blk, shapes)


def test_an_uncovered_region_can_still_be_a_caption_when_it_is_not_a_heading():
    """The sweep must keep working for the cases it was built for."""
    assert is_caption_candidate({"kind": "UncoveredText", "page_no": 6,
                                 "text": "City officials break ground on Fire Station 4."},
                                {"strategy 1"})


def test_line_grouping_scales_with_font_size_not_a_fixed_gap():
    """Year 4 page 8: the STRATEGY 2 heading is large-font, so its LEADING is 24pt --
    above the 18pt constant tuned on body text. The sweep shredded the heading into three
    groups, and the fragments ("Vehicles from Gasoline, Diesel, Propane, Coal,") no longer
    began with "STRATEGY 2:", so the heading-shape guard could not recognise them and two
    thirds of a section heading were deleted. Line spacing scales with font size; a fixed
    gap cannot.
    """
    big = [{"text": "STRATEGY 2: Switch our Appliances and", "x0": 50, "x1": 400,
            "top": 250, "bottom": 270},
           {"text": "Vehicles from Gasoline, Diesel, Propane, Coal,", "x0": 50, "x1": 400,
            "top": 274, "bottom": 294},
           {"text": "and Natural Gas to Electric", "x0": 50, "x1": 300,
            "top": 298, "bottom": 318}]
    groups = group_uncovered(big, page_no=8)
    assert len(groups) == 1, "one heading, not three fragments"
    assert "Natural Gas to Electric" in groups[0]["text"]


def test_small_body_text_still_splits_at_a_real_paragraph_break():
    """The scaling must not merge genuinely separate small-text regions."""
    small = [{"text": "first region", "x0": 50, "x1": 200, "top": 100, "bottom": 110},
             {"text": "far away region", "x0": 50, "x1": 200, "top": 700, "bottom": 710}]
    assert len(group_uncovered(small, page_no=3)) == 2


# ── page-number footers Docling emits as ordinary blocks ───────────────────────────────
# Year 4's audit flagged bare "1" on page 1 and bare "24" on page 24. convert_document
# strips these for the pdfplumber backend (_strip_footer_number, validated against each
# page's own index), and the coverage sweep strips them from recovered groups -- but
# Docling emits some footers as ordinary TextItem blocks, and that third path had no
# strip at all. A page footer is not prose and has no place in a citable span.

def test_a_block_that_is_only_its_own_page_number_is_dropped():
    assert is_page_footer({"text": "24", "page_no": 24})
    assert is_page_footer({"text": "1", "page_no": 1})


def test_a_number_that_is_not_this_pages_number_is_kept():
    """Validated against the page's own index, exactly as the pdfplumber backend does --
    a standalone '2,862' or a year is real content."""
    assert not is_page_footer({"text": "17", "page_no": 24})
    assert not is_page_footer({"text": "2,862", "page_no": 3})


def test_real_prose_is_never_a_footer():
    assert not is_page_footer({"text": "Reached 8,903 trees planted.", "page_no": 17})


# ── an index keeps its line structure ──────────────────────────────────────────────────
# Year 4's audit: the table of contents came through as one run-on line. It is recovered
# by the coverage sweep, and the sweep merges a group's lines into a single string --
# correct for a two-line caption, wrong for an index, where each line is its own entry.
# The signal is the document's own: every TOC line ends in its page number.

def _line(text, top):
    return {"text": text, "x0": 50, "x1": 300, "top": top, "bottom": top + 20}


def test_index_lines_become_one_block_each():
    words = [_line("INTRODUCTION 3", 248), _line("GREENHOUSE GAS EMISSIONS 4", 272),
             _line("STRATEGY 1: 100% RENEWABLES 5", 296)]
    groups = group_uncovered(words, page_no=2)
    assert len(groups) == 3
    assert groups[0]["text"] == "INTRODUCTION 3"
    assert groups[2]["text"] == "STRATEGY 1: 100% RENEWABLES 5"


def test_a_multiline_caption_still_merges_into_one_block():
    """Lines not ending in numbers are prose and must keep merging."""
    words = [_line("Michael Hagan from the Green Energy Neighbors", 400),
             _line("leading the Net-Zero Home Energy Tour.", 420)]
    assert len(group_uncovered(words, page_no=11)) == 1


def test_a_single_line_ending_in_a_number_is_not_an_index():
    """One line proves nothing; an index is a repeated structure."""
    assert len(group_uncovered([_line("Reached 8,903 trees planted 17", 300)],
                               page_no=3)) == 1


# ── an index inside a DOCLING block keeps its lines too ────────────────────────────────
# The two years reach the same content by different paths: Year 5 has no Docling block for
# its table of contents (so the coverage sweep handles it), while Year 4's arrives as a
# single Docling block -- typed CodeItem, of all things -- and was flattened to one
# run-on line before any index check could see it. The check has to run on the RAW crop,
# where the line breaks still exist.

def test_a_raw_index_crop_is_split_into_one_line_each():
    raw = ("INTRODUCTION 3\nGREENHOUSE GAS EMISSIONS 4\n"
           "STRATEGY 1: 100% RENEWABLES 5\nCLOSING 24")
    assert split_index_lines(raw) == ["INTRODUCTION 3", "GREENHOUSE GAS EMISSIONS 4",
                                      "STRATEGY 1: 100% RENEWABLES 5", "CLOSING 24"]


def test_an_ordinary_paragraph_crop_is_not_split():
    raw = ("Four years ago this month, Ann Arbor's climate action plan,\n"
           "known as A2ZERO, was adopted. This plan sets the foundation\n"
           "for how the community will achieve carbon neutrality.")
    assert split_index_lines(raw) is None


def test_a_two_line_crop_ending_in_numbers_is_not_enough_to_be_an_index():
    """Guard against splitting a wrapped sentence that happens to end in figures."""
    assert split_index_lines("saving residents $101,650\non costs in 2024") is None
