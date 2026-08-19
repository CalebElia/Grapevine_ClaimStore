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

from pipeline.convert_blocks import convert
from pipeline.extract_figures import extract_figures, render_figure_block
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
    ap.add_argument("--page-markers", action="store_true",
                    help="emit <!-- p.N --> at each page change, for the human review pass")
    a = ap.parse_args()

    conv, blocks, ambiguous = convert(a.pdf, a.blocks)
    print(f"[blocks] {len(blocks)} blocks, {len(conv.text.split()):,} words, "
          f"{conv.n_pages} pages")
    recovered = [b for b in blocks if b["kind"] == "UncoveredText"]
    print(f"[blocks] {len(recovered)} region(s) recovered by the coverage sweep "
          f"({sum(1 for b in recovered if b.get('caption_for'))} of them captions)")

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

    md = render_blocks(blocks, figure_xml, a.title, page_markers=a.page_markers)
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
