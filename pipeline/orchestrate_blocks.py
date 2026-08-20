"""End-to-end block-based assembly: Docling blocks + pdfplumber characters + vision
figures -> one reviewable markdown file.

    # 1. structure (slow, ~36s, needs the docling env)
    /opt/miniconda3/envs/grapevine-docling/bin/python -m pipeline.convert_docling \
        --pdf report.pdf --out out.md --no-charts
    # 2. text + figures + markdown (project env)
    python -m pipeline.orchestrate_blocks --pdf report.pdf --blocks out.blocks.json \
        --pictures out.pictures.json --figures-dir figs --title "..." --out review.md

Kept separate from orchestrate_document.py, which still drives the CU-anchored path.
That path remains useful as an INDEPENDENT structural cross-check -- corroboration is
independence, not count -- but it is no longer the spine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.convert_blocks import body_shapes, convert, heading_shapes
from pipeline.extract_figures import extract_figures, render_figure_block
from pipeline.quality_gate import assess, find_numbers, verdict
from pipeline.quality_gate import report as gate_report
from pipeline.render_blocks import render_blocks


def main() -> int:
    ap = argparse.ArgumentParser(description="block-based document assembly")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--blocks", required=True, help="<out>.blocks.json from convert_docling")
    ap.add_argument("--pictures", help="<out>.pictures.json -- extract figures now")
    ap.add_argument("--figures-json", help="reuse an earlier extraction (no vision calls)")
    ap.add_argument("--figures-dir", help="where crops land (required with --pictures)")
    ap.add_argument("--deployment-env", default="GRAPEVINE_DEPLOYMENT_VISION")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-refused", action="store_true",
                    help="write the file even when the gate refuses. Requires a reason, "
                         "recorded in the output, so an override is never silent.")
    ap.add_argument("--override-reason", default="",
                    help="why the refusal is being overridden")
    ap.add_argument("--page-markers", action="store_true",
                    help="emit <!-- p.N --> at each page change, for the human review pass")
    a = ap.parse_args()

    conv, blocks, conv_report = convert(a.pdf, a.blocks)
    ambiguous = conv_report["ambiguous_hyphens"]
    print(f"[blocks] {len(blocks)} blocks, {len(conv.text.split()):,} words, "
          f"{conv.n_pages} pages")
    srcs = [b.get("text_source") for b in blocks
            if (b.get("text") or "").strip() and b["kind"] != "PictureItem"]
    n_ocr = sum(1 for x in srcs if x == "docling_ocr")
    if n_ocr:
        print(f"[blocks] {n_ocr}/{len(srcs)} text block(s) ({100*n_ocr//max(len(srcs),1)}%) "
              f"read by OCR -- no usable text layer, NOT character-exact")
    fixes = conv_report.get("ocr_term_fixes") or []
    if fixes:
        from collections import Counter
        c = Counter(f"{v} -> {c2}" for v, c2 in fixes)
        print(f"[blocks] {len(fixes)} OCR term correction(s) from the registry: "
              f"{dict(c)}")
    recovered = [b for b in blocks if b["kind"] == "UncoveredText"]
    print(f"[blocks] {len(recovered)} region(s) recovered by the coverage sweep "
          f"({sum(1 for b in recovered if b.get('caption_for'))} of them captions)")
    if conv_report["captions_associated"]:
        print(f"[blocks] {len(conv_report['captions_associated'])} unlinked block(s) "
              f"associated to a picture and dropped as captions -- listed so a wrong "
              f"association is auditable, not an invisible deletion:")
        for pno, txt in conv_report["captions_associated"]:
            print(f"[blocks]   p.{pno}: {txt}")

    figs = []
    if a.pictures:
        if not a.figures_dir:
            ap.error("--figures-dir is required with --pictures")
        figs = extract_figures(Path(a.pdf), json.loads(Path(a.pictures).read_text()),
                               Path(a.figures_dir), a.deployment_env, dpi=a.dpi)
        (Path(a.figures_dir) / "figures.json").write_text(json.dumps(figs, indent=2))
    elif a.figures_json:
        figs = json.loads(Path(a.figures_json).read_text())
    figure_xml = {f["page_no"]: render_figure_block(f) for f in figs}
    print(f"[blocks] {len(figure_xml)} figure(s) available")

    # THE GATE, against the OTHER arm's read of the same PDF. Comparing a conversion
    # only against itself cannot detect that it lost the document: a converter that read
    # nothing is perfectly self-consistent.
    dl_text = Path(a.blocks).with_suffix("").with_suffix(".md")
    ref = dl_text.read_text() if dl_text.exists() else ""
    findings = assess(conv.text, conv.page_map, blocks,
                      reference_words=len(ref.split()),
                      reference_numbers=find_numbers(ref))
    print(gate_report(findings, Path(a.pdf).name))
    v = verdict(findings)

    if v == "refuse" and not a.allow_refused:
        print("[gate] REFUSING to write. This document is not readable as converted, and "
              "storing claims against it would anchor them to text that is not there.")
        print("[gate] Override with --allow-refused --override-reason '...' if you have "
              "a reason; it will be recorded in the file.")
        return 2

    md = render_blocks(blocks, figure_xml, a.title, page_markers=a.page_markers,
                       heading_shapes=heading_shapes(blocks),
                       body_shapes=body_shapes(blocks))
    banner = [f"<!-- gate: {v.upper()} -->"]
    for f in findings:
        banner.append(f"<!-- gate {f.severity}: {f.check} -- {f.evidence} -->")
    if v == "refuse":
        banner.append(f"<!-- gate OVERRIDDEN by operator. Reason: "
                      f"{a.override_reason or 'NONE GIVEN'} -->")
    # banner sits directly under the H1 so the verdict travels with the document
    head, rest = md.split("\n", 1)
    md = "\n".join([head, *banner, rest])
    Path(a.out).write_text(md)
    print(f"[blocks] wrote {a.out} ({len(md.split()):,} words)")

    if ambiguous:
        print(f"\n[blocks] {len(ambiguous)} AMBIGUOUS hyphen split(s) -- kept hyphenated, "
              f"needs a judgement pass:")
        for x, y in ambiguous:
            print(f"[blocks]   {x}-{y}   (join -> {x}{y}?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
