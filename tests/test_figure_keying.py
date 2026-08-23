"""Attaching vision output to the right picture when a page carries several.

render_blocks keyed figure_xml by PAGE, and said so: "a document with two data-bearing
figures on ONE page would need a finer key, which Year 5 does not exercise and which is not
invented here." The CAP exercises it. 55 substantive figures land on 38 pages, and 17 of them
-- 31% of everything the vision pass read -- were silently overwritten.

The visible symptom was page 117: two figures both classified `photograph`, one the ACTION
cost card reading $1,000,000 and one a snapshot of volunteers landscaping. The page rendered
neither.
"""
from __future__ import annotations

from pipeline.render_blocks import figure_key


def test_two_figures_on_one_page_get_different_keys():
    a = {"page_no": 117, "top_label": "photograph", "top_conf": 0.435}
    b = {"page_no": 117, "top_label": "photograph", "top_conf": 0.993}
    assert figure_key(a) != figure_key(b)


def test_the_same_figure_keys_the_same_from_either_side():
    """A PictureItem block and a figures.json record describe one picture and must agree."""
    block = {"page_no": 105, "top_label": "other", "top_conf": 0.401, "kind": "PictureItem"}
    record = {"page_no": 105, "top_label": "other", "top_conf": 0.401,
              "crop_path": "x.png", "xml": "<figure_description/>"}
    assert figure_key(block) == figure_key(record)


def test_different_pages_never_collide():
    a = {"page_no": 105, "top_label": "other", "top_conf": 0.4}
    b = {"page_no": 107, "top_label": "other", "top_conf": 0.4}
    assert figure_key(a) != figure_key(b)


def test_confidence_is_rounded_so_float_noise_does_not_split_a_pair():
    a = {"page_no": 1, "top_label": "pie_chart", "top_conf": 0.9160000001}
    b = {"page_no": 1, "top_label": "pie_chart", "top_conf": 0.916}
    assert figure_key(a) == figure_key(b)


def test_a_missing_label_or_confidence_still_yields_a_key():
    """An older figures.json may lack them; it must not raise."""
    assert figure_key({"page_no": 3}) == figure_key({"page_no": 3, "top_label": None,
                                                     "top_conf": None})


def test_two_figures_on_one_page_both_render():
    """The end-to-end guarantee: page 117's cost card and its decorative photo are both
    classified `photograph`, and both must reach the page."""
    from pipeline.render_blocks import render_blocks
    blocks = [
        {"kind": "PictureItem", "page_no": 117, "text": "", "self_ref": "#/pictures/1",
         "worth_extraction": True, "top_label": "photograph", "top_conf": 0.435,
         "bbox": (0, 0, 10, 10)},
        {"kind": "PictureItem", "page_no": 117, "text": "", "self_ref": "#/pictures/2",
         "worth_extraction": True, "top_label": "photograph", "top_conf": 0.993,
         "bbox": (0, 20, 10, 30)},
    ]
    xml = {figure_key(b): f"<figure_description>{b['top_conf']}</figure_description>"
           for b in blocks}
    out = render_blocks(blocks, xml, "T")
    assert "0.435" in out and "0.993" in out
