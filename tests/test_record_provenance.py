"""The binding between a document row and the bytes it was converted from."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline.record_provenance import Candidate, sha256_file, _key


def _cand(**kw):
    base = dict(document_id=1, pdf=Path("x.pdf"), pages=24, stored_pages=24,
                stored_title="A2ZERO YEAR FOUR ANNUAL REPORT",
                first_page="A2ZERO YEAR FOUR\nJULY 1, 2023", sha256="deadbeef")
    return Candidate(**{**base, **kw})


def test_a_matching_page_count_alone_is_not_enough():
    """Years 4 and 5 are BOTH 24 pages. A page count cannot tell them apart, so binding on
    it alone would silently attach every Year 5 claim to the Year 4 PDF."""
    c = _cand(stored_title="A2ZER0 Annual Report Year Five",
              first_page="A2ZERO YEAR FOUR\nJULY 1, 2023")
    assert c.pages_agree
    assert not c.title_agrees
    assert not c.ok


def test_title_match_ignores_case_and_punctuation():
    """The store holds 'YEAR THREE ANNUAL REPORT'; the PDF renders 'Year three Annual
    Report'. Same document, different string."""
    c = _cand(pages=16, stored_pages=16, stored_title="YEAR THREE ANNUAL REPORT",
              first_page="Year three Annual Report\nJULY 1, 2022-JUNE 3, 2023")
    assert c.ok


def test_a_null_stored_page_count_refuses_rather_than_passes():
    """Nothing to check against is not the same as checked."""
    c = _cand(stored_pages=None)
    assert not c.ok
    assert "NULL" in c.why_not


def test_refusal_says_which_check_failed():
    assert "page count" in _cand(pages=7).why_not
    assert "title" in _cand(first_page="something else entirely").why_not


def test_sha256_file_matches_hashlib(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"grapevine" * 1000)
    assert sha256_file(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_key_strips_everything_that_is_not_alphanumeric():
    assert _key("A2ZER0 Annual Report!") == "a2zer0annualreport"
    assert _key(None) == ""
