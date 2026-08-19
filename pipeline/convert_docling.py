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

PER-PICTURE PAGE/LOCATION RECORDS. `run()` also writes `<out>.pictures.json`: one row per
picture with page_no, bbox (Docling's own PDF-point space, BOTTOMLEFT origin -- confirmed
empirically, not assumed), area_frac relative to that page, and the classifier's top
label. This is the missing piece a crop-and-extract step needs -- classification alone
answers "what labels appear in this document," never "which specific image on which page
is worth a vision call." Measured on the real Year 5 PDF: 32 of 33 pictures clear a
5%-of-page-area threshold, and of those 32, exactly TWO carry a non-photograph,
non-logo label -- the page 4 bar_chart (0.997 confidence) and the page 5
screenshot_from_computer (0.543 confidence, the GHG dashboard). Both were independently
confirmed by hand as the only two data-bearing images in the report, so this is the
correct filter, not a guess at one.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


# Classification is what your Gemini-based DATA/JUNK step (docling-test/pipeline/
# 2_enrich_images.py) did by hand, with an external call per image. Docling ships an
# equivalent MODEL -- DocumentFigureClassifier-v2.5 -- that runs LOCALLY as part of the
# same conversion pass. Not the same thing as chart EXTRACTION: classification labels a
# picture's TYPE (chart, photo, logo...); extraction reads a chart's numbers into cells.
# They are independently controllable so triage can run without paying for extraction on
# every picture -- classify first, extract only what classification says is data-bearing.
# "screenshot_from_computer" is here because of a real miss, not by design: the Year 5
# GHG dashboard -- a genuine data-bearing image, hand-confirmed -- classifies as
# screenshot_from_computer at only 0.543 confidence, not as any chart label. A chart-type
# allowlist alone would have silently dropped the one image on this document most worth
# the vision-extraction cost it exists to gate.
CLASSIFY_LABELS = {"bar_chart", "pie_chart", "line_chart", "scatter_chart", "map",
                   "flow_chart", "table", "screenshot_from_computer"}


def _top_label(cls) -> tuple[str | None, float | None]:
    """One picture's top-ranked (class_name, confidence), or (None, None).

    `.predictions` is the FULL ranked probability distribution over ~26 classes, one
    entry per class, for EVERY picture -- confirmed by a raw dump on Year 2: each of the
    26 class names appeared exactly 13 times, matching 13 pictures, not 13 distinct
    labels. predictions[0] is the top guess (confidence-descending, verified against the
    raw values: 'other' at 0.48 led 'icon' at 0.27).
    """
    if cls is None:
        return None, None
    preds = getattr(cls, "predictions", None) or []
    if not preds:
        return None, None
    top = preds[0]
    name = getattr(top, "class_name", None) or str(top)
    return name, getattr(top, "confidence", None)


def _count_top_label(cls, labels: dict[str, int]) -> None:
    """Tally one picture's top classification. An earlier version iterated the WHOLE
    prediction distribution and summed every class at once into one nonsense dict entry
    keyed on the full object repr; see `_top_label` for the fix.
    """
    name, _ = _top_label(cls)
    if name is not None:
        labels[name] = labels.get(name, 0) + 1


def _picture_record(page_no: int, bbox: tuple[float, float, float, float],
                    coord_origin: str, page_w: float | None, page_h: float | None,
                    cls) -> dict:
    """One picture's page/location/classification -- the record a crop-and-extract step
    needs to answer "is this specific image on this specific page worth a vision call,"
    which a per-document label count cannot answer.

    bbox is (l, t, r, b) in Docling's own PDF-point coordinate space, not pixels -- a
    caller rendering the page at a chosen DPI must scale before cropping. coord_origin is
    carried through rather than assumed, since a wrong assumption here silently crops the
    wrong region rather than raising. page_w/page_h are carried through too, alongside the
    area_frac they already produce, so a later crop step reads page size from the same
    place area_frac came from rather than reopening the PDF through a second library and
    risking the two silently disagreeing.
    """
    l, t, r, b = bbox
    w, h = abs(r - l), abs(t - b)
    area_frac = (w * h) / (page_w * page_h) if page_w and page_h else None
    name, conf = _top_label(cls)
    return {
        "page_no": page_no,
        "bbox": [l, t, r, b],
        "coord_origin": coord_origin,
        "page_w": page_w,
        "page_h": page_h,
        "area_frac": round(area_frac, 4) if area_frac is not None else None,
        "top_label": name,
        "top_conf": round(conf, 3) if conf is not None else None,
        "worth_extraction": name in CLASSIFY_LABELS if name else False,
    }


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
    from docling_core.types.doc.document import PictureItem

    # (page_no -> (width, height)) so a picture's bbox can be turned into an area
    # fraction of the page it actually sits on, not some document-wide average.
    pages = {p.page_no: (p.size.width, p.size.height) for p in doc.pages.values()}

    charts, chart_cells, chart_text = 0, 0, []
    labels: dict[str, int] = {}
    pictures: list[dict] = []
    for item, _ in doc.iterate_items():
        cls = getattr(getattr(item, "meta", None), "classification", None)
        _count_top_label(cls, labels)
        if isinstance(item, PictureItem) and item.prov:
            prov = item.prov[0]
            pw, ph = pages.get(prov.page_no, (None, None))
            b = prov.bbox
            pictures.append(_picture_record(
                prov.page_no, (b.l, b.t, b.r, b.b), str(b.coord_origin), pw, ph, cls))
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

    # Markdown has nowhere to put a page/bbox/classification record; a JSON sidecar next
    # to it is where a crop-and-extract step will look.
    pictures_out = out.with_suffix(".pictures.json")
    pictures_out.write_text(json.dumps(pictures, indent=2))

    return {"seconds": round(elapsed, 1), "words": len(md.split()),
            "charts": charts, "chart_cells": chart_cells, "labels": labels,
            "pictures": pictures, "out": str(out), "pictures_out": str(pictures_out)}


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
    worth = [p for p in r["pictures"] if p["worth_extraction"]]
    print(f"[docling] {len(r['pictures'])} picture(s), {len(worth)} worth extraction "
          f"-> wrote {r['pictures_out']}")
    for p in worth:
        print(f"[docling]   page {p['page_no']}: {p['top_label']} "
              f"(conf {p['top_conf']}, {p['area_frac']:.1%} of page)")
    print(f"[docling] wrote {r['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
