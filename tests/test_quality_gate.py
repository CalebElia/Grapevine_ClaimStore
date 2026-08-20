"""The gate that makes a bad conversion fail instead of reporting success.

WHY THIS EXISTS AT ALL. Every failure this corpus has produced was SILENT. pdfplumber
returned 234 words and zero of Year 2's 16 dollar figures, exited cleanly, and raised
nothing. The block pipeline reproduced that exactly -- same 234 words, same zero figures,
same success message -- until a human asked for the test. Reporting a number is not the
same as refusing to proceed on it.

CALIBRATED AGAINST THREE REAL DOCUMENTS, deliberately unalike:
    Year 2   14 pages, image-based, 94% OCR, 2,862 words, 15 dollar figures
    Year 4   24 pages, clean text layer, 0% OCR, 5,616 words, 10 dollar figures
    Year 5   24 pages, clean text layer, 0% OCR, 7,114 words, 10 dollar figures
A gate that only passes documents shaped like Year 5 would be useless on the corpus this
pipeline is for.
"""
from __future__ import annotations

from pipeline.quality_gate import Finding, assess, verdict


def _ok_conv(words=5000, pages=24):
    text = " ".join(f"word{i}" for i in range(words))
    per = max(len(text) // pages, 1)
    page_map = [(i + 1, i * per, min((i + 1) * per, len(text))) for i in range(pages)]
    return text, page_map


# ── the check that would have caught the original failure ──────────────────────────────

def test_a_conversion_far_shorter_than_an_independent_read_is_a_high_finding():
    """The exact Year 2 signature: 234 words assembled against 2,995 available."""
    text, page_map = _ok_conv(words=234, pages=14)
    f = assess(text, page_map, blocks=[], reference_words=2995, reference_numbers=set())
    rec = [x for x in f if x.check == "text_recovery"]
    assert rec and rec[0].severity == "high"
    assert "8%" in rec[0].evidence or "7%" in rec[0].evidence


def test_a_conversion_matching_its_reference_passes():
    text, page_map = _ok_conv(words=2862, pages=14)
    f = assess(text, page_map, blocks=[], reference_words=2995, reference_numbers=set())
    assert not [x for x in f if x.check == "text_recovery" and x.severity == "high"]


def test_missing_dollar_figures_are_a_high_finding():
    """Year 2 lost all 16. A number present in an independent read and absent from the
    output is the single most citable thing a parse can drop."""
    text, page_map = _ok_conv()
    f = assess(text, page_map, blocks=[], reference_words=5000,
               reference_numbers={"$1.5 million", "$15 million", "$2.5 million"})
    num = [x for x in f if x.check == "numeric_conservation"]
    assert num and num[0].severity == "high"


def test_numbers_present_in_the_output_do_not_fire():
    text = "We secured $2.5 million and later $15 million for the programme."
    f = assess(text, [(1, 0, len(text))], blocks=[], reference_words=11,
               reference_numbers={"$2.5 million", "$15 million"})
    assert not [x for x in f if x.check == "numeric_conservation"]


# ── structural sanity ──────────────────────────────────────────────────────────────────

def test_a_block_whose_span_does_not_slice_back_is_a_high_finding():
    """The offsets ARE the citation. If text[start:end] is not the block, every claim
    anchored to it points somewhere else."""
    text, page_map = _ok_conv()
    bad = [{"kind": "TextItem", "page_no": 1, "text": "not what is there",
            "char_start": 0, "char_end": 10}]
    f = assess(text, page_map, blocks=bad, reference_words=5000, reference_numbers=set())
    assert [x for x in f if x.check == "span_round_trip" and x.severity == "high"]


def test_correct_spans_do_not_fire():
    text = "STRATEGY 1: RENEWABLES\n\nBody prose here."
    blocks = [{"kind": "SectionHeaderItem", "page_no": 1, "text": "STRATEGY 1: RENEWABLES",
               "char_start": 0, "char_end": 22}]
    f = assess(text, [(1, 0, len(text))], blocks=blocks, reference_words=6,
               reference_numbers=set())
    assert not [x for x in f if x.check == "span_round_trip"]


def test_a_non_monotonic_page_map_is_a_high_finding():
    text, _ = _ok_conv()
    bad_map = [(1, 0, 100), (2, 50, 200)]        # page 2 starts before page 1 ends
    f = assess(text, bad_map, blocks=[], reference_words=5000, reference_numbers=set())
    assert [x for x in f if x.check == "page_map" and x.severity == "high"]


# ── OCR is a grade, not a failure ──────────────────────────────────────────────────────

def test_a_heavily_ocr_document_is_flagged_but_not_failed():
    """Year 2 is 94% OCR and is nonetheless the best reading of it that exists. The
    pipeline must record that its text is not character-exact WITHOUT refusing a
    document whose only sin is being a scan."""
    text, page_map = _ok_conv(words=2862, pages=14)
    # spans that genuinely slice back, so this test isolates the OCR question rather
    # than tripping span_round_trip on a careless fixture
    blocks = [{"kind": "TextItem", "page_no": 1, "text": text[i * 6:i * 6 + 5],
               "char_start": i * 6, "char_end": i * 6 + 5,
               "text_source": "docling_ocr"} for i in range(94)]
    blocks += [{"kind": "TextItem", "page_no": 1, "text": text[i * 6:i * 6 + 5],
                "char_start": i * 6, "char_end": i * 6 + 5,
                "text_source": "pdfplumber"} for i in range(94, 100)]
    f = assess(text, page_map, blocks=blocks, reference_words=2995,
               reference_numbers=set())
    ocr = [x for x in f if x.check == "ocr_fraction"]
    assert ocr and ocr[0].severity == "medium"
    assert verdict(f) != "refuse", "a scanned document is not a broken one"


# ── the verdict ────────────────────────────────────────────────────────────────────────

def test_any_high_finding_refuses():
    assert verdict([Finding("text_recovery", "high", "8% of reference")]) == "refuse"


def test_medium_findings_warn_but_pass():
    assert verdict([Finding("ocr_fraction", "medium", "94% OCR")]) == "review"


def test_a_clean_document_passes():
    assert verdict([]) == "pass"


# ── truncation: the failure class the first four checks cannot see ─────────────────────
# Found by comparing Year 2's output against the human-healed reference. All 11 distinct
# grant amounts were recovered exactly -- but three bullets end mid-sentence, because
# RapidOCR dropped the continuation lines of the wrapped entries:
#     "$2,500,000 from the federal government ... improvements at"   [Ann Arbor Housing
#                                                                     Commission sites]
#     "$270,000 from the American Lung Association to support the purchase of"
#     "$406,000 from the State of Michigan to support the purchase of another"
# The gate PASSED that document: every currency figure was present and word recovery was
# 96%, so neither check moved. Three short tails are invisible to volume-based measures
# and highly visible to a reader -- and a claim quoted from a truncated bullet would be
# verbatim-correct and factually incomplete.

def test_a_block_ending_on_a_dangling_preposition_is_flagged():
    blocks = [{"kind": "ListItem", "page_no": 13, "char_start": 0, "char_end": 5,
               "text": "$270,000 from the American Lung Association to support the "
                       "purchase of"}]
    text = "hello"
    f = assess(text, [(1, 0, 5)], blocks=blocks, reference_words=1,
               reference_numbers=set())
    trunc = [x for x in f if x.check == "truncation"]
    assert trunc and trunc[0].severity == "medium"


def test_a_complete_sentence_is_not_flagged():
    blocks = [{"kind": "ListItem", "page_no": 13, "char_start": 0, "char_end": 5,
               "text": "$75,000 from the McKnight Foundation to support work in Bryant."}]
    f = assess("hello", [(1, 0, 5)], blocks=blocks, reference_words=1,
               reference_numbers=set())
    assert not [x for x in f if x.check == "truncation"]


def test_a_bullet_with_no_terminal_punctuation_but_a_real_last_word_is_not_flagged():
    """Many bullets in this corpus simply omit the full stop; that alone is not
    truncation. Only a dangling function word is."""
    blocks = [{"kind": "ListItem", "page_no": 13, "char_start": 0, "char_end": 5,
               "text": "$25,000 from the U.S. EPA to support work in Bryant"}]
    f = assess("hello", [(1, 0, 5)], blocks=blocks, reference_words=1,
               reference_numbers=set())
    assert not [x for x in f if x.check == "truncation"]


def test_a_heading_is_never_treated_as_truncated():
    blocks = [{"kind": "SectionHeaderItem", "page_no": 5, "char_start": 0, "char_end": 5,
               "text": "STRATEGY 1: 100% RENEWABLES"}]
    f = assess("hello", [(1, 0, 5)], blocks=blocks, reference_words=1,
               reference_numbers=set())
    assert not [x for x in f if x.check == "truncation"]


# ── a finding must carry the things it found ───────────────────────────────────────────
# Reported during review: the checklist said "2 block(s) end on a dangling word" and then
# showed ONE example, whose snippet -- text[-58:] -- began mid-word ("...customers" cut to
# "rs to have more control..."), so it could not be found on the page it named. A summary
# string is fine for a console line and useless for a work item.

def test_a_truncation_finding_carries_every_offending_block():
    blocks = [{"kind": "ListItem", "page_no": 2, "char_start": 0, "char_end": 5,
               "text": "allowing customers to have more control over where their "
                       "energy comes from"},
              {"kind": "TextItem", "page_no": 4, "char_start": 0, "char_end": 5,
               "text": "Introduced updates to our permitting system to better track our"}]
    f = [x for x in assess("hello", [(1, 0, 5)], blocks=blocks, reference_words=1,
                           reference_numbers=set()) if x.check == "truncation"]
    assert f and len(f[0].items) == 2
    assert {i["page_no"] for i in f[0].items} == {2, 4}


def test_findings_without_per_item_detail_default_to_an_empty_list():
    assert Finding("ocr_fraction", "medium", "94% OCR").items == []
