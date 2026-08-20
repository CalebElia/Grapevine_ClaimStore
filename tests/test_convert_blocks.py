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

from pipeline.convert_blocks import apply_hyphen_decisions, coverage_period


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

from pipeline.convert_blocks import (choose_block_text, is_caption_candidate,
                                     is_page_footer, looks_like_caption,
                                     split_index_lines)


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


# ── falling back to Docling's OCR when pdfplumber has no text layer ────────────────────
# Year 2 is an IMAGE-BASED PDF: its prose is rendered as pictures, so pdfplumber's text
# layer is nearly empty (9 words across the first 22 blocks, against Docling's 445) while
# Docling's OCR reads the document fine (2,995 words, all 15 dollar figures). Cropping
# pdfplumber to Docling's blocks therefore threw away a document that had been
# successfully read -- reproducing the exact 234-word, zero-dollar-figure catastrophe
# this corpus is famous for, and reporting success.
#
# OCR TEXT IS NOT CHARACTER-EXACT TEXT. It is a model's reading of pixels, the same
# category as the vision figure extraction, so it is marked rather than silently mixed in.

def test_pdfplumber_is_preferred_when_it_has_the_text():
    """Normal case: the two readings are comparable, so the character-exact one wins."""
    assert choose_block_text("Installed an additional 1.7MW of new solar",
                             "Installed an additional 1.7MW of new solar") == \
        ("Installed an additional 1.7MW of new solar", "pdfplumber")


def test_a_small_ordinary_difference_does_not_trigger_the_fallback():
    """Docling routinely differs slightly -- it fragments A2ZERO into three tokens, so it
    often reports MORE words than pdfplumber on a perfectly good page. That must not be
    read as pdfplumber failing."""
    p = "Strategy 1 of A2ZERO focuses on powering our electrical grid"
    d = "Strategy 1 of A 2 ZERO focuses on powering our electrical grid"
    assert choose_block_text(p, d)[1] == "pdfplumber"


def test_docling_ocr_is_used_when_pdfplumber_yields_almost_nothing():
    """The real Year 2 shape: one stray glyph against a full sentence."""
    d = ("Installed an additional 1.7Mw of new residential solar, bringing the "
         "total installed capacity in Ann Arbor to over 10MW")
    text, src = choose_block_text(".", d)
    assert src == "docling_ocr"
    assert text == d


def test_an_empty_pdfplumber_block_falls_back():
    text, src = choose_block_text("", "A2ZERO is Ann Arbor's plan for a just transition")
    assert src == "docling_ocr"


def test_neither_source_having_text_yields_nothing():
    assert choose_block_text("", "") == ("", "pdfplumber")


def test_a_short_docling_reading_never_triggers_the_fallback():
    """Below a few words there is not enough signal to call pdfplumber broken."""
    assert choose_block_text("", "2021 2022")[1] == "pdfplumber"


# ── list nesting, reconstructed from geometry ──────────────────────────────────────────
# Docling flattens it: on Year 2 page 13 it reports level=2 and marker '·' for the parent
# bullet "Continually wrote grants..." AND for all twelve grant sub-bullets beneath it.
# The human-healed reference indents them as children. The hierarchy matters for ingest --
# a grant claim with no link to its parent statement loses the sentence that frames it.
#
# The geometry still carries it (real page 13 left edges):
#     52.9  Continually wrote grants to support community-wide initiatives
#     88.6  $25,000 from the U.S. EPA ...        <- indented child
#    108.1  $2,500,000 from the federal ...      <- same level, ragged left edge
#    378.3  $270,000 from the American Lung ...  <- RIGHT COLUMN, still a child
# A naive "larger x0 = deeper" rule breaks on that last case: 378 exceeds 108, so the
# right column's grants would become grandchildren of a left-column sibling. Indentation
# has to be measured from each COLUMN's own left edge.

from pipeline.convert_blocks import (assign_nesting, column_edges, snap_scripts,
                                     absorb_body_shaped_headings)


def test_a_single_column_page_has_one_edge():
    assert column_edges([52.9, 88.6, 89.7, 108.1]) == [52.9]


def test_two_columns_are_detected_by_the_gutter():
    """Real page 13 x0 values."""
    xs = [52.9, 88.6, 89.7, 89.7, 108.1, 107.0, 378.3, 378.5, 388.8, 393.4, 377.3]
    assert column_edges(xs) == [52.9, 377.3]


def test_an_ordinary_indent_step_never_reads_as_a_column():
    """36pt is a paragraph indent; a gutter is hundreds of points."""
    assert column_edges([70.0, 106.0, 142.0]) == [70.0]


def test_the_parent_bullet_is_level_zero_and_its_children_are_level_one():
    blocks = [{"kind": "ListItem", "x0": 52.9, "text": "Continually wrote grants", "text_source": "docling_ocr"},
              {"kind": "ListItem", "x0": 88.6, "text": "$25,000 from the U.S. EPA", "text_source": "docling_ocr"},
              {"kind": "ListItem", "x0": 108.1, "text": "$2,500,000 from the federal", "text_source": "docling_ocr"}]
    assign_nesting(blocks)
    assert [b["list_level"] for b in blocks] == [0, 1, 1]


def test_a_right_column_child_stays_a_child_not_a_grandchild():
    """The case a naive x0 comparison gets wrong."""
    blocks = [{"kind": "ListItem", "x0": 52.9, "text": "Continually wrote grants", "text_source": "docling_ocr"},
              {"kind": "ListItem", "x0": 108.1, "text": "$2,500,000 left column"},
              {"kind": "ListItem", "x0": 378.3, "text": "$270,000 right column"}]
    assign_nesting(blocks)
    assert [b["list_level"] for b in blocks] == [0, 1, 1]


def test_a_ragged_left_edge_does_not_create_spurious_levels():
    """Children of one parent vary 88.6 to 108.1 on the real page -- OCR jitter and
    marker width, not three levels of nesting."""
    blocks = [{"kind": "ListItem", "x0": x, "text": t, "text_source": "docling_ocr"}
              for x, t in ((52.9, "parent"), (88.6, "a"), (89.7, "b"),
                           (108.1, "c"), (107.0, "d"))]
    assign_nesting(blocks)
    assert [b["list_level"] for b in blocks] == [0, 1, 1, 1, 1]


def test_non_list_blocks_reset_the_nesting_context():
    """A heading or paragraph ends the list; the next bullet starts fresh at level 0."""
    blocks = [{"kind": "ListItem", "x0": 52.9, "text": "parent", "text_source": "docling_ocr"},
              {"kind": "ListItem", "x0": 88.6, "text": "child"},
              {"kind": "SectionHeaderItem", "x0": 52.9, "text": "NEW SECTION"},
              {"kind": "ListItem", "x0": 88.6, "text": "first bullet of the new list"}]
    assign_nesting(blocks)
    assert blocks[3]["list_level"] == 0


# ── page furniture: present, but not an assertion ──────────────────────────────────────
# Year 2 carries a per-strategy footer: "1 For more information on activities to support
# Strategy 1, please contact Missy Stults (mstults@a2gov.org)". Azure CU classifies these
# as <!-- PageFooter: ... --> and ocrmac renders them as body prose. The content is worth
# keeping -- it is the staff roster, destined for `people` -- but it is NOT a claim about
# the world, and extracting it as one would fabricate seven assertions per document.
# Detected structurally (bottom margin + repeating across pages), never by keyword.

from pipeline.convert_blocks import mark_furniture


def _fb(page, text, top, page_h=792.0):
    return {"kind": "TextItem", "page_no": page, "text": text,
            "top": top, "page_h": page_h}


def test_a_block_repeating_in_the_bottom_margin_across_pages_is_furniture():
    blocks = [_fb(3, "1 For more information on activities to support Strategy 1, "
                     "please contact Missy Stults", 730),
              _fb(8, "4 For more information on activities to support Strategy 4, "
                     "please contact Simi Barr", 732),
              _fb(11, "6 For more information on activities to support Strategy 6, "
                      "please contact Sean Reynolds", 731)]
    mark_furniture(blocks)
    assert all(b["is_furniture"] for b in blocks)


def test_body_prose_in_the_bottom_margin_is_not_furniture():
    """A single bullet that happens to fall low on one page repeats nowhere."""
    blocks = [_fb(3, "Installed an additional 1.7MW of new residential solar.", 730),
              _fb(8, "Conducted energy audits of 4 City facilities.", 731)]
    mark_furniture(blocks)
    assert not any(b["is_furniture"] for b in blocks)


def test_a_repeated_phrase_in_the_MIDDLE_of_a_page_is_not_furniture():
    """Position matters as much as repetition -- a recurring body sentence is content."""
    blocks = [_fb(3, "For more information on activities to support Strategy 1", 300),
              _fb(8, "For more information on activities to support Strategy 4", 305)]
    mark_furniture(blocks)
    assert not any(b["is_furniture"] for b in blocks)


def test_furniture_needs_more_than_two_occurrences_to_be_a_pattern():
    blocks = [_fb(3, "1 For more information please contact Missy Stults", 730),
              _fb(8, "4 For more information please contact Simi Barr", 731)]
    mark_furniture(blocks, min_occurrences=3)
    assert not any(b["is_furniture"] for b in blocks)


def test_sibling_bullets_in_two_columns_stay_siblings():
    """Real Year 5 page 6: five bullets at x0=54 in the left column, four at x0=282 in
    the right. They are SIBLINGS -- one flat list flowing across the page -- and the
    first version promoted the right column to level 1 because it computed column edges
    across the WHOLE DOCUMENT rather than per page. Columns are a property of a page.
    """
    blocks = ([{"kind": "ListItem", "x0": 54.0, "page_no": 6, "text": f"left {i}"}
               for i in range(5)]
              + [{"kind": "ListItem", "x0": 282.4, "page_no": 6, "text": f"right {i}"}
                 for i in range(4)])
    assign_nesting(blocks)
    assert {b["list_level"] for b in blocks} == {0}


def test_a_narrow_page_does_not_inherit_a_wide_pages_columns():
    """Two pages with different layouts must not share an edge set."""
    blocks = [{"kind": "ListItem", "x0": 54.0, "page_no": 6, "text": "a"},
              {"kind": "ListItem", "x0": 282.4, "page_no": 6, "text": "b"},
              {"kind": "ListItem", "x0": 54.0, "page_no": 7, "text": "c"},
              {"kind": "ListItem", "x0": 90.0, "page_no": 7, "text": "d"}]
    assign_nesting(blocks)
    assert [b["list_level"] for b in blocks] == [0, 0, 0, 1]


def test_a_column_that_enters_deep_can_return_to_the_parent_level():
    """Real Year 2 page 12 right column. It ENTERS at a child indent (the awards
    sub-list continuing from the left column) and later returns to the parent level:

        left  96.6  Sean Reynolds was awarded ...        child
        right 377.3 Julie Roth was awarded ...           child, continued
        right 385.4 Galen Hardy was appointed ...        child
        right 352.0 The proposed SEU won project of ...  BACK to parent
        right 340.5 Working with peer communities ...    parent

    The first version treated the column-entry depth as a FLOOR, so everything after
    stayed at level 1 and four parent-level bullets were rendered as children of an
    awards list they have nothing to do with.
    """
    blocks = [{"kind": "ListItem", "page_no": 12, "x0": 96.6, "text": "Sean Reynolds"},
              {"kind": "ListItem", "page_no": 12, "x0": 377.3, "text": "Julie Roth"},
              {"kind": "ListItem", "page_no": 12, "x0": 385.4, "text": "Galen Hardy"},
              {"kind": "ListItem", "page_no": 12, "x0": 352.0, "text": "The proposed SEU"},
              {"kind": "ListItem", "page_no": 12, "x0": 340.5, "text": "Working with peers"},
              {"kind": "ListItem", "page_no": 12, "x0": 339.4, "text": "Anti-idling"}]
    # a parent exists in the left column above the awards
    blocks.insert(0, {"kind": "ListItem", "page_no": 12, "x0": 60.0, "text": "Awards:"})
    assign_nesting(blocks)
    assert [b["list_level"] for b in blocks] == [0, 1, 1, 1, 0, 0, 0]


def test_the_year_2_grants_all_stay_at_one_level():
    """Twelve siblings across two columns; regression guard for the ladder change."""
    xs = [52.9, 88.6, 89.7, 108.1, 107.0, 378.3, 378.5, 388.8, 393.4, 377.3, 394.6, 395.7]
    blocks = [{"kind": "ListItem", "page_no": 13, "x0": x, "text": f"b{i}", "text_source": "docling_ocr"}
              for i, x in enumerate(xs)]
    assign_nesting(blocks)
    assert blocks[0]["list_level"] == 0
    assert {b["list_level"] for b in blocks[1:]} == {1}


def test_a_recovered_group_of_only_bullet_glyphs_is_discarded():
    """Year 2 rendered six lines reading just "> •" -- stray bullet glyphs the coverage
    sweep recovered because Docling modelled no block for them. A marker with no text is
    not content; it is the punctuation left behind by text that lives elsewhere.
    """
    assert group_uncovered([_w("•", 100, 300)], page_no=5) == []
    assert group_uncovered([_w("•", 100, 300), _w("·", 120, 300)], page_no=5) == []


def test_a_bullet_glyph_WITH_text_is_kept():
    g = group_uncovered([_w("•", 100, 300), _w("Installed", 120, 300),
                         _w("solar", 200, 300)], page_no=5)
    assert g and "Installed" in g[0]["text"]


# ── the inverse of heading promotion: demoting one Docling invented ────────────────────
# Year 2 carries five "DIVE DEEPER into X:" pull-out boxes. Docling typed FOUR as
# TextItem and one -- COMMERCIAL BENCHMARKING -- as a SectionHeaderItem, splitting the
# callout's first line off as a heading and leaving "BUILDINGS: This year, ..." dangling
# as a separate paragraph. That fake `##` broke the strategy section in half and detached
# the bullets below it from their real parent heading.

from pipeline.convert_blocks import body_shapes


def test_a_shape_seen_mostly_on_body_text_is_a_body_shape():
    blocks = [{"kind": "TextItem", "text": "DIVE DEEPER into SOLAR: the city ..."},
              {"kind": "TextItem", "text": "DIVE DEEPER into TREES: since spring ..."},
              {"kind": "TextItem", "text": "DIVE DEEPER into RENTAL: expanding ..."},
              {"kind": "SectionHeaderItem", "text": "DIVE DEEPER into BENCHMARKING"}]
    assert "dive deeper into" in body_shapes(blocks)


def test_a_genuine_heading_shape_is_not_a_body_shape():
    blocks = [{"kind": "SectionHeaderItem", "text": "STRATEGY 1: RENEWABLES"},
              {"kind": "SectionHeaderItem", "text": "STRATEGY 2: ELECTRIFICATION"},
              {"kind": "TextItem", "text": "Strategy 1 of A2ZERO focuses on ..."}]
    assert "strategy #" not in body_shapes(blocks)


def test_one_body_occurrence_is_not_enough_to_demote_a_heading():
    blocks = [{"kind": "TextItem", "text": "CLOSING remarks follow here"},
              {"kind": "SectionHeaderItem", "text": "CLOSING"}]
    assert body_shapes(blocks) == set()


# ── OCR term normalisation ─────────────────────────────────────────────────────────────
# Year 2's central term came back wrong 21 times of 28: AZERO x11, A'ZERO x6, AZZERO x4,
# against A2ZERO x5. The page prints it with a superscript 2 and optical recognition
# guesses. No downstream alias table can repair a token that was never read as itself.
#
# APPLIED ONLY TO OCR-SOURCED TEXT. Where a text layer exists its characters are
# authoritative, and A2ZERO set as A²ZERO is legitimate typography to keep verbatim.
# Correcting a misread restores what the page says; rewriting an extracted character
# would change it.

from pipeline.convert_blocks import normalize_ocr_terms

_TERMS = [{"canonical": "A2ZERO", "variants": ["AZERO", "A'ZERO", "AZZERO"]}]


def test_an_observed_variant_is_corrected_and_reported():
    out, fixes = normalize_ocr_terms("Advancing the AZERO plan citywide.", _TERMS)
    assert out == "Advancing the A2ZERO plan citywide."
    assert fixes == [("AZERO", "A2ZERO")]


def test_every_variant_form_is_handled():
    out, _ = normalize_ocr_terms("AZERO A'ZERO AZZERO", _TERMS)
    assert out == "A2ZERO A2ZERO A2ZERO"


def test_the_canonical_form_is_left_alone_and_not_reported():
    out, fixes = normalize_ocr_terms("The A2ZERO plan.", _TERMS)
    assert out == "The A2ZERO plan." and fixes == []


def test_a_variant_inside_a_longer_word_is_not_touched():
    """Substitution is on whole tokens; 'LAZEROS' is not a mangled A2ZERO."""
    out, fixes = normalize_ocr_terms("LAZEROS and BAZERO4", _TERMS)
    assert out == "LAZEROS and BAZERO4" and fixes == []


def test_case_is_preserved_from_the_canonical_not_the_variant():
    out, _ = normalize_ocr_terms("the azero plan", _TERMS)
    assert "A2ZERO" in out


# ── trimming an over-captured crop to Docling's own boundary ───────────────────────────
# Year 1's bboxes are shifted ~8pt down from the text they describe: the block for
# "Installed approximately 1.3MW ... on upfront costs" is reported at y 172.7-202.7 while
# its lines sit at 164-176 and 181-193, so the box clips its own first line and reaches
# into the NEXT bullet at 197-209. pdfplumber's crop() includes any word that partially
# intersects, so the crop returns three lines where the block has two -- and every bullet
# on the page ended up carrying the opening of the one after it. 1,958 words against the
# healed 1,372, all of it duplication rather than recovery.
#
# Docling's TEXT is right even where its geometry is not: it ends the block exactly at
# "on upfront costs". So the boundary comes from Docling and the characters from
# pdfplumber -- the same split this whole module rests on, applied inside a block.

from pipeline.convert_blocks import trim_to_docling


def test_an_overcaptured_crop_is_trimmed_at_doclings_last_token():
    pdf = ("Installed approximately 1.3MW of rooftop solar on over 200 residential "
           "roofs, saving residents over $650,000 on upfront costs Launched the "
           "Solarize Toolkit, a step-by-step guide to")
    dl = ("Installed approximately 1.3MW of rooftop solar on over 200 residential "
          "roofs, saving residents over $650,000 on upfront costs")
    assert trim_to_docling(pdf, dl).endswith("on upfront costs")
    assert "Solarize Toolkit" not in trim_to_docling(pdf, dl)


def test_pdfplumbers_exact_characters_are_kept_not_doclings():
    """The point of the crop is character fidelity; trimming must not substitute text."""
    pdf = "The A2ZERO plan reached 5.4MW of solar installed. Next bullet begins"
    dl = "The A 2 ZERO plan reached 5.4MW of solar installed."
    out = trim_to_docling(pdf, dl)
    assert "A2ZERO" in out and "A 2 ZERO" not in out


def test_a_crop_that_matches_is_returned_unchanged():
    t = "Helped negotiate agreements on community solar."
    assert trim_to_docling(t, t) == t


def test_a_crop_SHORTER_than_docling_is_left_alone():
    """Under-capture is a different failure and must not be papered over by trimming."""
    pdf = "Installed approximately 1.3MW"
    dl = "Installed approximately 1.3MW of rooftop solar on over 200 residential roofs"
    assert trim_to_docling(pdf, dl) == pdf


def test_an_empty_docling_reading_leaves_the_crop_untouched():
    assert trim_to_docling("some text here", "") == "some text here"


def test_a_repeated_final_token_trims_at_the_right_occurrence():
    """'costs' appearing twice must not trim at the first one."""
    pdf = "reduce costs and lower costs Launched the toolkit"
    dl = "reduce costs and lower costs"
    assert trim_to_docling(pdf, dl) == "reduce costs and lower costs"


def test_a_block_beginning_lowercase_is_a_continuation_not_a_caption():
    """Real Year 3 page 13. Docling split one sentence into two blocks, and the second --
    "and in-person events designed to unlock their potential as planning and
    implementation partners." -- sat beside a photo, ran to 14 words, and was dropped as
    that photo's caption. It took "Plus, the A2ZERO Collaborators network grew to over
    120 organizations!" with it: a citable figure, deleted.

    A caption is a standalone descriptive phrase and begins as one. A block opening with
    a lowercase word is the tail of the sentence above it.
    """
    assert not is_caption_candidate({"kind": "TextItem", "page_no": 13, "text":
        "and in-person events designed to unlock their potential as partners."})


def test_a_normally_capitalised_caption_is_unaffected():
    assert is_caption_candidate({"kind": "TextItem", "page_no": 13,
                                 "text": "Mayor Taylor on his e-bike at the Green Fair."})


def test_a_caption_opening_with_a_number_is_still_a_caption():
    """Year 2's footers open with their footnote digit."""
    assert is_caption_candidate({"kind": "TextItem", "page_no": 9,
                                 "text": "2024 Electrification Expo attendees."})


# ── coverage verified by CONTENT, not geometry ─────────────────────────────────────────
# The geometric sweep asks "does a block's BOX contain this word". Year 3 page 9 shows why
# that is not enough: "the downtown, reducing vehicle/bicyclist conflicts" sits inside a
# block's box, so the sweep skipped it -- but that block's crop never returned those
# words, and the sentence shipped as "Passed a resolution to restrict turns on red lights
# in". Covered by a box and present in the text are different claims, and only the second
# one matters.

from pipeline.convert_blocks import missing_runs


def test_a_run_of_page_words_absent_from_the_assembled_text_is_reported():
    page = "Passed a resolution to restrict turns on red lights in the downtown reducing conflicts".split()
    assembled = "Passed a resolution to restrict turns on red lights in"
    runs = missing_runs(page, assembled)
    assert runs and "downtown" in " ".join(runs[0])


def test_text_fully_present_reports_nothing():
    page = "Installed 1.3MW of rooftop solar".split()
    assert missing_runs(page, "Installed 1.3MW of rooftop solar on many roofs") == []


def test_a_single_stray_word_is_not_a_run():
    """One token differing is OCR jitter or a hyphen decision, not a dropped line."""
    page = "the quick brown fox jumps".split()
    assert missing_runs(page, "the quick brown FOX jumps") == []


def test_runs_shorter_than_the_minimum_are_ignored():
    page = "alpha beta gamma delta".split()
    assert missing_runs(page, "alpha delta", min_run=3) == []


def test_comparison_ignores_case_and_punctuation():
    page = "The Downtown, reducing vehicle/bicyclist conflicts.".split()
    assert missing_runs(page, "the downtown reducing vehicle bicyclist conflicts") == []


def test_a_gap_containing_common_words_is_still_detected():
    """Membership testing breaks on function words: "the downtown, reducing
    vehicle/bicyclist conflicts" was NOT detected, because "the" occurs elsewhere on the
    page and split the run into fragments below the minimum. Presence has to be judged on
    consecutive SEQUENCES, not on whether each word appears somewhere.
    """
    page = ("Passed a resolution to restrict turns on red lights in the downtown "
            "reducing vehicle bicyclist conflicts").split()
    assembled = ("Passed a resolution to restrict turns on red lights in "
                 "and the community passed a millage")
    runs = missing_runs(page, assembled)
    assert runs, "the dropped tail must be found even though 'the' occurs elsewhere"
    assert "downtown" in " ".join(runs[0])


def test_a_reordered_but_present_passage_is_not_reported_missing():
    """Blocks can be emitted in a different order; that is not a loss."""
    page = "alpha beta gamma delta epsilon zeta".split()
    assembled = "delta epsilon zeta alpha beta gamma"
    assert missing_runs(page, assembled) == []


def test_docling_linkage_is_overridden_by_a_lowercase_start_too():
    """Year 3 page 9. DOCLING linked "the downtown, reducing vehicle/bicyclist
    conflicts." as picture 13's caption, so the "Docling's linkage is evidence" branch
    accepted it without ever applying the lowercase check -- and the sentence shipped as
    "Passed a resolution to restrict turns on red lights in".

    A caption is a standalone phrase. A lowercase opening is grammatical evidence that a
    block continues the sentence above it, and that evidence does not become weaker
    because Docling proposed the link rather than geometry.
    """
    from pipeline.convert_blocks import overrides_caption_link
    assert overrides_caption_link(
        {"kind": "TextItem", "page_no": 9,
         "text": "the downtown, reducing vehicle/bicyclist conflicts."}, set())


def test_a_normal_docling_caption_is_not_overridden():
    from pipeline.convert_blocks import overrides_caption_link
    assert not overrides_caption_link(
        {"kind": "TextItem", "page_no": 9,
         "text": "Mayor Taylor on his e-bike at the 2024 Green Fair."}, set())


def test_a_heading_shape_still_overrides_a_docling_link():
    from pipeline.convert_blocks import overrides_caption_link
    assert overrides_caption_link(
        {"kind": "TextItem", "page_no": 17,
         "text": "STRATEGY 6: Enhance the Resilience of Our People"}, {"strategy 6"})


# ── headings Docling split across overlapping boxes ────────────────────────────────────
# Year 1's headings are set as display type over two or three lines, and Docling emits a
# box per fragment -- boxes that OVERLAP and sometimes NEST. Page 3's real heading is
# "Strategy 2: Switch our appliances and vehicles from fossil fuels to electric", and it
# arrives as:
#     box A  x144-504  y100-121  "Switch our appliances and vehicles"
#     box B  x143-414  y 73-142  "Strategy 2: from fossil fuels to electric"
# B CONTAINS A vertically but is narrower, so cropping B alone cuts "vehicles" off at
# x=414 and the rendered heading read "Strategy 2: Switch our appliances and from fossil
# fuels to electric" -- scrambled, not merely split.
#
# Cropping their UNION recovers the heading exactly, verified against the real page.

from pipeline.convert_blocks import merge_overlapping_headings


def _hb(page, x0, x1, top, bot, text, kind="SectionHeaderItem", H=792.0):
    """A block in Docling's BOTTOMLEFT convention, from top-left intent."""
    return {"kind": kind, "page_no": page, "bbox": [x0, H - top, x1, H - bot],
            "coord_origin": "CoordOrigin.BOTTOMLEFT", "page_w": 612.0, "page_h": H,
            "docling_text": text, "self_ref": f"#/t/{text[:6]}", "caption_for": None}


def test_two_overlapping_heading_boxes_merge_into_their_union():
    blocks = [_hb(3, 144, 504, 100, 121, "Switch our appliances and vehicles"),
              _hb(3, 143, 414, 73, 142, "Strategy 2: from fossil fuels to electric")]
    out = merge_overlapping_headings(blocks)
    assert len(out) == 1
    l, t, r, b = out[0]["bbox"]
    assert min(l, r) == 143 and max(l, r) == 504, "union must take the WIDEST extent"


def test_vertically_adjacent_heading_boxes_merge():
    """Page 2: y72-121 then y120-144, same left edge -- one heading, two boxes."""
    blocks = [_hb(2, 144, 509, 72, 121, "Strategy 1: Power our electrical grid with 100%"),
              _hb(2, 144, 335, 120, 144, "renewable energy")]
    assert len(merge_overlapping_headings(blocks)) == 1


def test_headings_far_apart_do_not_merge():
    blocks = [_hb(2, 144, 509, 72, 121, "Strategy 1: Renewables"),
              _hb(2, 143, 235, 400, 420, "In Year One, we:")]
    assert len(merge_overlapping_headings(blocks)) == 2


def test_headings_on_different_pages_never_merge():
    blocks = [_hb(2, 144, 509, 72, 121, "Strategy 1"),
              _hb(3, 144, 509, 73, 122, "Strategy 2")]
    assert len(merge_overlapping_headings(blocks)) == 2


def test_a_heading_and_a_body_block_never_merge():
    """Only headings; merging a bullet into a heading would swallow content."""
    blocks = [_hb(2, 144, 509, 72, 121, "Strategy 1: Renewables"),
              _hb(2, 150, 519, 120, 150, "Installed 1.3MW of solar", kind="ListItem")]
    assert len(merge_overlapping_headings(blocks)) == 2


def test_three_fragments_of_one_heading_all_merge():
    blocks = [_hb(6, 144, 300, 100, 118, "Strategy 7:"),
              _hb(6, 144, 500, 117, 136, "Other strategies / accomplishments"),
              _hb(6, 144, 460, 135, 154, "and next steps")]
    assert len(merge_overlapping_headings(blocks)) == 1


# ── a heading that repeats under every section is a lead-in, not a heading ─────────────
# Year 1 types "In Year One, we:" as a SectionHeaderItem SEVEN times, on pages 2, 3, 4, 4,
# 5, 5 and 6 -- once under each of the seven strategies. It is the line that introduces
# each strategy's bullet list, not a section boundary, and promoting it to `##` cut every
# strategy in half and detached its achievements from the strategy they belong to.
#
# body_shapes() cannot demote this: Docling types it as a heading EVERY time, so the
# document's majority vote agrees with the mistake. The signal is repetition across
# DIFFERENT sections -- a real section heading names one section.

from pipeline.convert_blocks import recurring_lead_ins


def test_a_heading_repeated_under_several_sections_is_a_lead_in():
    blocks = [{"kind": "SectionHeaderItem", "text": "Strategy 1: Renewables"},
              {"kind": "SectionHeaderItem", "text": "In Year One, we:"},
              {"kind": "SectionHeaderItem", "text": "Strategy 2: Electrification"},
              {"kind": "SectionHeaderItem", "text": "In Year One, we:"},
              {"kind": "SectionHeaderItem", "text": "Strategy 3: Efficiency"},
              {"kind": "SectionHeaderItem", "text": "In Year One, we:"}]
    assert "in year one, we:" in recurring_lead_ins(blocks)


def test_a_unique_heading_is_never_a_lead_in():
    blocks = [{"kind": "SectionHeaderItem", "text": "Strategy 1: Renewables"},
              {"kind": "SectionHeaderItem", "text": "CLOSING"}]
    assert recurring_lead_ins(blocks) == set()


def test_a_heading_repeated_CONSECUTIVELY_is_a_continuation_not_a_lead_in():
    """"GREENHOUSE GAS EMISSIONS SUMMARY" twice in a row on Year 5 is one section
    resuming on a new page -- already handled by the continuation merge, and it must not
    be demoted to body text."""
    blocks = [{"kind": "SectionHeaderItem", "text": "GHG EMISSIONS SUMMARY"},
              {"kind": "SectionHeaderItem", "text": "GHG EMISSIONS SUMMARY"},
              {"kind": "SectionHeaderItem", "text": "GHG EMISSIONS SUMMARY"}]
    assert recurring_lead_ins(blocks) == set()


def test_two_occurrences_are_not_yet_a_pattern():
    blocks = [{"kind": "SectionHeaderItem", "text": "Strategy 1"},
              {"kind": "SectionHeaderItem", "text": "In Year One, we:"},
              {"kind": "SectionHeaderItem", "text": "Strategy 2"},
              {"kind": "SectionHeaderItem", "text": "In Year One, we:"}]
    assert recurring_lead_ins(blocks, min_sections=3) == set()


from pipeline.convert_blocks import already_present


def test_a_geometric_stray_already_present_in_the_text_is_not_re_added():
    """Year 1 page 6: the geometric sweep called "organizations as collaborators"
    uncovered, because the words' CENTRES fall outside a box that sits ~8pt below its own
    text -- while pdfplumber's crop(), which keeps any word INTERSECTING the box, had
    already captured them into "- Secured 92 organizations as collaborators".

    The two sweeps ask different questions and the geometric one is the weaker: being
    outside a box is not the same as being absent from the document. Re-adding produced a
    duplicate stub three lines later, and the same fault duplicated two other bullets and
    an eighth "In Year One, we:".
    """
    assert already_present("organizations as collaborators",
                           "- Secured 92 organizations as collaborators on our work")


def test_a_genuinely_absent_stray_is_still_recovered():
    assert not already_present("3 INTRODUCTION 4 GREENHOUSE GAS",
                               "Some entirely unrelated body prose about solar.")


def test_presence_ignores_case_and_punctuation():
    assert already_present("Commercial Enterprises.",
                           "- Expansion of Solarize to commercial enterprises")


def test_a_heading_ending_in_a_colon_merges_with_the_next_heading():
    """Year 3 splits its strategy headings as a LABEL and a TITLE separated by ~50pt of
    page -- "STRATEGY ONE:" at y56-71 and "POWER OUR ELECTRICAL GRID WITH 100% RENEWABLE
    ENERGY" at y121-134. Fifty points is far too wide to merge on proximity without
    risking two genuinely separate headings, but a heading ending in a colon is not a
    section name; it is the first half of one.
    """
    blocks = [_hb(3, 121, 252, 56, 71, "STRATEGY ONE:"),
              _hb(3, 100, 517, 121, 134, "POWER OUR ELECTRICAL GRID WITH 100% RENEWABLE ENERGY")]
    out = merge_overlapping_headings(blocks)
    assert len(out) == 1
    assert out[0]["docling_text"].startswith("STRATEGY ONE: POWER OUR")


def test_a_colon_heading_does_not_merge_across_a_page_break():
    blocks = [_hb(3, 121, 252, 56, 71, "STRATEGY ONE:"),
              _hb(4, 100, 517, 121, 134, "SOMETHING ELSE ENTIRELY")]
    assert len(merge_overlapping_headings(blocks)) == 2


def test_a_colon_heading_does_not_merge_when_body_text_intervenes():
    """Adjacency in the block stream is the guard: if a paragraph sits between them they
    are two sections, whatever the punctuation."""
    blocks = [_hb(3, 121, 252, 56, 71, "STRATEGY ONE:"),
              _hb(3, 100, 517, 90, 110, "Body prose here.", kind="TextItem"),
              _hb(3, 100, 517, 121, 134, "A REAL SECOND HEADING")]
    assert len([b for b in merge_overlapping_headings(blocks)
                if b["kind"] == "SectionHeaderItem"]) == 2


def test_a_normal_heading_is_not_merged_with_the_next_one():
    blocks = [_hb(3, 121, 400, 56, 71, "GREENHOUSE GAS EMISSIONS SUMMARY"),
              _hb(3, 100, 517, 300, 320, "CLOSING")]
    assert len(merge_overlapping_headings(blocks)) == 2


def test_a_leading_token_docling_does_not_have_is_trimmed_from_the_front():
    """Year 3 pages 7 and 9. Merging the label and title boxes widened the crop enough to
    catch the decorative strategy numeral set beside the heading, so the rendered heading
    read "3 STRATEGY THREE: SIGNIFICANTLY IMPROVE...". Docling's own text starts at
    "STRATEGY", which is the boundary -- the same rule as the tail trim, at the other end.
    """
    pdf = "3 STRATEGY THREE: SIGNIFICANTLY IMPROVE THE ENERGY EFFICIENCY"
    dl = "STRATEGY THREE: SIGNIFICANTLY IMPROVE THE ENERGY EFFICIENCY"
    assert trim_to_docling(pdf, dl) == dl


def test_the_front_trim_keeps_pdfplumbers_characters():
    pdf = "3 STRATEGY: powering A2ZERO forward"
    dl = "STRATEGY: powering A 2 ZERO forward"
    out = trim_to_docling(pdf, dl)
    assert out.startswith("STRATEGY:") and "A2ZERO" in out


def test_a_matching_front_is_left_alone():
    t = "STRATEGY THREE: efficiency"
    assert trim_to_docling(t, t) == t


def test_the_front_trim_never_removes_more_than_a_couple_of_tokens():
    """A wholesale mismatch means something else is wrong; silently deleting the opening
    of a block would be the very failure this module keeps finding."""
    pdf = "one two three four five STRATEGY THREE: efficiency"
    dl = "STRATEGY THREE: efficiency"
    assert trim_to_docling(pdf, dl).startswith("one two")


def test_the_front_trim_never_removes_a_token_docling_has_elsewhere():
    """The regression this pins, caught on Year 1 immediately after the front trim landed.
    Merging that document's split heading boxes concatenates their text in BLOCK order,
    which is not reading order -- Docling's merged text reads "Switch our appliances and
    vehicles Strategy 2: from fossil fuels to electric". Aligning to its first token
    ("Switch") stripped "Strategy 2:" off the front of a correct heading.

    A leading token Docling has SOMEWHERE is a token Docling read; it is only the ordering
    that differs, and ordering is not evidence of over-capture.
    """
    pdf = "Strategy 2: Switch our appliances and vehicles from fossil fuels to electric"
    dl = "Switch our appliances and vehicles Strategy 2: from fossil fuels to electric"
    assert trim_to_docling(pdf, dl).startswith("Strategy 2:")


def test_a_leading_token_docling_lacks_entirely_is_still_trimmed():
    """Year 3's decorative numeral: "3" appears nowhere in Docling's reading."""
    pdf = "3 STRATEGY THREE: SIGNIFICANTLY IMPROVE THE ENERGY EFFICIENCY"
    dl = "STRATEGY THREE: SIGNIFICANTLY IMPROVE THE ENERGY EFFICIENCY"
    assert trim_to_docling(pdf, dl) == dl


def test_an_empty_pdfplumber_crop_does_not_crash_the_trim():
    """Year 2 is image-based: most crops return nothing, and the OCR fallback supplies
    the text afterwards. Reordering the trims removed a length guard that had been
    protecting pw[0] by accident, and the whole document crashed on IndexError.
    """
    assert trim_to_docling("", "A2ZERO is Ann Arbor's plan") == ""
    assert trim_to_docling("   ", "A2ZERO is Ann Arbor's plan") == "   "


def _char(text, x0, x1, top, size, doctop=None):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + size,
            "doctop": doctop if doctop is not None else top, "y0": 0.0, "y1": 0.0,
            "size": size}


def test_a_superscript_is_snapped_onto_its_own_line():
    """Year 1 draws the 2 of A2ZERO in a separate text pass, raised ~4pt -- outside
    pdfplumber's y_tolerance, so it became a phantom line of its own and landed many
    words from where it belongs."""
    line = [_char("A", 288, 297, 489, 12), _char(" ", 297, 300, 489, 12),
            _char("Z", 300, 307, 489, 12), _char("O", 307, 314, 489, 12)]
    sup = _char("2", 295.5, 301.0, 485.4, 10)          # raised 3.6pt, sits on the space
    fixed, moved = snap_scripts([*line, sup])
    assert moved == 1
    two = [c for c in fixed if c["text"] == "2"][0]
    assert two["top"] == 489 and two["bottom"] == 501   # now on its line's band
    assert not [c for c in fixed if c["text"].isspace()]  # the room it was given is gone


def test_a_body_glyph_is_never_snapped():
    line = [_char("A", 288, 297, 489, 12), _char(" ", 297, 300, 489, 12),
            _char("Z", 300, 307, 489, 12)]
    fixed, moved = snap_scripts(line)
    assert moved == 0 and fixed == line          # inert where there is no script glyph


def test_a_trailing_footnote_marker_keeps_the_space_after_it():
    """A marker sitting AFTER a word must not weld it to the next one. Only a glyph
    covering MORE THAN HALF a space is sitting on it; a marker that merely touches the
    space is punctuation, and "plan1was" would be a worse defect than "A2 ZERO"."""
    line = [_char("p", 100, 107, 489, 12), _char(" ", 107, 110, 489, 12),
            _char("w", 110, 118, 489, 12)]
    sup = _char("1", 106.0, 108.0, 485.4, 8)   # covers a third of the space, not half
    fixed, moved = snap_scripts([*line, sup])
    assert moved == 1
    assert [c for c in fixed if c["text"].isspace()]


def test_an_indent_of_18pt_nests_at_12pt_type():
    """Year 1 page 6: eleven children of 'through the following avenues:' indented 18.4pt
    while their siblings jitter by 1.2. A fixed 24pt step missed every one."""
    blocks = ([{"kind": "ListItem", "x0": 150.7, "page_no": 6} for _ in range(3)]
              + [{"kind": "ListItem", "x0": 170.3, "page_no": 6} for _ in range(4)])
    assign_nesting(blocks, {6: 12.0})
    assert [b["list_level"] for b in blocks] == [0, 0, 0, 1, 1, 1, 1]


def test_jitter_below_one_em_does_not_nest():
    """Year 3 page 15's siblings scatter over 5.8pt and Year 2 page 13's over 10.3 --
    both under 1.25 em, both genuinely one level."""
    for em, wobble in ((12.0, 5.8), (10.0, 10.3)):
        blocks = ([{"kind": "ListItem", "x0": 300.0, "page_no": 1} for _ in range(2)]
                  + [{"kind": "ListItem", "x0": 300.0 + wobble, "page_no": 1}])
        assign_nesting(blocks, {1: em})
        assert {b["list_level"] for b in blocks} == {0}, (em, wobble)


def test_a_space_one_line_away_from_a_script_is_not_room():
    """The room a glyph was given is a space it brackets on its OWN line. Matching on the
    x-range alone also caught spaces directly above and below, which joined Year 3's
    "turns three" into "turnsthree" and Year 5's "slated for" into "slatedfor"."""
    below = [_char("s", 292, 299, 501, 12), _char(" ", 299, 302, 501, 12),
             _char("t", 302, 309, 501, 12)]
    line = [_char("A", 288, 297, 489, 12), _char(" ", 297, 300, 489, 12),
            _char("Z", 300, 307, 489, 12)]
    sup = _char("2", 295.5, 301.0, 485.4, 10)
    fixed, moved = snap_scripts([*line, sup, *below])
    assert moved == 1
    kept = [c for c in fixed if c["text"].isspace()]
    assert len(kept) == 1 and kept[0]["top"] == 501     # only the far line's space survives


def test_a_doubled_space_under_one_glyph_goes_entirely():
    """Two of Year 1's nine occurrences are set with TWO space chars between A and ZERO.
    Dropping only the fully covered one still left "A2 ZERO"."""
    line = [_char("A", 268.4, 277.1, 489, 12), _char(" ", 277.1, 280.3, 489, 12),
            _char(" ", 280.3, 283.4, 489, 12), _char("Z", 283.4, 290.7, 489, 12)]
    sup = _char("2", 276.8, 282.3, 485.4, 10)   # covers 100% of one, 64.7% of the next
    fixed, moved = snap_scripts([*line, sup])
    assert moved == 1
    assert not [c for c in fixed if c["text"].isspace()]


def test_the_trim_preserves_the_newlines_de_hyphenation_reads():
    """Both trims rebuilt the block with " ".join(tokens), flattening every newline --
    and apply_hyphen_decisions runs on the very next line of convert(), needing exactly
    those newlines to tell "zero-\\nemissions" from a real "zero- emissions"."""
    raw = "a study of zero-\nemissions transit options today"
    kept = trim_to_docling(raw, "a study of zero emissions transit options")
    assert "zero-\nemissions" in kept          # the split survives the trim
    assert kept.endswith("options")            # and the tail was still trimmed


def test_the_front_trim_also_preserves_newlines():
    raw = "3 STRATEGY THREE: improve the energy-\nefficiency of buildings"
    kept = trim_to_docling(raw, "STRATEGY THREE: improve the energy efficiency of buildings")
    assert kept.startswith("STRATEGY") and "energy-\nefficiency" in kept


def _hblk(kind, text, l, t, b, page=6):
    return {"kind": kind, "docling_text": text, "page_no": page, "bbox": [l, t, l + 200, b]}


def test_a_body_shaped_heading_glued_to_its_paragraph_is_absorbed():
    """Year 2 page 6: Docling split one DIVE DEEPER callout across two blocks and typed
    the first line as a heading, leaving the label's colon in the block below."""
    blocks = [_hblk("TextItem", "DIVE DEEPER into GREEN RENTAL HOUSING: prose here", 75.9, 400, 380),
              _hblk("TextItem", "DIVE DEEPER into TREES: more prose here", 75.9, 370, 350),
              _hblk("SectionHeaderItem", "DIVE DEEPER into COMMERCIAL BENCHMARKING FOR ANN ARBOR",
                    75.9, 363.8, 341.9),
              _hblk("TextItem", "BUILDINGS: This year, the ordinance passed.", 75.9, 337.3, 162.3)]
    out = absorb_body_shaped_headings(blocks, {6: 10.0})
    assert len(out) == 3
    assert out[-1]["kind"] == "TextItem"
    assert out[-1]["docling_text"].startswith("DIVE DEEPER into COMMERCIAL")
    assert "BUILDINGS: This year" in out[-1]["docling_text"]


def test_a_real_heading_close_to_its_body_is_not_absorbed():
    """Year 1's "Next Steps" sits 5.4pt above its paragraph -- inside the geometric
    window -- but its shape is nothing the document uses for body text."""
    blocks = [_hblk("TextItem", "Some ordinary paragraph of prose text here", 150, 400, 380, 7),
              _hblk("SectionHeaderItem", "Next Steps", 150, 363.8, 341.9, 7),
              _hblk("TextItem", "Looking ahead to Year Two we plan to do more.", 150, 337.3, 162.3, 7)]
    out = absorb_body_shaped_headings(blocks, {7: 12.0})
    assert len(out) == 3 and out[1]["kind"] == "SectionHeaderItem"


def test_a_run_of_bare_bullet_glyphs_is_not_recovered_text():
    """Year 2 pages 12-13 emitted "> o o o o o" and "> o o" -- the markers of a sub-list
    whose words Docling had already claimed."""
    from pipeline.convert_blocks import group_uncovered
    marks = [{"text": "o", "top": 100.0, "bottom": 110.0, "x0": 50.0 + 12 * i, "x1": 56.0 + 12 * i}
             for i in range(5)]
    assert group_uncovered(marks, 12) == []


def test_a_marker_beside_real_words_still_travels():
    """Only a run that is ENTIRELY markers is dropped; with words present the words are
    the evidence and the marker rides along."""
    from pipeline.convert_blocks import group_uncovered
    ws = [{"text": t, "top": 100.0, "bottom": 110.0, "x0": 50.0 + 12 * i, "x1": 56.0 + 12 * i}
          for i, t in enumerate(["o", "Missy", "Stults"])]
    assert group_uncovered(ws, 12)


def test_the_stated_coverage_period_is_parsed_with_its_length():
    assert coverage_period("June 1, 2024 – May 31, 2025")["days"] == 364
    assert coverage_period("JULY 1, 2023 - JUNE 3, 2024")["days"] == 338   # source typo
    assert coverage_period("2021 - 2022 Annual Report") is None           # year-only


def test_the_period_keeps_the_verbatim_string_it_came_from():
    """The interpretation is a curator's ruling; what the document SAYS is not."""
    p = coverage_period("A2ZERO YEAR FOUR ANNUAL REPORT JULY 1, 2023 - JUNE 3, 2024")
    assert p["text"] == "JULY 1, 2023 - JUNE 3, 2024" and p["end"] == "2024-06-03"


def test_both_sweeps_drop_a_bare_marker_run():
    """Year 2's "o o o o o" survived a fix to the geometric sweep because it arrives
    through the CONTENT sweep: pdfplumber reads the glyphs, Docling's assembled text
    never contains them, so they register as missing content."""
    from pipeline.convert_blocks import is_marker_run
    assert is_marker_run(["o", "o", "o", "o", "o"])
    assert is_marker_run(["•", "-", "o"])
    assert not is_marker_run(["o", "Missy", "Stults"])
    assert not is_marker_run([])                 # nothing is not a marker run
