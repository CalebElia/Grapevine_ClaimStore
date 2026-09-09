"""Docling tables -> markdown, with the characters coming from the page.

Every fixture here is the real shape of the CAP's Action Summary Table (page 10), which is
the document's densest carrier of fact -- 44 actions with costs, GHG reduction, share of
total emissions, dollars per ton and co-benefit codes -- and which reached the renderer as
interleaved loose words because a TableItem has no `.text` and was dropped before
blocks.json was written.
"""
from __future__ import annotations

from pipeline.tables import (build_grid, cell_text, is_degenerate,
                             split_spanning_cell, to_markdown)


def _c(row, col, text, col_span=1, row_span=1, pdf_text=None):
    return {"row": row, "col": col, "col_span": col_span, "row_span": row_span,
            "docling_text": text, "pdf_text": pdf_text, "bbox": None, "is_header": row == 0}


# ── spans, which is where the doubled columns came from ──────────────────────────────────

def test_a_cell_spanning_two_columns_is_emitted_once():
    """Docling's own markdown export doubles every column of this table.

    "| STRATEGY 1 | STRATEGY 1 | Total Costs | Total Costs |" -- because a cell covering two
    grid columns is written once per column. Working from the offsets, it appears once.
    """
    cells = [_c(0, 0, "STRATEGY 1", col_span=2), _c(0, 2, "Total Costs", col_span=2),
             _c(1, 0, "Community Choice Aggregation", col_span=2),
             _c(1, 2, "$3,245,000", col_span=2)]
    grid = build_grid(cells)
    assert [[cell_text(c) for c in r] for r in grid] == [
        ["STRATEGY 1", "Total Costs"],
        ["Community Choice Aggregation", "$3,245,000"]]


def test_a_table_that_genuinely_repeats_a_value_keeps_it():
    """De-duplicating equal adjacent columns would have destroyed this; offsets do not."""
    cells = [_c(0, 0, "Not Calculated"), _c(0, 1, "Not Calculated")]
    assert [cell_text(c) for c in build_grid(cells)[0]] == ["Not Calculated", "Not Calculated"]


def test_a_row_spanning_cell_does_not_repeat_down_the_rows():
    """The covered position stays BLANK rather than repeating -- and stays a position, so
    the cell beside it keeps its column."""
    cells = [_c(0, 0, "STRATEGY 2", row_span=2), _c(0, 1, "Bus Electrification"),
             _c(1, 1, "Bulk Purchase EVs")]
    grid = build_grid(cells)
    assert [[cell_text(c) for c in r] for r in grid] == [
        ["STRATEGY 2", "Bus Electrification"], ["", "Bulk Purchase EVs"]]


def test_an_empty_cell_list_is_not_a_table():
    assert build_grid([]) == []


# ── the characters ───────────────────────────────────────────────────────────────────────

def test_pdfplumbers_characters_win_over_doclings():
    """The founding constraint holds inside a cell exactly as it holds inside a block."""
    assert cell_text(_c(1, 1, "$3.245.000", pdf_text="$3,245,000")) == "$3,245,000"


def test_doclings_reading_is_used_only_when_the_crop_is_empty():
    """A cell whose crop returned nothing still has to carry what Docling saw."""
    assert cell_text(_c(1, 1, "Not Calculated", pdf_text="")) == "Not Calculated"
    assert cell_text(_c(1, 1, "Not Calculated", pdf_text=None)) == "Not Calculated"


def test_a_genuinely_blank_cell_stays_blank():
    """The "Not Calculated" rows leave %total emissions and $/ton empty, and that is data."""
    assert cell_text(_c(1, 3, "", pdf_text="")) == ""


# ── rendering ────────────────────────────────────────────────────────────────────────────

def test_the_action_summary_table_renders_as_markdown():
    """Real page 10 content, in the shape the standard produces at its line 129."""
    cells = [_c(0, 0, "STRATEGY 1"), _c(0, 1, "Total Costs"), _c(0, 2, "GHG Reduction"),
             _c(1, 0, "Community Choice Aggregation"), _c(1, 1, "$3,245,000"),
             _c(1, 2, "784,000"),
             _c(2, 0, "Landfill Solar Project"), _c(2, 1, "$80,000"), _c(2, 2, "23,000")]
    md = to_markdown(build_grid(cells)).splitlines()
    assert md[0] == "| STRATEGY 1 | Total Costs | GHG Reduction |"
    assert md[1] == "|---|---|---|"
    assert md[2] == "| Community Choice Aggregation | $3,245,000 | 784,000 |"
    assert md[3] == "| Landfill Solar Project | $80,000 | 23,000 |"


def test_a_short_row_is_padded_so_the_columns_stay_aligned():
    """"Private EV Fleets | $123,000 | Not Calculated | | |" -- the blanks are real."""
    cells = [_c(0, 0, "Action"), _c(0, 1, "Cost"), _c(0, 2, "$/ton"),
             _c(1, 0, "Private EV Fleets"), _c(1, 1, "$123,000")]
    md = to_markdown(build_grid(cells)).splitlines()
    assert md[2] == "| Private EV Fleets | $123,000 |  |"


def test_a_pipe_inside_a_cell_cannot_invent_a_column():
    cells = [_c(0, 0, "a"), _c(0, 1, "b"), _c(1, 0, "x|y"), _c(1, 1, "z")]
    assert to_markdown(build_grid(cells)).splitlines()[2] == "| x\\|y | z |"


def test_a_newline_inside_a_cell_does_not_break_the_row():
    """A wrapped cell is still one cell; a literal newline would end the table row."""
    cells = [_c(0, 0, "a"), _c(0, 1, "b"),
             _c(1, 0, "Energy Concierge &\nCommunity Education"), _c(1, 1, "$820,000")]
    row = to_markdown(build_grid(cells)).splitlines()[2]
    assert row == "| Energy Concierge & Community Education | $820,000 |"


def test_an_empty_grid_renders_as_nothing():
    assert to_markdown([]) == ""


# ── what is not a table ──────────────────────────────────────────────────────────────────

def test_a_single_column_is_not_a_table():
    """Docling sometimes proposes a one-column table over a run of paragraphs."""
    assert is_degenerate(build_grid([_c(0, 0, "A paragraph."), _c(1, 0, "Another one.")]))


def test_a_single_row_is_not_a_table():
    assert is_degenerate(build_grid([_c(0, 0, "Header"), _c(0, 1, "Header")]))


def test_a_real_table_is_not_degenerate():
    assert not is_degenerate(build_grid(
        [_c(0, 0, "a"), _c(0, 1, "b"), _c(1, 0, "c"), _c(1, 1, "d")]))


# ── the column axis ──────────────────────────────────────────────────────────────────────

def test_an_absent_cell_leaves_a_blank_rather_than_shifting_the_row():
    """Real page 10 failure: "Private EV Fleets | $123,000 | Not Calculated | AIR; RES..."

    The %total emissions and $/ton cells are genuinely empty on the "Not Calculated" rows.
    Emitting only the cells a row has put the co-benefit codes in the $/ton column.
    """
    cells = [_c(0, 0, "Action"), _c(0, 1, "Cost"), _c(0, 2, "%total"), _c(0, 3, "$/ton"),
             _c(0, 4, "Co-Benefits"),
             _c(1, 0, "Private EV Fleets"), _c(1, 1, "$123,000"),
             _c(1, 4, "AIR; RES; $$; HEALTH; SCALE")]
    row = to_markdown(build_grid(cells)).splitlines()[2]
    assert row == "| Private EV Fleets | $123,000 |  |  | AIR; RES; $$; HEALTH; SCALE |"


# ── cells TableFormer merged across a column boundary ────────────────────────────────────

def _pw(text, x0, x1, size=9.0):
    return {"text": text, "x0": x0, "x1": x1, "top": 100, "bottom": 109, "size": size}


def test_a_span_carrying_a_gutter_is_cut():
    """Measured on page 10: "35.8% $4" is one cell of col_span 2 with a 45.2pt gap."""
    cell = _c(1, 5, "35.8% $4", col_span=2)
    parts = split_spanning_cell(cell, [_pw("35.8%", 373, 396), _pw("$4", 441, 452)])
    assert [cell_text(p) for p in parts] == ["35.8%", "$4"]
    assert [p["col"] for p in parts] == [5, 6]
    assert all(p["col_span"] == 1 for p in parts)


def test_the_header_gutter_is_cut_too():
    """"%total emissions $/ton": gaps of 2.1 and 31.9 -- only the second is a boundary."""
    cell = _c(0, 5, "%total emissions $/ton", col_span=2)
    parts = split_spanning_cell(cell, [_pw("%total", 327, 354), _pw("emissions", 356, 398),
                                       _pw("$/ton", 430, 452)])
    assert [cell_text(p) for p in parts] == ["%total emissions", "$/ton"]


def test_a_genuinely_spanning_heading_is_not_cut():
    """"Community Choice Aggregation" spans two columns and its words sit 2.3pt apart."""
    cell = _c(1, 0, "Community Choice Aggregation", col_span=2)
    parts = split_spanning_cell(cell, [_pw("Community", 37, 80), _pw("Choice", 82, 110),
                                       _pw("Aggregation", 112, 168)])
    assert len(parts) == 1 and parts[0] is cell


def test_a_gutter_is_cut_even_where_docling_declared_no_span():
    """Five of the twelve real boundaries on page 10 sit inside col_span=1 cells.

    "362,200 16.5%" is one Docling cell with a 55.4pt gap in 9pt type. Gating on the span
    left those numbers in the GHG Reduction column with the percentage stuck to them.
    """
    cell = _c(6, 4, "362,200 16.5%", col_span=1)
    parts = split_spanning_cell(cell, [_pw("362,200", 285, 321), _pw("16.5%", 376, 400)])
    assert [cell_text(p) for p in parts] == ["362,200", "16.5%"]


def test_a_narrow_gap_is_never_cut_whatever_the_span():
    """844 of the 856 measured gaps are word spaces, and none may be touched."""
    cell = _c(1, 0, "Community Choice Aggregation", col_span=1)
    parts = split_spanning_cell(cell, [_pw("Community", 37, 80), _pw("Choice", 82, 110),
                                       _pw("Aggregation", 112, 168)])
    assert len(parts) == 1


def test_a_cut_never_carries_doclings_reading_onto_a_piece():
    """The model read the whole run; attributing it to a fragment would invent a cell."""
    cell = _c(1, 5, "35.8% $4", col_span=2)
    parts = split_spanning_cell(cell, [_pw("35.8%", 373, 396), _pw("$4", 441, 452)])
    assert all(p["docling_text"] == "" for p in parts)
    assert all(p["pdf_text"] for p in parts)


def test_the_cut_threshold_scales_with_the_type():
    """A 45pt gap is a gutter in 9pt type and ordinary spacing in 30pt display."""
    cell = _c(0, 0, "A B", col_span=2)
    small = [_pw("A", 0, 20, size=9.0), _pw("B", 65, 85, size=9.0)]
    big = [_pw("A", 0, 20, size=30.0), _pw("B", 65, 85, size=30.0)]
    assert len(split_spanning_cell(cell, small)) == 2
    assert len(split_spanning_cell(cell, big)) == 1


def test_no_cell_is_ever_dropped_when_two_land_on_one_column():
    """Splitting a merged cell can put two pieces on one declared column.

    An earlier version bounded the placement search by the row's width and silently lost
    every value past it -- 44 actions' worth of costs, on the table this module exists for.
    """
    cells = [_c(0, 0, "a"), _c(0, 1, "b"),
             _c(1, 0, "x"), _c(1, 0, "y"), _c(1, 0, "z"), _c(1, 1, "w")]
    texts = [cell_text(c) for c in build_grid(cells)[1]]
    assert [t for t in texts if t] == ["x", "y", "z", "w"]


def test_every_cell_survives_the_round_trip():
    """The invariant, stated directly: as many non-empty cells out as went in."""
    cells = [_c(r, c, f"v{r}{c}") for r in range(4) for c in range(3)]
    grid = build_grid(cells)
    assert sum(1 for row in grid for c in row if cell_text(c)) == len(cells)


def test_the_threshold_is_not_lowered_into_a_false_cut():
    """At 2.85 ems this document holds a boundary AND a word space. Geometry cannot tell.

    "0.6%" | "$" is a column boundary; "Reuse" | "Material" is a word space inside "Change
    the Way We Use, Reuse, and Dispose of Material". Both measure 2.85 ems on page 10. This
    test exists so that a future attempt to catch the first does not silently create the
    second -- the cut would invent a column the page does not have.
    """
    boundary = [_pw("0.6%", 100, 120), _pw("$", 145.7, 150)]      # 2.85 em apart
    wordgap = [_pw("Reuse", 100, 125), _pw("Material", 150.7, 190)]
    assert len(split_spanning_cell(_c(0, 0, "x", col_span=2), boundary)) == 1
    assert len(split_spanning_cell(_c(0, 0, "x", col_span=2), wordgap)) == 1
