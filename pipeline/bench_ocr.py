"""Benchmark OCR configurations on an image-based PDF, scored against known truncations.

WHY THIS IS MEASURABLE NOW. Year 2's three truncated grant bullets give a concrete target
that a word count cannot express: RapidOCR's default configuration drops the continuation
lines of wrapped list items, so "$270,000 from the American Lung Association to support
the purchase of" simply stops. The human-healed reference supplies the ground truth those
tails should reach. Any OCR configuration can therefore be scored on whether it recovers
them, rather than on a subjective read of its output.

THE SCORE IS NOT WORD COUNT. An OCR engine that hallucinates fluent text scores well on
volume and badly on truth. The metrics here are: how many currency figures survive (the
most citable content), how many blocks end mid-sentence (the failure being hunted), and
whether three specific known-missing phrases come back.

    /opt/miniconda3/envs/grapevine-docling/bin/python -m pipeline.bench_ocr --pdf x.pdf
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

# The three tails RapidOCR's default configuration drops on Year 2, verified absent from
# Docling's own markdown and present in the human-healed reference.
KNOWN_MISSING = [
    "Ann Arbor Housing Commission sites",
    "refuse truck",
    "limited to",
]

_MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?", re.I)
_DANGLING = {
    "a", "an", "and", "at", "but", "by", "for", "from", "in", "including", "into", "of",
    "on", "or", "our", "the", "their", "to", "with", "another", "not", "as", "that",
}


def score(text: str, blocks: list[dict]) -> dict:
    """Metrics that distinguish a better READ from a merely longer one.

    Truncation is counted per BLOCK with internal wraps joined, never per LINE. Scoring
    line-by-line penalised Azure CU for 97 phantom truncations simply because it preserves
    the PDF's visual line breaks -- every one of which the pipeline flattens anyway. That
    was a defect in the metric, not in the read, and it would have ranked the fastest
    complete engine last.
    """
    trunc = 0
    for b in blocks:
        t = " ".join((b.get("text") or "").split())
        if not t or t[-1] in ".!?:;’\"')":
            continue
        if t.split()[-1].lower().strip(",") in _DANGLING:
            trunc += 1
    return {
        "words": len(text.split()),
        "money": len({m.group(0) for m in _MONEY.finditer(text)}),
        "truncated_blocks": trunc,
        "tails_recovered": sum(1 for k in KNOWN_MISSING if k in text),
    }


def build(engine: str, model: str | None, full_page: bool):
    """A converter for one OCR configuration.

    force_full_page_ocr is the setting most likely to matter for truncation: by default
    Docling OCRs the regions its layout model proposes, so a region clipped short takes
    the text with it. Full-page OCR removes the layout model from that decision.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions, TableStructureOptions, TableFormerMode,
        RapidOcrOptions, EasyOcrOptions, OcrMacOptions, TesseractOcrOptions,
    )

    o = PdfPipelineOptions()
    o.do_ocr = True
    o.do_table_structure = True
    o.table_structure_options = TableStructureOptions(mode=TableFormerMode.ACCURATE)
    o.generate_page_images = True

    if engine == "rapidocr":
        # backend="torch" on purpose: RapidOcrOptions defaults to onnxruntime, which is
        # not installed here, while Docling's own OcrAutoOptions selects torch. Building
        # the options explicitly therefore fails where the default path works -- a trap
        # worth naming, since it looks like the engine is unavailable when it is not.
        ocr = RapidOcrOptions(force_full_page_ocr=full_page, backend="torch")
        if model:
            # rapidocr_params reaches the engine directly, and it validates on the ENUM,
            # not the string -- passing "server" raises "must be Enum Type".
            from rapidocr.utils.typings import ModelType
            mt = ModelType(model)
            ocr.rapidocr_params = {"Det.model_type": mt, "Rec.model_type": mt}
    elif engine == "easyocr":
        ocr = EasyOcrOptions(force_full_page_ocr=full_page)
    elif engine == "ocrmac":
        ocr = OcrMacOptions(force_full_page_ocr=full_page)
    elif engine == "tesseract":
        ocr = TesseractOcrOptions(force_full_page_ocr=full_page)
    else:
        raise SystemExit(f"unknown engine {engine!r}")
    o.ocr_options = ocr

    for target in (o, getattr(o, "picture_description_options", None)):
        if target is not None and hasattr(target, "picture_area_threshold"):
            target.picture_area_threshold = 0.0
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=o)})


def run_one(pdf: Path, engine: str, model: str | None, full_page: bool) -> dict:
    t0 = time.time()
    doc = build(engine, model, full_page).convert(str(pdf)).document
    elapsed = time.time() - t0
    blocks = [{"text": i.text, "kind": type(i).__name__}
              for i, _ in doc.iterate_items() if getattr(i, "text", "")]
    text = doc.export_to_markdown()
    return {"config": f"{engine}"
                      f"{'/' + model if model else ''}"
                      f"{'/full-page' if full_page else ''}",
            "seconds": round(elapsed, 1), **score(text, blocks), "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(description="benchmark OCR configurations")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--configs", default="rapidocr::0,rapidocr::1,rapidocr:server:1",
                    help="comma-separated engine:model:full_page triples")
    a = ap.parse_args()
    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for spec in a.configs.split(","):
        engine, model, full = (spec.split(":") + ["", "0"])[:3]
        try:
            r = run_one(Path(a.pdf), engine, model or None, full == "1")
        except Exception as e:
            print(f"[bench] {spec}: FAILED {type(e).__name__}: {str(e)[:110]}")
            continue
        (out_dir / f"{r['config'].replace('/', '_')}.md").write_text(r.pop("text"))
        rows.append(r)
        print(f"[bench] {r['config']:<26} {r['seconds']:>6}s  {r['words']:>5} words  "
              f"{r['money']:>2} $  {r['truncated_blocks']:>3} truncated  "
              f"{r['tails_recovered']}/3 tails")
    (out_dir / "bench.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
