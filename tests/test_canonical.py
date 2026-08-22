"""The coordinate space every stored span is an offset into.

Getting this wrong is not a bug anyone notices: a span still round-trips perfectly against
the text it was written from, and points at the wrong words in the text you now have.
"""
import re

from pipeline.canonical import BLOCK_SEP, build, parse, sections

MD = """# YEAR THREE ANNUAL REPORT

<!-- gate: PASS -->
<!-- generated 2026-08-21T00:00:00Z -- structure from Docling -->
<!-- run: a title from the command line -->
<!-- COVERAGE PERIOD: July 1, 2022-June 3, 2023 -> 2022-07-01..2023-06-03 (337 days) -->

<!-- p.1 -->
July 1, 2022-June 3, 2023

The Ann Arbor Office of Sustainability and Innovations Team

## INTRODUCTION

<!-- p.2 -->
As A2ZERO turns three, we reflect on actions taken.

**Figure (pie_chart, page 2):** Waste, 2% Electricity, 41%

<figure_description>
  <relevance>substantive</relevance>
  <point label="Electricity" value="41" unit="percent"/>
</figure_description>

- Planted 10,000 trees.
  - A nested achievement.

<!-- FURNITURE: page footer, not an assertion -->
> 1 For more information contact someone@a2gov.org

<!-- CAPTION: describes a photograph on page 2 that was not retained -->
> Solar array installed at Gallup Park.

<!-- recovered by coverage sweep: no Docling block modelled this region on page 2 -->
> a fragment whose placement was inferred

<!-- ORNAMENTAL FIGURE: screenshot_from_computer on page 6 was examined -->

<!-- p.3 -->
> [OCR] a block read by OCR where the document is a text layer
"""


def test_every_unit_round_trips():
    c = build(MD)
    for u in c.units:
        if u.is_prose:
            assert c.slice(u.char_start, u.char_end) == u.text, u.text


def test_offsets_are_assigned_by_construction_not_by_searching():
    """Two identical bullets on one page would both FIND the first occurrence, and the
    second claim would cite the first bullet while round-tripping perfectly."""
    md = "# t\n\n- Planted 10,000 trees.\n\n- Planted 10,000 trees.\n"
    c = build(md)
    bullets = [u for u in c.units if u.kind == "list_item"]
    assert len(bullets) == 2
    assert bullets[0].char_start != bullets[1].char_start
    assert c.slice(*(bullets[1].char_start, bullets[1].char_end)) == "Planted 10,000 trees."


def test_the_hash_ignores_everything_that_changes_between_runs():
    base = build(MD).content_hash
    for swap in ((r"generated \d{4}-\d\d-\d\dT[\d:]+Z", "generated 2099-01-01T00:00:00Z"),
                 (r"<!-- gate: \w+ -->", "<!-- gate: REVIEW -->"),
                 (r"<!-- run: [^>]*-->", "<!-- run: anything at all -->")):
        assert build(re.sub(swap[0], swap[1], MD)).content_hash == base, swap[0]


def test_the_hash_changes_when_a_word_does():
    assert build(MD.replace("Planted", "Planting")).content_hash != build(MD).content_hash


def test_tags_become_fields_rather_than_characters():
    """FURNITURE, CAPTION and the sweep's warning are how the renderer tells ingest what it
    decided -- tagging is what was bought instead of deleting, so it must survive as
    structure rather than as prose."""
    c = build(MD)
    kinds = {u.kind for u in c.units}
    assert {"title", "heading", "para", "list_item", "caption", "furniture"} <= kinds
    furn = next(u for u in c.units if u.kind == "furniture")
    assert furn.flags["is_furniture"] and "FURNITURE" not in c.text
    cap = next(u for u in c.units if u.kind == "caption" and "Gallup" in u.text)
    assert cap.flags["is_caption"] and "CAPTION" not in c.text
    swept = next(u for u in c.units if "placement was inferred" in u.text)
    assert swept.flags["placement_inferred"]


def test_provenance_is_a_flag_not_a_prefix():
    c = build(MD)
    u = next(u for u in c.units if "read by OCR" in u.text)
    assert u.flags["text_source"] == "docling_ocr"
    assert "[OCR]" not in c.text


def test_figure_xml_is_not_document_text():
    """Vision output belongs in document_figures.raw_xml, not in the span space."""
    c = build(MD)
    assert "<relevance>" not in c.text and "figure_description" not in c.text
    assert any(u.kind == "figure" and u.flags["figure_state"] == "ornamental"
               for u in c.units)


def test_pages_and_list_levels_survive():
    c = build(MD)
    assert next(u for u in c.units if "turns three" in u.text).page_no == 2
    assert next(u for u in c.units if "nested achievement" in u.text).level == 1


def test_front_matter_is_its_own_section():
    """Everything before the first heading holds the title, the period and the sign-off --
    attaching it to INTRODUCTION would misattribute all three."""
    secs = sections(build(MD))
    assert secs[0]["heading"] is None
    assert secs[1]["heading"] == "INTRODUCTION"
    assert secs[0]["char_end"] <= secs[1]["char_start"]


def test_section_ranges_are_contiguous_and_hashed():
    c = build(MD)
    for s in sections(c):
        assert 0 <= s["char_start"] < s["char_end"] <= len(c.text)
        assert len(s["content_hash"]) == 64


def test_a_document_with_no_headings_is_one_section():
    c = build("# t\n\n- only a bullet.\n")
    assert len(sections(c)) == 1


def test_the_real_corpus_round_trips():
    """The five converted reports, every unit, every section. This is the claim the store
    rests on and it is cheap to check, so it is checked against real documents rather than
    only against a fixture."""
    import pathlib
    mds = sorted(pathlib.Path("processing").glob("a2zero-year*/orchestrated/*-reviewed.md"))
    if not mds:
        import pytest
        pytest.skip("converted corpus not present")
    assert len(mds) == 5
    for p in mds:
        c = build(p.read_text())
        assert c.units and c.text
        for u in c.units:
            if u.is_prose:
                assert c.slice(u.char_start, u.char_end) == u.text, f"{p.name}: {u.text[:40]}"
        for s in sections(c):
            assert 0 <= s["char_start"] < s["char_end"] <= len(c.text)
        # every heading the renderer emitted survives into the span space
        heads = [l[3:].strip() for l in p.read_text().splitlines() if l.startswith("## ")]
        assert all(h in c.text for h in heads), p.name


def test_a_caption_opening_the_next_page_moves_to_the_next_section():
    """Years 4 and 5 open each section with a full-bleed photo whose caption is emitted
    BEFORE the heading. Fifteen sections end on a caption; the page tells the one that is
    the next section's opener from the fourteen that are their own closing photo."""
    md = ("# t\n\n<!-- p.19 -->\n## STRATEGY 7: OTHER\n\n- Did a thing.\n\n"
          "<!-- p.23 -->\n<!-- CAPTION: a photograph on page 23 -->\n"
          "> Emergency kit supplies distribution.\n\n"
          "## YEAR 6 PRIORITIES\n\nNext year we will do more.\n")
    secs = sections(build(md))
    s7 = next(s for s in secs if s["heading"] == "STRATEGY 7: OTHER")
    s8 = next(s for s in secs if s["heading"] == "YEAR 6 PRIORITIES")
    assert "Emergency kit" not in [u.text[:13] for u in s7["units"]]
    assert any("Emergency kit" in u.text for u in s8["units"])


def test_a_closing_photo_stays_with_its_own_section():
    """Caption on page 7, next heading on page 8: Strategy 1's own parting shot."""
    md = ("# t\n\n<!-- p.5 -->\n## STRATEGY 1\n\n- Did a thing.\n\n"
          "<!-- p.7 -->\n<!-- CAPTION: a photograph on page 7 -->\n"
          "> Drilling for the geothermal system.\n\n"
          "<!-- p.8 -->\n## STRATEGY 2\n\nMore text.\n")
    secs = sections(build(md))
    s1 = next(s for s in secs if s["heading"] == "STRATEGY 1")
    assert any("Drilling" in u.text for u in s1["units"])


# --- OCR provenance: the rule, not only the exceptions ----------------------------------

_OCR_DOC = """# A Report
<!-- generated 2026-01-01T00:00:00Z -- structure from Docling -->
<!-- run: whatever -->
<!-- 96% OCR: 150 of 157 text blocks were read by OCR (no usable text layer), not \
extracted character-exact. Dominant source: docling_ocr. -->

<!-- p.1 -->
This paragraph carries no mark of its own.

[text layer] This one was read from the text layer.
"""


def test_the_document_level_header_is_read():
    from pipeline.canonical import document_provenance
    assert document_provenance(_OCR_DOC) == ("docling_ocr", 96)


def test_header_is_found_below_the_first_line():
    """The first version used _TAG.finditer over a multi-line slice. _TAG is anchored
    ^...$ WITHOUT re.M -- built to match one stripped line -- so it matched nothing and
    silently reported every document as 0% OCR, including the one that is 96%."""
    from pipeline.canonical import document_provenance
    assert document_provenance(_OCR_DOC)[0] == "docling_ocr"
    assert "96% OCR" in _OCR_DOC.splitlines()[3]      # it really is on line 4


def test_an_unmarked_block_inherits_the_documents_dominant_source():
    """render_blocks marks a block only when it DIFFERS from the dominant source, so an
    unmarked block means 'text layer' in four of these reports and 'OCR' in the fifth.
    Absence must never be the carrier of that fact."""
    from pipeline.canonical import build
    c = build(_OCR_DOC)
    unmarked = next(u for u in c.units if u.text.startswith("This paragraph"))
    assert unmarked.flags["text_source"] == "docling_ocr"


def test_an_explicit_mark_still_wins_over_the_default():
    from pipeline.canonical import build
    c = build(_OCR_DOC)
    marked = next(u for u in c.units if u.text.startswith("This one"))
    assert marked.flags["text_source"] == "pdfplumber"


def test_a_document_with_no_header_defaults_to_the_text_layer():
    from pipeline.canonical import document_provenance
    assert document_provenance("# Plain\n\nSome text.\n") == ("pdfplumber", 0)


def test_every_prose_unit_carries_a_source():
    """A NULL text_source is indistinguishable from 'we did not check'."""
    from pipeline.canonical import build
    c = build(_OCR_DOC)
    assert all(u.flags.get("text_source") for u in c.units if u.is_prose)
