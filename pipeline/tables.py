"""A Docling table -> a markdown table, without letting the model supply the characters.

WHY THIS EXISTS. convert_docling's block pass had two branches, PictureItem and "anything
with .text". A TableItem has no `.text` -- its content lives in `data.table_cells` -- so it
matched neither and was dropped before blocks.json was written. The CAP carries 536 rows of
table Docling detected correctly, and every one of them reached the renderer only as loose
words swept off the page, columns interleaved into unreadable prose:

    | Bulk Purchase of Renewables $605,000 85,000 3.9% $7 $$; SCALE LOCAL; N

against the standard's

    | Bulk Purchase of Renewables | $605,000 | 85,000 | 3.9% | $7 | LOCAL; NRG; AIR; ... |

A TABLE IS NOT A SPECIAL CASE FOR THIS PIPELINE. It is a grid of small crops, and the same
rule holds inside it as everywhere else: Docling proposes the cell boundaries and the reading
order of the grid, pdfplumber supplies the characters within each one. Nothing here reads a
cell's text out of the model's output when the page has that text; see verify_cells().
"""
from __future__ import annotations


def build_grid(cells: list[dict], n_rows: int = 0, n_cols: int = 0) -> list[list[dict]]:
    """Cells with grid offsets -> rows of logical cells, spans resolved.

    THE DOUBLED COLUMNS ARE A SPAN, NOT A BUG. Docling's own markdown export of the CAP's
    Action Summary Table repeats every column:

        | STRATEGY 1 | STRATEGY 1 | Total Costs | Total Costs | ...

    because a cell spanning two grid columns is written once per column it covers. Reading
    the flattened export and de-duplicating adjacent equal columns would be guesswork -- a
    real table may legitimately repeat a value -- so this works from the offsets instead: a
    cell is emitted at the position it STARTS, and the positions its span covers are skipped.
    The repetition then cannot arise, and a table that really does repeat a value keeps it.
    """
    if not cells:
        return []
    n_rows = n_rows or max(c["row"] + max(c.get("row_span", 1), 1) for c in cells)
    n_cols = n_cols or max(c["col"] + max(c.get("col_span", 1), 1) for c in cells)
    at = {(c["row"], c["col"]): c for c in cells}

    covered: set[tuple[int, int]] = set()
    for c in cells:
        for r in range(c["row"], c["row"] + max(c.get("row_span", 1), 1)):
            for k in range(c["col"], c["col"] + max(c.get("col_span", 1), 1)):
                if (r, k) != (c["row"], c["col"]):
                    covered.add((r, k))

    # ONE COLUMN AXIS FOR THE WHOLE TABLE, not one per row. Emitting only the cells a row
    # happens to have shifts every later value left when a cell is absent -- and absence is
    # common and meaningful here: the CAP's "Not Calculated" rows leave %total emissions and
    # $/ton empty, so "Private EV Fleets | $123,000 | Not Calculated | AIR; RES; $$..." put
    # the co-benefit codes in the $/ton column.
    # THE DECLARED COLUMN INDICES, DELIBERATELY -- and this was tried the other way first.
    # Inferring the axis from the cells' own geometry is the move this pipeline makes
    # everywhere else, and on this table it is worse. Clustering left edges gave THIRTEEN
    # columns for an eight-column table, because the numeric columns are right-aligned.
    # Merging overlapping x-extents into bands then gave TWO, because Docling folds the KEY
    # legend into the same TableItem and its full-width rows bridge every band. Docling's
    # indices are imperfect but they are a consistent grid, and the geometry is used where it
    # is reliable instead: inside a cell, to cut the boundaries TableFormer missed.
    live = [c for c in cells if (c["row"], c["col"]) not in covered]
    cols = sorted({c["col"] for c in live})
    where = {col: i for i, col in enumerate(cols)}

    by_row: dict[int, list[dict]] = {}
    for c in live:
        by_row.setdefault(c["row"], []).append(c)

    grid = []
    for r in sorted(by_row):
        row: list[dict | None] = [None] * len(cols)
        for c in sorted(by_row[r], key=lambda c: c["col"]):
            i = where[c["col"]]
            # A CELL IS NEVER DROPPED. Splitting a merged cell can put two pieces on one
            # declared column; an earlier version bounded the search by the row width and
            # silently lost every value past it, which is the one outcome this whole module
            # exists to prevent. The row grows instead.
            while i < len(row) and row[i] is not None:
                i += 1
            while i >= len(row):
                row.append(None)
            row[i] = c
        if any(x is not None for x in row):
            grid.append(row)
    return grid


# A gap this many times the type size, inside a cell, is a column boundary TableFormer failed
# to cut. Measured over all 856 word gaps in the CAP's six tables: twelve sit above three ems
# (31.9 to 58.7 points) and every one is a real boundary; the other 844 are word spaces.
#
# THIS IS SET WHERE IT IS BECAUSE LOWERING IT CANNOT WORK, not because three is elegant. The
# two boundaries this misses -- "0.6% $ 5,839" at 2.85 ems and "0.05% $3,636" at 2.73 -- sit
# inside a band where the two populations genuinely overlap: at 2.85 ems this document holds
# BOTH a column boundary ("0.6%" | "$") and an ordinary word space ("Reuse" | "Material", from
# "Change the Way We Use, Reuse, and Dispose of Material"). Identical geometry, opposite
# meaning. A tighter threshold does not recover the misses, it trades them for false cuts,
# and a false cut invents a column the page does not have.
#
# What separates those two cases is what the words MEAN, not where they sit, so if the last
# few are worth having they need the semantic pass, which can judge "0.6% $ 5,839" as two
# values without altering either. Three ems is the limit of what geometry can decide here.
_CELL_GUTTER_EMS = 3.0


def split_spanning_cell(cell: dict, words: list[dict]) -> list[dict]:
    """One cell Docling marked as spanning -> one cell per column actually inside it.

    GEOMETRY DECIDES, NOT THE DECLARED SPAN. col_span was the obvious gate and it is the
    wrong one: measured across all 856 word gaps inside the CAP's six tables, 844 sit under
    three ems and twelve sit above, and every one of the twelve is a real column boundary --
    but only seven of them carry col_span=2. The other five ("362,200 16.5%", "122,900 5.6%",
    "2,600 .1%", "400 0.0%", "58.5pt apart") are declared as single cells, so gating on the
    span left a quarter of the table's numbers in the wrong column.

    The separation in that measurement is what makes this safe: two ems of slack on one side,
    an order of magnitude on the other. Genuinely spanning text -- "Community Choice
    Aggregation", "LOCAL; NRG; AIR; JOBS; RES" -- holds gaps of two to three points and is
    never touched.

    `words` are pdfplumber word dicts from inside the cell, in x order -- the same characters
    the cell text came from, so a cut can never introduce text the page does not have.
    """
    if len(words) < 2:
        return [cell]
    ems = sorted(float(w.get("size") or 0) or abs(w["bottom"] - w["top"]) or 10.0
                 for w in words)
    floor = _CELL_GUTTER_EMS * ems[len(ems) // 2]
    cuts = [i for i in range(len(words) - 1)
            if words[i + 1]["x0"] - words[i]["x1"] > floor]
    if not cuts:
        return [cell]

    out, start = [], 0
    for j, end in enumerate([*cuts, len(words) - 1]):
        part = words[start:end + 1]
        out.append({**cell,
                    "col": cell["col"] + j, "col_span": 1,
                    "pdf_text": " ".join(w["text"] for w in part),
                    # The model's reading of the whole run cannot be carried onto a piece of
                    # it -- that would attribute text to a cell it was never read from.
                    "docling_text": "",
                    "bbox": [part[0]["x0"], cell["bbox"][1] if cell.get("bbox") else 0,
                             part[-1]["x1"], cell["bbox"][3] if cell.get("bbox") else 0],
                    "_split_from_span": True})
        start = end + 1
    return out


def cell_text(cell: dict | None) -> str:
    """The characters for one cell: pdfplumber's if the crop found any, else Docling's.

    Same precedence, and for the same reason, as choose_block_text() applies to a text block.
    An empty cell is genuinely empty in many tables ("Not Calculated" rows leave %total and
    $/ton blank), so an empty crop is only a fallback trigger when Docling saw something.
    """
    if cell is None:
        return ""              # a position no cell occupies; the blank is the table's own
    pdf = (cell.get("pdf_text") or "").strip()
    return pdf or (cell.get("docling_text") or "").strip()


def to_markdown(grid: list[list[dict]]) -> str:
    """A grid -> a GitHub markdown table.

    The first row is the header, because that is what markdown requires and what the CAP's
    tables actually have -- "STRATEGY 1 | Total Costs | GHG Reduction | ...". A table whose
    first row is data loses nothing by being labelled a header here; a table rendered with no
    header row is not a table in markdown at all.

    Pipes inside a cell are escaped rather than dropped: "$/ton" is fine, but a cell that
    really contains a pipe would otherwise invent a column.
    """
    if not grid:
        return ""
    width = max(len(r) for r in grid)

    def row(cells: list[dict | None]) -> str:
        vals = [cell_text(c).replace("|", "\\|").replace("\n", " ") for c in cells]
        vals += [""] * (width - len(vals))
        return "| " + " | ".join(vals) + " |"

    out = [row(grid[0]), "|" + "---|" * width]
    out += [row(r) for r in grid[1:]]
    return "\n".join(out)


def is_degenerate(grid: list[list[dict]]) -> bool:
    """Whether a 'table' is really a single column or a single row of prose.

    Docling's layout model types the CAP's contents pages as tables -- two columns of entry
    and page number -- and it is right to. But it also occasionally proposes a one-column
    table over a run of paragraphs, and rendering that as markdown wraps ordinary prose in
    pipes for no gain. One column is not a table; one row might be a header with nothing
    under it, which is also not a table.
    """
    if not grid or len(grid) < 2:
        return True
    return max(sum(1 for c in r if c is not None) for r in grid) < 2
