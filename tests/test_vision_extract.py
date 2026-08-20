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


# ── relevance triage: ornamental by CONTENT, not by size ───────────────────────────────
# Year 2 page 6 carries a screenshot of the ENERGY STAR Portfolio Manager interface. The
# classifier calls it screenshot_from_computer -- the same label Year 5's A2ZERO dashboard
# earns, and that one yielded 31 real data points. Nothing about type, size or confidence
# separates them: Year 2's is 10.3% of the page at 0.481, Year 5's is 32.3% at 0.543.
#
# What separates them is CONTENT. Year 2's is a picture of a TOOL a reader could go and
# use, carrying no Ann Arbor data at all -- ornamental in exactly the way a stock
# ribbon-cutting photo is ornamental. Only something that looks at the image can tell,
# and the vision pass is already looking, so the triage belongs in its prompt rather than
# in another geometric threshold.

from pipeline.vision_extract import parse_relevance


def test_a_generic_tool_screenshot_is_reported_as_ornamental():
    xml = """<figure_description>
      <relevance>ornamental</relevance>
      <relevance_reason>A screenshot of the ENERGY STAR Portfolio Manager web interface.
      No values specific to Ann Arbor appear; this illustrates a tool.</relevance_reason>
      <data_points></data_points>
    </figure_description>"""
    r = parse_relevance(xml)
    assert r["relevance"] == "ornamental"
    assert "Portfolio Manager" in r["reason"]


def test_a_subject_specific_chart_is_reported_as_substantive():
    xml = """<figure_description>
      <relevance>substantive</relevance>
      <relevance_reason>A bar chart of Ann Arbor community GHG emissions by year.</relevance_reason>
    </figure_description>"""
    assert parse_relevance(xml)["relevance"] == "substantive"


def test_a_response_with_no_relevance_element_is_unknown_not_assumed_substantive():
    """An older prompt's output must not be silently treated as having passed triage."""
    assert parse_relevance("<figure_description><type>bar_chart</type>"
                           "</figure_description>")["relevance"] == "unknown"


def test_the_prompt_defines_ornamental_by_subject_specificity_not_by_beauty():
    from pipeline.vision_extract import XML_PROMPT
    low = XML_PROMPT.lower()
    assert "ornamental" in low and "substantive" in low
    assert "stock" in low or "tool" in low
