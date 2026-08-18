"""Docling backend for the text-path bake-off. Runs in its own arm64 conda env.

WHY A SEPARATE SCRIPT AND ENV. Docling pulls torch, transformers and mlx-vlm -- several
gigabytes that the rest of this pipeline neither needs nor should be pinned against. It
lives in `grapevine-docling` (conda-forge, python 3.12, native arm64), following the same
convention as grapevine-db and grapevine-media. The old docling_env in ../docling-test is
dead: empty except pip, with an interpreter symlink pointing at a path that no longer
exists after the project moved. Nothing here revives it.

    /opt/miniconda3/envs/grapevine-docling/bin/python -m pipeline.convert_docling --pdf x.pdf

TWO SETTINGS CARRY THIS WHOLE RUN, AND BOTH ARE OFF OR WRONG BY DEFAULT:

`do_chart_extraction` defaults to False. It is the capability the March pipeline did not
have -- Granite Vision reading bar/pie/line charts into real table cells at
`item.meta.tabular_chart`. On a corpus where 20-33 chart-sized figures per report have
never been mined, this is the entire reason to retest Docling at all.

`picture_area_threshold` defaults to 0.05, silently skipping any picture smaller than 5%
of page area. Measured on Year 2 page 13: 46 of 47 images fall below it, median area
0.0032. Left at the default, Docling would process one image on that page and ignore
forty-six -- including the ones holding the twelve grant amounts -- and report no error.
A default that discards content without erroring is this project's founding failure mode
wearing a config file, so it is set to 0.0 explicitly.

TableFormer stays on v1 ACCURATE. v2 exists but issue #3158 reports it parsing tables
incorrectly where v1 succeeded, and no maintainer resolution was found. Newer is not
evidence of better.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path


# Classification is what your Gemini-based DATA/JUNK step (docling-test/pipeline/
# 2_enrich_images.py) did by hand, with an external call per image. Docling ships an
# equivalent MODEL -- DocumentFigureClassifier-v2.5 -- that runs LOCALLY as part of the
# same conversion pass. Not the same thing as chart EXTRACTION: classification labels a
# picture's TYPE (chart, photo, logo...); extraction reads a chart's numbers into cells.
# They are independently controllable so triage can run without paying for extraction on
# every picture -- classify first, extract only what classification says is data-bearing.
CLASSIFY_LABELS = {"bar_chart", "pie_chart", "line_chart", "scatter_chart", "map",
                   "flow_chart", "table"}   # data-bearing kinds worth extracting


def _count_top_label(cls, labels: dict[str, int]) -> None:
    """Tally one picture's TOP classification only.

    `.predictions` is the FULL ranked probability distribution over ~26 classes, one
    entry per class, for EVERY picture -- confirmed by a raw dump on Year 2: each of the
    26 class names appeared exactly 13 times, matching 13 pictures, not 13 distinct
    labels. predictions[0] is the top guess (confidence-descending, verified against the
    raw values: 'other' at 0.48 led 'icon' at 0.27). An earlier version iterated the
    whole distribution and summed every class at once into one nonsense dict entry keyed
    on the full object repr.
    """
    if cls is None:
        return
    preds = getattr(cls, "predictions", None) or []
    if not preds:
        return
    top = preds[0]
    name = getattr(top, "class_name", None) or str(top)
    labels[name] = labels.get(name, 0) + 1


def build_converter(chart_extraction: bool = True, classification: bool = True):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions, TableStructureOptions, TableFormerMode,
    )

    o = PdfPipelineOptions()
    o.generate_page_images = True        # required by chart extraction
    o.generate_picture_images = True
    o.do_chart_extraction = chart_extraction
    o.do_picture_classification = classification or chart_extraction
    o.do_table_structure = True
    o.table_structure_options = TableStructureOptions(mode=TableFormerMode.ACCURATE)

    # THE SILENT-SKIP FIX. See module docstring: 46 of 47 images on the grant page fall
    # under the 0.05 default. Not every build exposes this attribute, so set it defensively
    # rather than crashing a long run on an AttributeError.
    for target in (o, getattr(o, "picture_description_options", None)):
        if target is not None and hasattr(target, "picture_area_threshold"):
            target.picture_area_threshold = 0.0

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=o)})


def run(pdf: Path, out: Path, chart_extraction: bool = True,
        classification: bool = True) -> dict:
    conv = build_converter(chart_extraction=chart_extraction, classification=classification)
    t0 = time.time()
    res = conv.convert(str(pdf))
    elapsed = time.time() - t0
    doc = res.document
    md = doc.export_to_markdown()

    # Chart data does NOT appear in export_to_markdown() -- it lives on the item metadata.
    # Pull it out explicitly, or the entire point of do_chart_extraction is invisible.
    # chart_data is a TableData object, NOT a list -- len() on it raises TypeError.
    # The first version did exactly that, and the crash was diagnostic: it can only
    # fire WHEN a chart exists, so "0 charts" on years 2 and 4 was real while year 5
    # actually found one and took the counter down with it. Extract the cells rather
    # than measuring the container.
    charts, chart_cells, chart_text = 0, 0, []
    labels: dict[str, int] = {}
    for item, _ in doc.iterate_items():
        _count_top_label(getattr(getattr(item, "meta", None), "classification", None), labels)
        tab = getattr(getattr(item, "meta", None), "tabular_chart", None)
        if tab is None:
            continue
        charts += 1
        data = getattr(tab, "chart_data", None)
        cells = getattr(data, "table_cells", None) or []
        chart_cells += len(cells)
        for c in cells:
            t = (getattr(c, "text", "") or "").strip()
            if t:
                chart_text.append(t)
    # Chart data does not appear in export_to_markdown(), so append it explicitly or
    # the entire capability stays invisible to every downstream consumer.
    if chart_text:
        md = md + "\n\n<!-- docling chart data -->\n" + " | ".join(chart_text)
    out.write_text(md)
    return {"seconds": round(elapsed, 1), "words": len(md.split()),
            "charts": charts, "chart_cells": chart_cells, "labels": labels, "out": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Docling conversion for the bake-off")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-charts", action="store_true",
                    help="control arm: disable chart extraction to isolate its effect")
    ap.add_argument("--no-classify", action="store_true",
                    help="disable picture classification too (only meaningful with --no-charts)")
    a = ap.parse_args()
    import docling, importlib.metadata as m
    print(f"[docling] v{m.version('docling')}  charts={'off' if a.no_charts else 'ON'}  "
          f"classify={'off' if a.no_classify else 'ON'}", flush=True)
    r = run(Path(a.pdf), Path(a.out), chart_extraction=not a.no_charts,
            classification=not a.no_classify)
    print(f"[docling] {r['seconds']}s  {r['words']:,} words  "
          f"{r['charts']} chart(s) with {r['chart_cells']} extracted cell(s)")
    if r["labels"]:
        print(f"[docling] picture classifications: {r['labels']}")
    print(f"[docling] wrote {r['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
