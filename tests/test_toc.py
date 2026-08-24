"""The table of contents as structured ground truth.

WHY THIS IS EVIDENCE AND NOT INFERENCE. A table of contents is the document declaring its own
hierarchy: which headings exist, in what order, at what depth, on what page. Every other
signal we use for structure -- font size, Docling's SectionHeaderItem, a heading's shape --
is a guess at something the document has already written down.

Treating it as CONTENT, which is what the conversion does today, loses that and costs twice:
the hierarchy is thrown away, and the dot-leader lines land in the prose as blockquotes.

MEASURED ON THE CAP, pages 2-4. Levels are set by INDENTATION, corroborated by font size:
    x0 76-77, size 12   Welcome Letter, Executive Summary, Introduction
    x0 88-94, size 11   A²ZERO Values, Strategy 3, Strategy 4
    x0 111,   size 10   the 44 named actions
A wrapped entry continues on the next line with neither dot leader nor page number.
"""
from __future__ import annotations

from pipeline.toc import assign_levels, is_toc_page, parse_toc_words


def _w(text, x0, top, x1=None):
    return {"text": text, "x0": x0, "x1": x1 if x1 is not None else x0 + 40,
            "top": top, "bottom": top + 10}


def test_a_page_of_dot_leaders_is_a_contents_page():
    """The dot leader is what makes a contents page recognisable without reading it."""
    assert is_toc_page("Welcome Letter ...................... 5\n"
                       "Executive Summary ................... 6\n"
                       "Introduction ........................ 12\n")


def test_ordinary_prose_is_not_a_contents_page():
    assert not is_toc_page("Ann Arbor City Council unanimously declared a climate emergency "
                           "and directed the City to begin crafting a strategy.")


def test_an_ellipsis_in_prose_does_not_make_a_contents_page():
    """Three dots is punctuation. A leader is a long run of them, repeated down the page."""
    assert not is_toc_page("We asked... and the community answered. Twice... at least.")


# --- one entry per visual line -----------------------------------------------------------

def test_a_line_yields_its_title_and_page_reference():
    words = [_w("Welcome", 76, 100), _w("Letter", 120, 100),
             _w("." * 60, 165, 100), _w("5", 521, 100)]
    got = parse_toc_words(words)
    assert got == [{"title": "Welcome Letter", "page_ref": 5, "x0": 76}]


def test_the_dot_leader_is_not_part_of_the_title():
    words = [_w("Introduction", 76, 100), _w("........................", 146, 100),
             _w("12", 521, 100)]
    assert parse_toc_words(words)[0]["title"] == "Introduction"


def test_a_wrapped_entry_folds_into_the_one_above_it():
    """"Strategy 3: Significantly Improve the Energy Efficiency in our Homes," wraps onto a
    second line carrying neither leader nor page number. It is not a new entry."""
    words = [_w("Strategy", 88, 100), _w("3:", 130, 100), _w("Improve", 150, 100),
             _w("." * 40, 300, 100), _w("41", 521, 100),
             _w("Schools,", 88, 114), _w("Places", 140, 114), _w("of", 180, 114)]
    got = parse_toc_words(words)
    assert len(got) == 1
    assert got[0]["title"] == "Strategy 3: Improve Schools, Places of"


def test_a_page_heading_is_not_an_entry():
    """"CONTENTS" itself has no page reference and opens the page."""
    words = [_w("CONTENTS", 77, 40), _w("Welcome", 76, 100),
             _w("." * 60, 165, 100), _w("5", 521, 100)]
    got = parse_toc_words(words)
    assert [e["title"] for e in got] == ["Welcome"]


# --- depth comes from indentation --------------------------------------------------------

def test_levels_come_from_indentation():
    entries = [{"title": "Introduction", "x0": 76}, {"title": "A2ZERO Values", "x0": 94},
               {"title": "Implement CCA", "x0": 111}]
    assert [e["level"] for e in assign_levels(entries)] == [1, 2, 3]


def test_near_identical_indents_are_one_level():
    """76 and 77 are the same column; 88 and 94 are both strategy depth."""
    entries = [{"title": "a", "x0": 76}, {"title": "b", "x0": 77},
               {"title": "c", "x0": 88}, {"title": "d", "x0": 94}]
    assert [e["level"] for e in assign_levels(entries)] == [1, 1, 2, 2]


def test_a_flat_contents_is_all_level_one():
    entries = [{"title": "a", "x0": 76}, {"title": "b", "x0": 76}]
    assert [e["level"] for e in assign_levels(entries)] == [1, 1]


def test_no_entries_is_safe():
    assert assign_levels([]) == []
    assert parse_toc_words([]) == []


def test_a_bare_page_footer_digit_is_not_an_entry():
    """Contents pages carry their own footer number. Read as a row it becomes a nameless
    entry at the far-left margin, which then defines column 1 and pushes every real entry
    down a level -- "Welcome Letter" came out as L2."""
    words = [_w("Welcome", 76, 100), _w("." * 50, 165, 100), _w("5", 521, 100),
             _w("1", 12, 700)]
    got = parse_toc_words(words)
    assert [e["title"] for e in got] == ["Welcome"]


def test_a_page_heading_does_not_fold_into_the_previous_page_s_last_entry():
    """"CONTENTS" opens pages 3 and 4. Folded as a wrap it corrupted the entry above it and
    invented an indent column at x0=303."""
    words = [_w("Welcome", 76, 100), _w("." * 50, 165, 100), _w("5", 521, 100),
             _w("CONTENTS", 77, 40, x1=200)]
    got = parse_toc_words(words, page_heading_tops={40})
    assert [e["title"] for e in got] == ["Welcome"]


def test_levels_are_normalised_so_the_shallowest_is_one():
    entries = [{"title": "a", "x0": 94}, {"title": "b", "x0": 111}]
    assert [e["level"] for e in assign_levels(entries)] == [1, 2]


def test_an_entry_title_must_contain_letters():
    """The surviving footer case: a row of "1" and "2" parsed as the entry titled "1" on
    page 2. A contents entry names something; a row of bare numerals is page furniture,
    however many numerals it has."""
    words = [_w("1", 12, 700), _w("2", 60, 700),
             _w("Introduction", 76, 100), _w("." * 40, 146, 100), _w("12", 521, 100)]
    assert [e["title"] for e in parse_toc_words(words)] == ["Introduction"]


# --- a contents page need not use dot leaders ---------------------------------------------

def test_leading_page_numbers_also_make_a_contents_page():
    """The dot leader was a CAP-specific tell. The annual reports write the page number
    FIRST and use no leaders at all:

        3 INTRODUCTION
        4 GREENHOUSE GAS EMISSIONS SUMMARY
        6 STRATEGY 1: 100% RENEWABLES

    What both styles share is a column of ASCENDING page references beside headings."""
    assert is_toc_page("CONTENTS\n3 INTRODUCTION\n4 GREENHOUSE GAS EMISSIONS SUMMARY\n"
                       "6 STRATEGY 1: 100% RENEWABLES\n8 STRATEGY 2: ELECTRIFICATION\n")


def test_descending_or_scattered_numbers_are_not_a_contents_page():
    """Ascent is the signal. A page of statistics has numbers in no particular order."""
    assert not is_toc_page("2030 target\n1990 baseline\n2021 actual\n45 percent\n12 sites\n")


def test_a_numbered_list_of_prose_is_not_a_contents_page():
    """Ordinals ascend too, so the numbers must also look like PAGE references -- small,
    and not restarting."""
    assert not is_toc_page("1 We will power the grid with renewable energy and this "
                           "sentence continues well past any heading length\n"
                           "2 We will switch appliances and vehicles to electric across "
                           "the whole community over the coming decade\n")


def test_a_leading_page_number_is_not_part_of_the_title():
    words = [_w("3", 40, 100), _w("INTRODUCTION", 60, 100)]
    got = parse_toc_words(words)
    assert got == [{"title": "INTRODUCTION", "page_ref": 3, "x0": 60}]


def test_a_prose_page_with_a_few_ascending_numbers_is_not_a_contents_page():
    """MEASURED. Requiring only a COUNT of ascending references made the CAP report 19
    contents pages instead of 3: its strategy and action pages carry a handful of ascending
    figures among prose. What separates them is the RATIO -- a contents page is MOSTLY
    entries. CAP p.2 scores 0.85; p.6 scores 0.15; p.10 scores 0.05."""
    prose = "\n".join(
        [f"In {y} the City advanced its work on renewable energy across the community."
         for y in (2020, 2021, 2022)] +
        ["Ann Arbor has pursued this through a range of programmes and partnerships."] * 14)
    assert not is_toc_page(prose + "\n3 INTRODUCTION\n4 SUMMARY\n6 STRATEGY ONE\n")


def test_a_scattered_layout_has_no_hierarchy():
    """Year 4's contents is arranged around a graphic: eleven entries at eleven different
    indents. That is a design, not a hierarchy, and reading it as one produced ten levels
    for eleven entries. A column must hold more than one entry to be a level."""
    entries = [{"title": t, "x0": x} for t, x in
               [("INTRODUCTION", 351), ("EMISSIONS", 209), ("STRATEGY 1", 194),
                ("STRATEGY 2", 85), ("STRATEGY 3", 179), ("STRATEGY 4", 213),
                ("STRATEGY 5", 166), ("STRATEGY 6", 265), ("STRATEGY 7", 313)]]
    assert {e["level"] for e in assign_levels(entries)} == {1}


def test_a_real_hierarchy_still_resolves():
    """The CAP's columns each hold many entries, so they are levels."""
    entries = ([{"title": f"a{i}", "x0": 76} for i in range(11)]
               + [{"title": f"b{i}", "x0": 94} for i in range(12)]
               + [{"title": f"c{i}", "x0": 111} for i in range(49)])
    assert sorted({e["level"] for e in assign_levels(entries)}) == [1, 2, 3]


def test_a_page_that_yields_no_entries_is_not_a_contents_page():
    """CAP pages 124 and 126 are numbered APPENDIX LISTS -- "28. January 21, 2020 - A²ZERO
    presentation..." -- whose ordinals ascend exactly like page references. No shape test
    separates them from a contents list, and it matters because the page list drives
    suppression: marking them would have deleted two pages of appendix.

    The parse settles it. Both yield zero entries, and a contents page that names nothing
    is not one."""
    from pipeline.toc import qualifies
    assert not qualifies("28. January 21, 2020 - a meeting\n29. January 22, 2020 - another\n",
                         entries=[])
    assert qualifies("Welcome ..... 5\nSummary ..... 6\nIntro ..... 12\n",
                     entries=[{"title": "Welcome"}, {"title": "Summary"},
                              {"title": "Intro"}])
