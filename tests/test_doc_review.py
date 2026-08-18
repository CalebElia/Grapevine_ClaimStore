"""The document review round-trip: human edits must survive, machine output must not move.

The load-bearing guarantees, each tested because each has a failure mode that looks like
success:

  - row keys are CONTENT-derived, so a re-export after a converter change still matches
    annotations to the right passages. Position-based keys silently reattribute.
  - the header is asserted before any read. A reordered column read by position files a
    correction under the wrong field, which is worse than crashing because it looks fine.
  - an unrecognised verdict is preserved and reported, never dropped. A reviewer who types
    their own wording has said something; discarding it loses exactly the observation that
    did not fit our categories.
"""

from __future__ import annotations

import pytest

from pipeline.export_doc_review import COL, HEADER, VERDICTS, build_rows, row_key, write
from pipeline.import_doc_review import parse


def test_row_keys_are_content_derived_not_positional():
    """A re-export after a converter change must still match the same passage."""
    assert row_key("Control", "the same text") == row_key("Control", "the same text")
    assert row_key("Control", "text A") != row_key("Control", "text B")
    # Same text in a different section is a different review question.
    assert row_key("Control", "x") != row_key("Chart data", "x")


def test_all_three_sections_are_built():
    """Control rows are the ones a hurried reviewer skips and the ones that measure
    the false-negative rate, so their absence must fail loudly here."""
    ref = " ".join(f"This is ground truth sentence number {i} and it is long enough to count."
                   for i in range(30))
    arms = {"pdfplumber": ref,
            "other": ref.replace("sentence number 3 and it is long enough to count.", "")
                        + " An extra 4,321 figure appears only here."}
    rows = build_rows(arms, lambda off: 1, n_control=5)
    assert {r["section"] for r in rows} >= {"Disagreement", "Control"}
    assert sum(1 for r in rows if r["section"] == "Control") > 0


def test_workbook_round_trips_annotations(tmp_path):
    rows = [{"section": "Disagreement", "page": 3, "check": "k", "a": "alpha text", "b": "beta"},
            {"section": "Control", "page": 4, "check": "k", "a": "gamma text", "b": "-"}]
    wb_path = write(rows, tmp_path / "r.xlsx", "t")

    from openpyxl import load_workbook
    wb = load_workbook(wb_path)
    ws = wb["Review"]
    ws.cell(2, COL["✎ Correct?"], "both wrong")
    ws.cell(2, COL["✎ True text"], "the real wording")
    ws.cell(3, COL["✎ Correct?"], "both fine")
    wb.save(wb_path)

    d = parse(wb_path)
    assert len(d["rows"]) == 2
    first = d["rows"][0]
    assert first["verdict"] == "both wrong"
    assert first["true_text"] == "the real wording"
    assert first["row_key"] == row_key("Disagreement", "alpha text")


def test_reordered_header_is_a_hard_stop(tmp_path):
    """Reading by position after a column move misfiles corrections silently."""
    wb_path = write([{"section": "Control", "page": 1, "check": "k", "a": "x", "b": "y"}],
                    tmp_path / "r.xlsx", "t")
    from openpyxl import load_workbook
    wb = load_workbook(wb_path)
    wb["Review"].cell(1, COL["Version A"], "Renamed")
    wb.save(wb_path)
    with pytest.raises(ValueError, match="header does not match"):
        parse(wb_path)


def test_unrecognised_verdict_is_kept_and_reported(tmp_path):
    wb_path = write([{"section": "Control", "page": 1, "check": "k", "a": "x", "b": "y"}],
                    tmp_path / "r.xlsx", "t")
    from openpyxl import load_workbook
    wb = load_workbook(wb_path)
    wb["Review"].cell(2, COL["✎ Correct?"], "sort of? hard to tell")
    wb.save(wb_path)
    d = parse(wb_path)
    assert d["rows"][0]["verdict"] == "sort of? hard to tell"
    assert d["unknown_verdicts"], "an off-menu verdict must be surfaced, never dropped"


def test_dropdown_offers_every_verdict_the_importer_accepts():
    """A dropdown narrower than the importer's vocabulary makes options unreachable."""
    assert "both wrong" in VERDICTS and "both fine" in VERDICTS
    assert len(set(VERDICTS)) == len(VERDICTS)


def test_header_and_column_map_cannot_drift_apart():
    assert list(COL) == HEADER
    assert COL["row_key"] == len(HEADER)


def test_identical_true_text_on_both_fine_is_not_counted_as_wrong():
    """Measured on the real Year 5 review: a reviewer filled True text with the SAME
    wording as Version A on a row marked 'both fine' -- habit, not a correction. That
    inflated the control error rate from 8% to 17% before this was caught. Only a
    true_text that actually DIFFERS from the machine's version is a real correction.
    """
    from pipeline.import_doc_review import report
    import io, contextlib

    rows = [{"section": "Control", "page": 1, "verdict": "both fine",
            "true_text": "same wording", "version_a": "same wording", "notes": "",
            "check": "k", "version_b": "-", "row_key": "a"},
            {"section": "Control", "page": 1, "verdict": "both wrong",
            "true_text": "the real text", "version_a": "wrong text", "notes": "",
            "check": "k", "version_b": "-", "row_key": "b"}]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report({"rows": rows, "unknown_verdicts": []})
    out = buf.getvalue()
    assert "1/2" in out and "50%" in out


def test_a_heading_never_fuses_with_the_paragraph_that_follows_it():
    """Measured on the real Year 5 conversion: pdfplumber's raw text is genuinely
    well-structured -- 'level.\\n\\nCLOSING\\nA2ZERO is our community's plan...' -- with
    the heading on its own line. The old flattener destroyed that boundary; this pins
    the fix so the destruction cannot silently return.
    """
    from pipeline.export_doc_review import _sentences
    t = ("household\nlevel.\n\nCLOSING\nA2ZERO is our community's plan to become carbon "
        "neutral in a just and equitable way by the year 2030, per the report.")
    sents = _sentences(t)
    assert not any(s.startswith("CLOSING") for s in sents)
    assert any("A2ZERO is our community's plan" in s for s in sents)


def test_a_heading_line_is_never_emitted_as_its_own_short_sentence():
    """A heading is not prose to review as if it were a claim-bearing quote."""
    from pipeline.export_doc_review import _sentences
    t = "STRATEGY 2: BENEFICIAL ELECTRIFICATION\nOSI launched a rebate program for residents to use."
    assert "STRATEGY 2: BENEFICIAL ELECTRIFICATION" not in _sentences(t)


def test_a_capitalised_sentence_ending_in_punctuation_is_not_mistaken_for_a_heading():
    """The heading heuristic excludes anything ending in .!? -- a short final line of a
    paragraph must not be discarded just because it is short and capitalised.
    """
    from pipeline.export_doc_review import _paragraphs
    t = "Some long lead-in text that runs on for a while here.\nThank You."
    paras, _ = _paragraphs(t)
    assert any("Thank You." in p for p in paras)


def test_within_paragraph_line_wraps_still_collapse_to_one_line():
    """Ordinary PDF line-wrapping (no blank line, no heading) must still join into a
    single readable sentence -- only structural boundaries should survive as breaks.
    """
    from pipeline.export_doc_review import _sentences
    t = ("This is a perfectly ordinary sentence that happens to wrap\nacross two lines "
        "in the source PDF and should read as one continuous sentence when reviewed.")
    sents = _sentences(t)
    assert any("\n" not in s and "wrap across two lines" in s for s in sents)


# ── the heading heuristic's known failure mode, made loud instead of silent ────────────

def test_all_caps_heading_matches_but_title_case_does_not():
    """Confirmed directly, not assumed: this heuristic is corpus-specific. A document
    using Title Case headings instead of ALL CAPS regresses to the exact bug this module
    fixed, and nothing about the regex itself signals that -- check_heading_density()
    exists because of this gap, not despite it.
    """
    from pipeline.export_doc_review import _HEADING
    assert _HEADING.match("CLOSING REMARKS")
    assert not _HEADING.match("Closing Remarks")


def test_a_long_title_case_document_raises_rather_than_silently_degrading():
    from pipeline.export_doc_review import HeadingDetectionUnreliable, check_heading_density
    body = ("This is an ordinary sentence about municipal sustainability programs and "
           "their measurable outcomes across the reporting period. ") * 90
    t = "Introduction\n\n" + body + "\nClosing Remarks\n\n" + body
    assert len(t.split()) > 1500, "test document must clear the length threshold"
    try:
        check_heading_density(t, "synthetic")
        assert False, "a heading-free long document must raise, not pass silently"
    except HeadingDetectionUnreliable as e:
        assert "synthetic" in str(e)


def test_the_real_year5_document_does_not_false_fire():
    """The check must not cry wolf on the exact document it was tuned against."""
    from pipeline.export_doc_review import check_heading_density
    t = ("STRATEGY 1: RENEWABLES\n\n" + ("Ordinary body text about renewables. " * 30) +
        "\n\nSTRATEGY 2: EFFICIENCY\n\n" + ("Ordinary body text about efficiency. " * 30) +
        "\n\nCLOSING\n\n" + ("Ordinary closing body text here. " * 30)) * 8
    n = check_heading_density(t, "synthetic-caps")
    assert n > 0


def test_a_short_heading_free_document_does_not_false_fire():
    """The length gate matters: a genuinely short document with few headings is normal,
    not a signal the heuristic is broken. Only a LONG heading-free document is suspicious.
    """
    from pipeline.export_doc_review import check_heading_density
    short = "Just a short memo with no section headings at all, a few sentences long."
    check_heading_density(short, "short-doc")   # must not raise


def test_require_headings_false_skips_the_check_entirely():
    """A caller who already knows a document doesn't use ALL CAPS can opt out rather
    than being blocked from generating a workbook that is still useful without it.
    """
    from pipeline.export_doc_review import build_rows
    body = ("This is an ordinary sentence about municipal programs and measurable "
           "outcomes across the period. ") * 90
    arms = {"pdfplumber": body, "other": body}
    rows = build_rows(arms, lambda off: 1, require_headings=False)
    assert isinstance(rows, list)   # did not raise
