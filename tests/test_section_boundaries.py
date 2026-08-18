"""The pdfplumber <-> CU handoff: every case pinned here was a real failure on the actual
Year 4/5 documents, found by running the tool against them and checking the result, not
anticipated in advance.

A synthetic Conversion (from pipeline.convert_document) stands in for a real pdfplumber
run so these need no PDF and no network -- the module under test only ever calls
`.text` and `.page_for()` on whatever it's given.
"""
from __future__ import annotations

from pipeline.convert_document import Conversion, _assemble
from pipeline.section_boundaries import build, cu_sections, locate


def fake_conv(pages: list[str]) -> Conversion:
    text, page_map = _assemble(pages)
    return Conversion(source_path="t.pdf", converter="test", converter_version="0",
                      text=text, page_map=page_map, content_hash="x",
                      n_pages=len(pages), converted_at="now")


# ── merging continuations ───────────────────────────────────────────────────────────

def test_identical_repeated_heading_merges_into_one_section():
    """Measured on Year 5: 'GREENHOUSE GAS EMISSIONS SUMMARY' appears twice
    consecutively -- a page continuation, not two sections.
    """
    cu = "## GREENHOUSE GAS EMISSIONS SUMMARY\n\nFirst page text.\n\n## GREENHOUSE GAS EMISSIONS SUMMARY\n\nSecond page text."
    secs = cu_sections(cu)
    assert len(secs) == 1


def test_differently_worded_continuation_merges_by_number_alone():
    """Measured on Year 4: a strategy's continuation can carry ENTIRELY different
    wording from its main heading -- 'STRATEGY 1: Powering Our Electrical Grid...'
    then later 'STRATEGY 1: 100% RENEWABLES'. Comparing remaining text after the
    number (Year 5's pattern) would never merge these; the number must be the key.
    """
    cu = "## STRATEGY 1: Powering Our Electrical Grid\n\nBody one.\n\n## STRATEGY 1: 100% RENEWABLES\n\nBody two."
    secs = cu_sections(cu)
    assert len(secs) == 1


def test_different_strategy_numbers_are_never_merged():
    cu = "## STRATEGY 1: RENEWABLES\n\nBody one.\n\n## STRATEGY 2: ELECTRIFICATION\n\nBody two."
    secs = cu_sections(cu)
    assert len(secs) == 2


def test_a_numbered_and_an_unnumbered_heading_are_never_merged():
    """_same_section's explicit rule: one numbered, one not, is never a continuation --
    without this a bare 'CLOSING' could accidentally merge with an unrelated numbered
    section purely because neither matches on full core text.
    """
    cu = "## STRATEGY 7: OTHER\n\nBody one.\n\n## CLOSING\n\nBody two."
    secs = cu_sections(cu)
    assert len(secs) == 2


# ── the four real character-normalisation gaps ──────────────────────────────────────

def test_curly_vs_straight_apostrophe_still_locates():
    """Measured on Year 5's CLOSING section: CU straightens quotes, pdfplumber keeps
    the PDF's curly glyph. Without normalising, this section was NOT LOCATED.
    """
    cu = "## CLOSING\n\nA2ZERO is our community's plan to become carbon neutral."
    pdf = fake_conv(["A2ZERO is our community’s plan to become carbon neutral."])
    secs = locate(cu_sections(cu), pdf)
    assert secs[0].located


def test_en_dash_vs_hyphen_still_locates():
    """Measured on Year 4 Strategy 3: CU flattens to a plain hyphen; pdfplumber
    preserves the PDF's en dash."""
    cu = "## STRATEGY 3: ENERGY EFFICIENCY\n\nEnergy efficiency - or energy waste reduction - is critical."
    pdf = fake_conv(["Energy efficiency – or energy waste reduction – is critical."])
    secs = locate(cu_sections(cu), pdf)
    assert secs[0].located


def test_superscript_two_still_locates():
    """Measured on two of seven Year 4 strategy anchors: CU renders 'A2ZERO', pdfplumber
    preserves the PDF's superscript glyph 'A²ZERO'. Not a one-off.
    """
    cu = "## STRATEGY 1: RENEWABLES\n\nStrategy 1 of A2ZERO calls for community power."
    pdf = fake_conv(["Strategy 1 of A²ZERO calls for community power."])
    secs = locate(cu_sections(cu), pdf)
    assert secs[0].located


def test_escaped_dash_and_stray_leading_period_are_stripped_from_the_anchor():
    """Measured directly in CU's own Year 4 output: a section body began with a literal
    stray '. ' (CU appears to drop a list marker but keep its trailing punctuation), and
    elsewhere CU emits a markdown-escaped '\\-' bullet neither of which pdfplumber's
    plain text ever contains.
    """
    cu_escaped = "## S\n\n\\- The City secured funding for the program this year."
    pdf = fake_conv(["The City secured funding for the program this year."])
    assert locate(cu_sections(cu_escaped), pdf)[0].located

    cu_period = "## S\n\n. Supported the new ordinance this year in full."
    pdf2 = fake_conv(["Supported the new ordinance this year in full."])
    assert locate(cu_sections(cu_period), pdf2)[0].located


# ── the case that SHOULD fail, and must say so ──────────────────────────────────────

def test_genuinely_scrambled_text_is_reported_not_located_not_guessed():
    """Measured on Year 4 Strategy 2: pdfplumber's column-interleaving defect spliced an
    adjacent bullet mid-sentence -- 'phase-out . Supported the installation of
    geothermal at ordinance by launching...' -- so the anchor words are genuinely NOT
    contiguous in pdfplumber's reading order on that page. This must NOT locate, and
    must not fall back to a fuzzy/partial match that would cite a scrambled span as if
    it were clean. Refusing here is the correct behaviour, not a bug to route around.
    """
    cu = "## STRATEGY 2\n\nSupported the gas leaf blower phase-out ordinance by launching a website."
    pdf = fake_conv(["Supported the gas leaf blower phase-out . Supported the installation of "
                     "geothermal at ordinance by launching a website."])
    secs = locate(cu_sections(cu), pdf)
    assert not secs[0].located


# ── boundary bookkeeping ────────────────────────────────────────────────────────────

def test_sections_never_overlap_and_leave_no_gap():
    cu = "## A\n\nFirst section body text here.\n\n## B\n\nSecond section body text here."
    pdf = fake_conv(["First section body text here. Second section body text here."])
    secs = locate(cu_sections(cu), pdf)
    assert all(s.located for s in secs)
    assert secs[0].pdf_end == secs[1].pdf_start


def test_page_numbers_come_from_the_same_conversion_as_the_text():
    """The bug this structurally prevents: an earlier version took text and a page
    lookup as SEPARATE arguments, and a stale on-disk text dump (46,038 chars) paired
    with a freshly computed page map (45,989 chars) from a re-run made a page lookup
    silently return None near the end of the document. Tying both to one Conversion
    object makes that drift impossible rather than something to remember to avoid.
    """
    cu = "## A\n\nSection text spanning the only page of this document."
    pdf = fake_conv(["Section text spanning the only page of this document."])
    secs = build(cu, pdf)
    assert secs[0].page_start == 1 and secs[0].page_end == 1


def test_an_unlocated_section_does_not_break_boundaries_for_the_ones_that_are():
    """A failure in one section (the scrambled-text case) must not corrupt the pdf_end
    of a NEIGHBOURING located section -- ends are computed from located sections only.
    """
    cu = "## A\n\nReal clean text for section A right here today.\n\n## B (scrambled)\n\nxyz not present anywhere.\n\n## C\n\nReal clean text for section C right here today."
    pdf = fake_conv(["Real clean text for section A right here today. "
                     "Real clean text for section C right here today."])
    secs = locate(cu_sections(cu), pdf)
    assert secs[0].located and not secs[1].located and secs[2].located
    assert secs[0].pdf_end == secs[2].pdf_start   # B contributed no boundary at all


def test_continuation_body_text_is_accumulated_not_dropped():
    """An earlier version dropped a continuation's BODY along with its heading -- only
    invisible because a LOCATED section's span comes from pdfplumber, not cu_body. The
    omission would have silently truncated cu_body exactly for the sections that most
    need it complete: the ones whose pdfplumber anchor fails to locate, where cu_body
    is the only fallback content available.
    """
    cu = "## STRATEGY 1: Long Title\n\nPage one body.\n\n## STRATEGY 1: SHORT\n\nPage two body."
    secs = cu_sections(cu)
    assert len(secs) == 1
    assert "Page one body." in secs[0].cu_body
    assert "Page two body." in secs[0].cu_body
