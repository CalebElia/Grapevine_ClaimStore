"""parse_points(): the one pure-logic piece of vision_extract.py, testable without a
live model call. Everything else in the module needs real credentials and a real image,
matching the pattern already used for convert_check.py's live-call functions.
"""
from __future__ import annotations

from pipeline.vision_extract import parse_points


def test_extracts_attributes_from_a_self_closing_point():
    xml = '<point label="New Solar" value="11.88" unit="MW" confidence="high"/>'
    pts = parse_points(xml)
    assert pts == [{"label": "New Solar", "value": "11.88", "unit": "MW",
                   "confidence": "high"}]


def test_extracts_multiple_points_in_document_order():
    xml = ('<data_points>'
          '<point label="a" value="1" unit="x" confidence="high"/>'
          '<point label="b" value="2" unit="y" confidence="low"/>'
          '</data_points>')
    pts = parse_points(xml)
    assert [p["label"] for p in pts] == ["a", "b"]


def test_no_points_in_plain_prose_is_an_empty_list_not_an_error():
    """The legacy prompt format has no <point> elements at all -- free-text bullets
    instead. Measured: 0 across both models under that prompt. Must not crash.
    """
    assert parse_points("<data_points>\n  - some free text bullet\n</data_points>") == []


def test_malformed_xml_degrades_gracefully():
    """Model output is not guaranteed well-formed. A regex-based parser should recover
    what it can rather than raising on a missing closing tag or stray text.
    """
    pts = parse_points('<point label="ok" value="1" unit="u" confidence="high">')
    assert pts == [{"label": "ok", "value": "1", "unit": "u", "confidence": "high"}]
