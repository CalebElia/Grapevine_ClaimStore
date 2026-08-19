"""Crop-and-vision-extract: turn a Docling picture record worth extraction into a
validated <figure_description> XML block, ready to splice into the orchestrated markdown.

WHY A SEPARATE STEP FROM convert_docling.py. Docling's own env (grapevine-docling) is
where the classifier runs; the crop and the vision call are cheap, need no torch/docling
import, and run in the project's normal env against pipeline/vision_extract.py's Azure
deployment. Splitting them means a vision-prompt iteration never needs the slow Docling
conversion to re-run alongside it.

THE COORDINATE CONVERSION IS THE ONE WAY THIS SILENTLY CROPS THE WRONG REGION. Docling's
bbox is (l, t, r, b) in PDF points with BOTTOMLEFT origin -- confirmed empirically on the
real Year 5 PDF, not assumed (see convert_docling.py). A PIL image from pdfplumber's
page.to_image() is always TOP-LEFT origin. Converting one BOTTOMLEFT y to a TOP-LEFT y is
`page_height - y`; getting the sign wrong crops a mirror-image region that often still
LOOKS plausible (still overlaps part of a photo-heavy page) instead of raising. Pinned by
test_extract_figures.py against the real page 4 bar_chart bbox.

CROPPING AT HIGH DPI IS THE VALIDATED LEVER, NOT AN OPTION LEFT AT DEFAULT. Measured
earlier in this project: cropping to the figure's exact bbox at 600dpi (vs. sending the
whole page, or a low-dpi crop) was what took the vision model from "correctly declines to
guess, adds nothing" to 31 real, cross-validated data points on this exact dashboard. The
default here is 600, not 72 or 150, because a lower default would quietly regress that
result the next time someone omits --dpi.

MIN_AREA_FRAC IS A COST GUARD, NOT A CORRECTNESS FILTER. worth_extraction (see
convert_docling.py) is the real signal -- a label match, already validated against this
document's ground truth (2 for 2: the GHG chart and the dashboard). The area floor exists
only to stop a misclassified icon-sized image from spending a 600dpi render and a paid
vision call; it defaults low (2%) so it never excludes a real chart, only postage stamps.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pipeline import vision_extract


def crop_box_px(bbox: tuple[float, float, float, float], coord_origin: str,
                page_h_pts: float, dpi: int) -> tuple[int, int, int, int]:
    """Docling bbox (points) -> PIL crop box (pixels, top-left origin) at the given DPI.

    `sorted()` on each axis guards against a bbox whose corners are not already in
    (min, max) order -- seen nowhere yet, but a silently-mirrored crop from an unsorted
    box would look like a smaller version of the same failure this function exists to
    prevent, so the guard costs nothing and closes that path too.
    """
    l, t, r, b = bbox
    if "BOTTOMLEFT" in coord_origin:
        top, bottom = page_h_pts - t, page_h_pts - b
    else:
        top, bottom = t, b
    scale = dpi / 72.0
    x0, x1 = sorted((l * scale, r * scale))
    y0, y1 = sorted((top * scale, bottom * scale))
    return (int(x0), int(y0), int(x1), int(y1))


def crop_page(pdf_path: Path, page_no: int, bbox: tuple[float, float, float, float],
             coord_origin: str, page_h_pts: float, out_path: Path, dpi: int = 600) -> Path:
    """Render page `page_no` (1-indexed) at `dpi` and save the cropped region to
    `out_path`. One PDF open + render per call -- fine at the scale this runs (a
    document's worth-extraction count, not every picture).
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no - 1]
        img = page.to_image(resolution=dpi).original
    box = crop_box_px(bbox, coord_origin, page_h_pts, dpi)
    img.crop(box).save(out_path)
    return out_path


def render_figure_block(fig: dict) -> str:
    """One figure's markdown block: the raw XML wrapped in a provenance comment, so a
    reader sees which page, which classifier label led to the call, which deployment
    answered, and when -- not just a set of numbers with no trail back to their source.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return (f"<!-- figure: page {fig['page_no']}, classified {fig['top_label']} "
           f"(conf {fig['top_conf']}), extracted by {fig['deployment']} at {ts} -->\n"
           f"{fig['xml'].strip()}")


def extract_figures(pdf_path: Path, pictures: list[dict], out_dir: Path,
                    deployment_env: str = "GRAPEVINE_DEPLOYMENT_VISION",
                    dpi: int = 600, min_area_frac: float = 0.02) -> list[dict]:
    """Crop and vision-extract every picture flagged worth_extraction (see
    convert_docling.py), skipping anything under min_area_frac as too small to be a real
    chart rather than a misclassified icon. Returns one record per figure with the raw
    XML and enough provenance for render_figure_block / the orchestration splice step.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, pic in enumerate(pictures):
        if not pic.get("worth_extraction"):
            continue
        if (pic.get("area_frac") or 0) < min_area_frac:
            continue
        crop_path = out_dir / f"page{pic['page_no']:02d}_fig{i}.png"
        crop_page(pdf_path, pic["page_no"], tuple(pic["bbox"]), pic["coord_origin"],
                 pic["page_h"], crop_path, dpi=dpi)
        r = vision_extract.analyze(crop_path, deployment_env)
        results.append({
            "page_no": pic["page_no"], "top_label": pic["top_label"],
            "top_conf": pic["top_conf"], "crop_path": str(crop_path),
            "xml": r["text"], "deployment": r["deployment"], "seconds": r["seconds"],
            "finish_reason": r["finish_reason"],
        })
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="crop-and-vision-extract figures from a PDF")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pictures", required=True, help="<out>.pictures.json from convert_docling")
    ap.add_argument("--out-dir", required=True, help="where crops + a figures.json land")
    ap.add_argument("--deployment-env", default="GRAPEVINE_DEPLOYMENT_VISION")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--min-area-frac", type=float, default=0.02)
    a = ap.parse_args()

    pictures = json.loads(Path(a.pictures).read_text())
    out_dir = Path(a.out_dir)
    figs = extract_figures(Path(a.pdf), pictures, out_dir, a.deployment_env,
                           dpi=a.dpi, min_area_frac=a.min_area_frac)
    figs_out = out_dir / "figures.json"
    figs_out.write_text(json.dumps(figs, indent=2))
    print(f"[extract_figures] {len(pictures)} picture(s), {len(figs)} extracted -> {figs_out}")
    for f in figs:
        print(f"[extract_figures]   page {f['page_no']}: {f['top_label']} "
              f"{f['seconds']}s finish={f['finish_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
