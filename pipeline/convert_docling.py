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
# The classifier's COMPLETE vocabulary, read from the model's own config.json
# (docling-project/DocumentFigureClassifier-v2.5, id2label). It is here so the allowlist
# below can be checked against it: two of the original eight entries -- "scatter_chart"
# and "map" -- were names this model never emits ("scatter_plot", "geographical_map"),
# so they could not have matched a picture on any document ever processed. A label
# allowlist fails silently by construction, always in the direction of extracting
# nothing, and Year 1's A2ZERO strategy infographic -- a dense, bespoke, text-bearing
# illustration -- classified 'geographical_map' and was skipped because of it.
CLASSIFIER_CLASSES = {
    "bar_chart", "bar_code", "box_plot", "calendar", "chemistry_structure",
    "crossword_puzzle", "engineering_drawing", "flow_chart", "full_page_image",
    "geographical_map", "icon", "line_chart", "logo", "music", "other",
    "page_thumbnail", "photograph", "pie_chart", "qr_code", "scatter_plot",
    "screenshot_from_computer", "screenshot_from_manual", "signature", "stamp",
    "table", "topographical_map",
}

# MAP LABELS ARE DELIBERATELY ABSENT, and this is a ruling with evidence behind it.
# Year 1 page 1's only map-labelled picture is not a map: it is a hand-drawn A2ZERO
# illustration, and the classifier reached for the nearest class it had. Sent to vision
# it came back "substantive" with a data point of 40% for vehicle-miles reduction --
# hand-lettered, correctly read, and contradicting the report's own Strategy 4 heading of
# "at least 50%". Extracting artwork therefore does not merely waste a call; it
# manufactures a number that disagrees with the prose on the same document, sourced from
# a drawing. Add a map class back only alongside a real one -- a choropleth, a site plan
# -- with a document to test it against.
CLASSIFY_LABELS = {"bar_chart", "pie_chart", "line_chart", "scatter_plot", "box_plot",
                   "flow_chart", "table",
                   "screenshot_from_computer", "screenshot_from_manual"}
assert CLASSIFY_LABELS <= CLASSIFIER_CLASSES, (
    f"not real classifier labels: {sorted(CLASSIFY_LABELS - CLASSIFIER_CLASSES)}")


def choose_ocr_engine(is_macos: bool | None = None,
                      ocrmac_available: bool | None = None,
                      requested: str | None = None) -> str:
    """Which OCR engine to use. Speed and availability only -- never correctness.

    Benchmarked on Year 2, the corpus's image-based document, against the human-healed
    reference. At FULL PAGE the two local engines are indistinguishable on quality: both
    recover all three previously-truncated grant bullets character-identically, both end
    with zero truncated blocks, and both reproduce all 15 currency figures exactly.

        ocrmac/full-page     24.0s
        rapidocr/full-page   60.4s

    So the choice is availability: ocrmac wraps the macOS Vision framework and needs both
    a Mac and the pip wrapper; rapidocr is pure python and runs anywhere. The wrapper can
    INSTALL on other platforms while the framework cannot exist there, so the platform
    check is separate from the import check.

    AZURE CU IS NOT AN AUTOMATIC FALLBACK, despite being fastest at 6.8s. A page-by-page
    side-by-side puts its real text at 3,461 words against ocrmac's 3,110 and the human
    reference's 3,108 -- and page 6 ALONE, an ENERGY STAR Portfolio Manager screenshot,
    accounts for +196 of that gap. CU reads text inside images; on Year 2 that is 233
    token occurrences at 37% dictionary words ("aaid", "abost", "arnage"), none of them
    in the human reference. Garbled screenshot chrome, not recovered prose, and mixing it
    into a citation spine is worse than omitting it -- image content is the
    vision-extraction path's job, where it arrives as typed points with per-value
    confidence. So CU is an explicit opt-in for bulk runs wanting the speed.

    CREDIT WHERE IT IS DUE, and a correction: CU semantically classifies page furniture,
    emitting the recurring "For more information ... please contact <staff>" block as
    <!-- PageFooter: ... -->. Three successive analyses of mine stripped HTML comments
    before counting and therefore DELETED 111 words of real CU text, making it look like
    CU had dropped the staff contact blocks when it had merely labelled them. ocrmac
    keeps that text inline and does not distinguish it from body prose. Neither behaviour
    is wrong; they are different, and only CU's is machine-actionable.
    """
    if requested:
        return requested
    if is_macos is None:
        import platform
        is_macos = platform.system() == "Darwin"
    if ocrmac_available is None:
        try:
            import ocrmac  # noqa: F401
            ocrmac_available = True
        except Exception:
            ocrmac_available = False
    return "ocrmac" if (is_macos and ocrmac_available) else "rapidocr"


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


def _block_record(kind: str, page_no: int, bbox: tuple[float, float, float, float],
                  coord_origin: str, page_w: float, page_h: float, text: str,
                  self_ref: str, caption_refs) -> dict:
    """One text block's kind, geometry and reading-order position -- what
    convert_blocks.py crops pdfplumber against.

    `caption_refs` maps a caption's self_ref -> the picture ref it belongs to. Confirmed
    on the real Year 5 document: all 13 picture captions ALSO appear in the main item
    stream as ordinary TextItems, indistinguishable from body prose by kind or geometry.
    Without this tag they render as stray sentences stranded where their photo used to
    be -- which is exactly what the human review caught ("This is a photo caption to a
    photo that was dropped"). Tagging them here is what lets the renderer drop a caption
    with its dropped picture, or fold it into a kept picture's description.

    Docling's own reading of the block is carried alongside, even though pdfplumber
    supplies the characters, so a disagreement between the two is detectable rather than
    invisible.
    """
    l, t, r, b = bbox
    return {
        "kind": kind,
        "page_no": page_no,
        "bbox": [l, t, r, b],
        "coord_origin": coord_origin,
        "page_w": page_w,
        "page_h": page_h,
        "self_ref": self_ref,
        "caption_for": (caption_refs or {}).get(self_ref) if hasattr(caption_refs, "get") else None,
        "docling_text": text,
    }


def _table_cells(item) -> list[dict]:
    """A table's cells as plain dicts: grid position, span, text, and the cell's own box.

    WHY A TABLE CANNOT RIDE THE TEXT BRANCH. The block pass keys on `item.text`, and a
    TableItem has none -- its content lives in `data.table_cells`. So every table Docling
    found fell through both branches and was dropped before blocks.json was written. The CAP
    has 536 rows of detected table and carried zero TableItem blocks; the Action Summary
    Table on page 10 reached the renderer only as loose words swept off the page, with its
    columns interleaved into unreadable prose.

    THE BBOX IS THE POINT. Each cell brings its own box, so a table is not a special case for
    this pipeline at all -- it is a grid of small crops, and the same rule applies inside it
    as everywhere else: Docling proposes the cell boundaries and the reading order of the
    grid, pdfplumber supplies the characters within each one. Docling's own cell text is
    carried alongside for the same reason a block's is, so disagreement stays detectable.
    """
    data = getattr(item, "data", None)
    cells = []
    for c in (getattr(data, "table_cells", None) or []):
        bb = getattr(c, "bbox", None)
        cells.append({
            "row": int(getattr(c, "start_row_offset_idx", 0)),
            "col": int(getattr(c, "start_col_offset_idx", 0)),
            "row_span": int(getattr(c, "row_span", 1) or 1),
            "col_span": int(getattr(c, "col_span", 1) or 1),
            "is_header": bool(getattr(c, "column_header", False)),
            "docling_text": getattr(c, "text", "") or "",
            "bbox": [bb.l, bb.t, bb.r, bb.b] if bb is not None else None,
            "coord_origin": str(getattr(bb, "coord_origin", "")) if bb is not None else None,
        })
    return cells


def build_converter(chart_extraction: bool = True, classification: bool = True,
                    ocr_engine: str | None = None):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions, TableStructureOptions, TableFormerMode,
    )

    from docling.datamodel.pipeline_options import RapidOcrOptions, OcrMacOptions

    o = PdfPipelineOptions()
    o.generate_page_images = True        # required by chart extraction

    # FULL-PAGE OCR IS NOT OPTIONAL. Benchmarked on Year 2: by default Docling OCRs only
    # the regions its layout model proposes, so a region clipped short takes the text
    # with it -- 26 blocks ended mid-sentence and all three of the grant table's wrapped
    # continuation lines were lost. At full page both local engines recover them
    # character-identically to the human-healed reference, and rapidocr is also FASTER
    # that way (60.4s against 86.5s), so the setting costs nothing.
    engine = choose_ocr_engine(requested=ocr_engine)
    if engine == "ocrmac":
        o.ocr_options = OcrMacOptions(force_full_page_ocr=True)
    else:
        # backend="torch": RapidOcrOptions defaults to onnxruntime, which is absent here,
        # while Docling's OcrAutoOptions picks torch. Constructing options explicitly
        # therefore fails where the default path works.
        o.ocr_options = RapidOcrOptions(force_full_page_ocr=True, backend="torch")
    o.do_ocr = True
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

    print(f"[docling] OCR engine: {engine} (full-page)", flush=True)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=o)})


def run(pdf: Path, out: Path, chart_extraction: bool = True,
        classification: bool = True, ocr_engine: str | None = None) -> dict:
    conv = build_converter(chart_extraction=chart_extraction,
                           classification=classification, ocr_engine=ocr_engine)
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
    from docling_core.types.doc.document import PictureItem, TableItem

    # (page_no -> (width, height)) so a picture's bbox can be turned into an area
    # fraction of the page it actually sits on, not some document-wide average.
    pages = {p.page_no: (p.size.width, p.size.height) for p in doc.pages.values()}

    # Materialised once: the stream is walked twice (captions first, then blocks) and
    # iterate_items() is a generator, so re-iterating it would silently yield nothing.
    items = [it for it, _ in doc.iterate_items()]

    # caption self_ref -> the picture it captions. Built BEFORE the block pass because a
    # caption appears in the stream as an ordinary TextItem, so it can only be recognised
    # by a ref its picture declares.
    caption_refs: dict[str, str] = {}
    for item in items:
        if isinstance(item, PictureItem):
            for c in (item.captions or []):
                ref = getattr(c, "cref", None) or str(c)
                caption_refs[ref] = item.self_ref

    charts, chart_cells, chart_text = 0, 0, []
    labels: dict[str, int] = {}
    pictures: list[dict] = []
    blocks: list[dict] = []
    for item in items:
        cls = getattr(getattr(item, "meta", None), "classification", None)
        _count_top_label(cls, labels)
        kind = type(item).__name__
        prov = item.prov[0] if getattr(item, "prov", None) else None

        # Pictures ride in the SAME ordered stream as text. That is what makes figure
        # placement structural rather than guessed: the page 4 GHG chart sits after
        # exactly three page 4 paragraphs in Docling's reading order, which is precisely
        # where the human review said it belonged.
        if isinstance(item, PictureItem) and prov:
            pw, ph = pages.get(prov.page_no, (None, None))
            b = prov.bbox
            rec = _picture_record(prov.page_no, (b.l, b.t, b.r, b.b),
                                  str(b.coord_origin), pw, ph, cls)
            pictures.append(rec)
            blocks.append({**_block_record(kind, prov.page_no, (b.l, b.t, b.r, b.b),
                                           str(b.coord_origin), pw, ph, "",
                                           item.self_ref, caption_refs),
                           "top_label": rec["top_label"], "top_conf": rec["top_conf"],
                           "area_frac": rec["area_frac"],
                           "worth_extraction": rec["worth_extraction"]})
        elif isinstance(item, TableItem) and prov:
            pw, ph = pages.get(prov.page_no, (None, None))
            b = prov.bbox
            cells = _table_cells(item)
            blocks.append({**_block_record(kind, prov.page_no, (b.l, b.t, b.r, b.b),
                                           str(b.coord_origin), pw, ph, "",
                                           item.self_ref, caption_refs),
                           "n_rows": (max((c["row"] for c in cells), default=-1) + 1),
                           "n_cols": (max((c["col"] for c in cells), default=-1) + 1),
                           "cells": cells})
        elif prov and getattr(item, "text", ""):
            pw, ph = pages.get(prov.page_no, (None, None))
            b = prov.bbox
            blocks.append(_block_record(kind, prov.page_no, (b.l, b.t, b.r, b.b),
                                        str(b.coord_origin), pw, ph, item.text,
                                        item.self_ref, caption_refs))
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

    # The text spine for convert_blocks.py: blocks in reading order with the geometry
    # pdfplumber needs to crop each one.
    blocks_out = out.with_suffix(".blocks.json")
    blocks_out.write_text(json.dumps(
        {"n_pages": len(pages), "blocks": blocks}, indent=1))

    return {"seconds": round(elapsed, 1), "words": len(md.split()),
            "charts": charts, "chart_cells": chart_cells, "labels": labels,
            "pictures": pictures, "blocks": blocks, "n_pages": len(pages),
            "out": str(out), "pictures_out": str(pictures_out),
            "blocks_out": str(blocks_out)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Docling conversion for the bake-off")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-charts", action="store_true",
                    help="control arm: disable chart extraction to isolate its effect")
    ap.add_argument("--ocr-engine", choices=["ocrmac", "rapidocr"],
                    help="override automatic selection (macOS Vision if available, "
                         "else rapidocr). Both are equivalent in quality at full page.")
    ap.add_argument("--no-classify", action="store_true",
                    help="disable picture classification too (only meaningful with --no-charts)")
    a = ap.parse_args()
    import docling, importlib.metadata as m
    print(f"[docling] v{m.version('docling')}  charts={'off' if a.no_charts else 'ON'}  "
          f"classify={'off' if a.no_classify else 'ON'}", flush=True)
    r = run(Path(a.pdf), Path(a.out), chart_extraction=not a.no_charts,
            classification=not a.no_classify, ocr_engine=a.ocr_engine)
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
    n_tab = sum(1 for b in r["blocks"] if b.get("cells") is not None)
    n_cell = sum(len(b.get("cells") or []) for b in r["blocks"])
    print(f"[docling] {len(r['blocks'])} text/picture/table blocks "
          f"({n_tab} table(s), {n_cell} cell(s)) -> {r['blocks_out']}")
    print(f"[docling] wrote {r['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
