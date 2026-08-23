"""Glyphs drawn twice at the same place, and glyphs drawn sideways.

MEASURED ON THE CAP. Its ACTION pages carry cost/GHG infographics whose headings are
overprinted -- every character stamped twice at IDENTICAL coordinates, which is how a PDF
fakes bold or draws fill-then-stroke. pdfplumber faithfully returns both copies, so the text
layer reads "CCoosstt OOvveerr 1100 YYeeaarrss" and the conversion gate refuses the whole
138-page document as garbled.

Two glyphs cannot occupy one position and both be content. Rendering them once is what a
reader sees, so this is not a heuristic -- it is undoing a drawing instruction.
"""
from __future__ import annotations

from pipeline.convert_blocks import dedupe_overprint


def _c(text, x0, top, size=12.0, font="F"):
    return {"text": text, "x0": x0, "x1": x0 + 6, "top": top, "bottom": top + size,
            "size": size, "fontname": font, "upright": True}


def test_a_glyph_stamped_twice_at_one_position_is_kept_once():
    chars = [_c("C", 100, 128), _c("C", 100, 128), _c("o", 111, 128), _c("o", 111, 128)]
    assert "".join(c["text"] for c in dedupe_overprint(chars)) == "Co"


def test_two_different_letters_at_one_position_are_both_kept():
    """Not a duplicate: different glyphs overlapping is a layout artefact, not overprint,
    and dropping one would delete a character the page really draws."""
    chars = [_c("C", 100, 128), _c("O", 100, 128)]
    assert len(dedupe_overprint(chars)) == 2


def test_the_same_letter_at_a_different_position_is_kept():
    """'oo' in 'book' is two real glyphs."""
    chars = [_c("o", 100, 128), _c("o", 107, 128)]
    assert len(dedupe_overprint(chars)) == 2


def test_the_same_letter_at_a_different_size_is_kept():
    """A drop shadow or a layered heading may repeat a glyph at another size; that is a
    design choice the page makes and not a doubled stamp."""
    chars = [_c("C", 100, 128, size=12.0), _c("C", 100, 128, size=16.0)]
    assert len(dedupe_overprint(chars)) == 2


def test_sub_point_jitter_still_counts_as_the_same_stamp():
    """Float noise in the content stream must not defeat the match."""
    chars = [_c("C", 100.0, 128.0), _c("C", 100.004, 128.003)]
    assert len(dedupe_overprint(chars)) == 1


def test_order_is_preserved():
    chars = [_c("a", 10, 5), _c("b", 20, 5), _c("b", 20, 5), _c("c", 30, 5)]
    assert "".join(c["text"] for c in dedupe_overprint(chars)) == "abc"


def test_an_empty_page_is_safe():
    assert dedupe_overprint([]) == []


# --- chart scaffolding is not prose -------------------------------------------------------

from pipeline.convert_blocks import is_data_graphic


def _pic(label, conf=0.9):
    return {"kind": "PictureItem", "top_label": label, "top_conf": conf,
            "page_no": 1, "bbox": (0, 0, 100, 100)}


def test_charts_are_data_graphics():
    """MEASURED ON THE CAP. Its charts carry leftover spreadsheet legends -- "1st Qtr 2nd Qtr
    3rd Qtr 4th Qtr" -- invisible on the page and present in the text layer. The coverage
    sweep found them uncovered by any text block and recovered them into prose as
    '3crdaQtrrbo4tnhQ', which is what refused the document.

    A chart's internal text is scaffolding. The vision path reads charts properly."""
    for label in ("bar_chart", "pie_chart", "line_chart", "flow_chart"):
        assert is_data_graphic(_pic(label)), label


def test_a_photograph_is_not_a_data_graphic():
    """THE CASE THE EXCLUSION EXISTS FOR: a heading printed over a photograph is real content
    and must still be recovered. Suppressing all pictures would lose it."""
    assert not is_data_graphic(_pic("photograph"))
    assert not is_data_graphic(_pic("full_page_image"))


def test_icons_and_unknowns_stay_recoverable():
    """Conservative by default: only a positive chart classification suppresses text."""
    for label in ("icon", "logo", "other", "geographical_map", None):
        assert not is_data_graphic(_pic(label)), label


def test_a_low_confidence_chart_does_not_suppress_text():
    """Below the floor the classifier is guessing, and the cost of being wrong is asymmetric:
    a suppressed heading is gone silently, while a recovered chart legend is loud."""
    assert not is_data_graphic(_pic("bar_chart", conf=0.2))


def test_the_measured_low_end_still_counts():
    """The CAP's page 121 bar chart classifies at 0.552 and does carry legend junk."""
    assert is_data_graphic(_pic("bar_chart", conf=0.552))


def test_a_non_picture_block_is_never_a_data_graphic():
    assert not is_data_graphic({"kind": "TextItem", "top_label": "bar_chart"})


# --- an invisible underlay is not the document's text -------------------------------------

from pipeline.convert_blocks import drop_placed_underlay


def _tagged(text, tag, font="SofiaProRegular"):
    return {"text": text, "tag": tag, "fontname": font, "x0": 0, "x1": 5,
            "top": 0, "bottom": 10, "size": 10.0, "upright": True}


def test_placed_underlay_text_is_dropped():
    """MEASURED ON THE CAP. It carries an invisible underlay -- 2,613 characters in Calibri
    across 41 pages, marked PlacedPDF -- holding an EARLIER DRAFT of the visible text. The
    rendered page reads "A²ZERO strives toward one unifying vision"; the underlay reads "The
    A2Zero initiative strives toward one unifying vision". Docling's block bbox spans both,
    so the crop interleaved them into "AA²2ZZeEroRVOisioVnISION" and the gate refused the
    document.

    Verified by rendering page 14: the underlay appears nowhere on it."""
    chars = [_tagged("A", "Span"), _tagged("T", "PlacedPDF", "Calibri"),
             _tagged("B", "Span")]
    assert "".join(c["text"] for c in drop_placed_underlay(chars)) == "AB"


def test_artifact_tagged_text_is_kept():
    """THE ONE THAT WOULD HAVE BEEN EASY TO GET WRONG. `Artifact` is the standard PDF tag for
    non-content, so dropping it looks right -- but in this document its 302 characters are
    SofiaPro page furniture that IS rendered. Only the placed underlay is invisible."""
    chars = [_tagged("A", "Span"), _tagged("B", "Artifact")]
    assert len(drop_placed_underlay(chars)) == 2


def test_untagged_text_is_kept():
    """6,401 characters carry no tag at all and are ordinary visible prose."""
    assert len(drop_placed_underlay([_tagged("A", None)])) == 1


def test_a_document_with_no_underlay_is_untouched():
    chars = [_tagged(c, "Span") for c in "hello"]
    assert len(drop_placed_underlay(chars)) == 5


# --- a shadow stamp is offset, not identical ---------------------------------------------

def _w(text, x0, top, width=16.0, size=30.0, font="F"):
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + size,
            "size": size, "fontname": font, "upright": True}


def test_a_glyph_restamped_at_a_small_offset_is_a_shadow():
    """MEASURED ON CAP PAGE 117. The cost figures are drawn twice at a CONSTANT 3.134pt
    offset -- a drop shadow, or fake bold by offset stamping -- so "$1,016,000" reads
    "$$11,,016000,,000000". Exact-position dedupe does not catch it because the positions
    differ.

    At size 30 a glyph advance is about 16pt, so 3.134pt is a fifth of one character. Two
    real glyphs never overlap that far."""
    chars = [_w("$", 95.105, 282), _w("$", 98.239, 282),
             _w("1", 111.335, 282), _w("1", 114.469, 282)]
    assert "".join(c["text"] for c in dedupe_overprint(chars)) == "$1"


def test_two_real_repeated_letters_are_kept():
    """The 'oo' in 'book' sits a full glyph apart and is two characters."""
    chars = [_w("o", 100, 50, width=8.0, size=12.0),
             _w("o", 108, 50, width=8.0, size=12.0)]
    assert len(dedupe_overprint(chars)) == 2


def test_the_threshold_scales_with_the_glyph():
    """A 3pt offset is a shadow at size 30 and a real gap in small type."""
    big = [_w("A", 0, 0, width=16.0, size=30.0), _w("A", 3.1, 0, width=16.0, size=30.0)]
    small = [_w("A", 0, 0, width=5.0, size=9.0), _w("A", 3.1, 0, width=5.0, size=9.0)]
    assert len(dedupe_overprint(big)) == 1
    assert len(dedupe_overprint(small)) == 2


def test_a_shadow_on_another_line_is_not_matched():
    """Same letter, same offset, different baseline: two lines of a poem, not a shadow."""
    chars = [_w("A", 100, 50), _w("A", 103, 90)]
    assert len(dedupe_overprint(chars)) == 2


def test_the_earlier_stamp_is_the_one_kept():
    """Order matters for reading order: keep the first, drop the restamp."""
    chars = [_w("X", 10.0, 5), _w("X", 13.0, 5)]
    kept = dedupe_overprint(chars)
    assert len(kept) == 1 and kept[0]["x0"] == 10.0


# --- a garbled run from inside a picture is not prose -------------------------------------

from pipeline.convert_blocks import looks_mis_decoded


def test_repeated_digit_pairs_are_mis_decoded():
    """MEASURED. CAP page 117 RENDERS "$1,000,000" -- confirmed by cropping the page -- while
    its glyphs decode as "$$11,,016000,,000000". The subset font's ToUnicode map is wrong, so
    the characters are not merely doubled: some are the wrong characters. Dedupe cannot fix
    that, and no amount of geometry will."""
    assert looks_mis_decoded("$11,,016000,,000000")
    assert looks_mis_decoded("$13,10600,,000000")
    assert looks_mis_decoded("$$11,,11600,,000000")


def test_real_money_is_not_mis_decoded():
    for good in ("$1,000,000", "$9,440,000", "$75,000", "$3,245,000", "13.6%"):
        assert not looks_mis_decoded(good), good


def test_the_cap_cost_notation_is_not_mis_decoded():
    """'$$; EQU; SCALE' is real CAP content -- a cost-rating notation in the action tables --
    and must survive."""
    assert not looks_mis_decoded("$$; EQU; SCALE")
    assert not looks_mis_decoded("$$; SCALE LOCAL; NRG")


def test_ordinary_prose_is_never_flagged():
    assert not looks_mis_decoded("Reduce the miles we travel in our vehicles by at least 50%")
    assert not looks_mis_decoded("")


# --- sideways text is never prose ---------------------------------------------------------

from pipeline.convert_blocks import upright_only


def test_rotated_characters_are_not_prose():
    """MEASURED ON THE CAP. Its chart axis labels are set vertically, and linearising them
    by x-position reads them backwards: ")e²OCTM( laitnetoP noitcudeR snoissimE GHG", which
    reverses to "GHG Emissions Reduction Potential (MTCO²e)". Four pages raised a truncation
    warning for it, because the reversed run ends on a dangling "By".

    866 rotated characters in the document, 842 of them inside a picture. The 24 outside are
    a sideways sidebar label on page 13 that reaches the output nowhere, so excluding all of
    them loses nothing. Text a reader turns their head for is design, not prose."""
    chars = [{"text": "A", "upright": True}, {"text": "B", "upright": False},
             {"text": "C", "upright": True}]
    assert "".join(c["text"] for c in upright_only(chars)) == "AC"


def test_characters_without_the_flag_are_kept():
    """Absence of `upright` is not evidence of rotation."""
    assert len(upright_only([{"text": "A"}, {"text": "B"}])) == 2


def test_an_all_upright_page_is_untouched():
    chars = [{"text": c, "upright": True} for c in "hello"]
    assert len(upright_only(chars)) == 5
