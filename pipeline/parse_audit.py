"""Adversarial parse audit — prove a PDF parsed correctly, never assume it.

WHY THIS IS THE GATE. Conversion quality determines the quality of every layer above it, and
a bad parse is invisible. pdfplumber on the Year 2 A2Zero report returns 234 words and ZERO
dollar figures out of 16, across 14 pages, and exits cleanly. Nothing raises. That is this
project's founding failure mode — confidently wrong, nothing errors — occurring one layer
below where the rest of the guards sit.

FAIL CLOSED. Every section starts `unaudited` and extraction refuses anything not `clean`.
Silence is not evidence of a good parse. Flagging a section that turns out fine is cheap;
passing one that is quietly missing two thirds of a grant table is not.

THE CHECKS ARE MEASURED, NOT ASSUMED. The first design here used `find_tables()` row counts
to catch the known Year 2 grant-table loss. Tested against the actual PDF, that check FAILS:
the grant page yields 0 detected tables, while other pages report 18-49 spurious "rows" that
are really bounding boxes around graphics. What does separate that document cleanly is the
image-to-text ratio — page 13 carries 47 images and 54 characters. So IMAGE_DOMINANCE is the
primary detector and table census is secondary. Checks earn their place by firing on real
failures, not by sounding rigorous.

WHAT THIS DOES NOT DO. It never asserts a parse is *correct* — there is no gold standard for
Years 3-5, whose markdown came from an unverified pdfplumber+Claude pass. It asserts that two
independent reads disagree, or that a page looks structurally unlike the rest of its document,
and points a human at it.

Usage:
    python -m pipeline.parse_audit --pdf path/to.pdf
    python -m pipeline.parse_audit --pdf a.pdf --against converted.md
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass, asdict, field
from pathlib import Path

# A page with almost no extractable text is either blank or an image. Below this it cannot
# carry a claim, so it is never silently accepted as "parsed".
MIN_PAGE_CHARS = 40
# Year 2's signature: dozens of images, tens of characters. A text page in these reports runs
# hundreds of chars against a handful of images.
IMAGE_DOMINANCE = 3.0        # images per 100 chars
# A page far below its own document's median is partially extracted, whatever the cause.
DENSITY_FLOOR = 0.25         # fraction of the document median

# Bullet glyphs that survive as TEXT when their list items are images. Measured on the
# Year 2 grant page: the entire extractable text is "• o o o o o o o o o o o o" -- one
# bullet and twelve sub-bullets, matching the twelve known grants exactly, while every
# grant's content is an image. The PDF tells us how much we are missing even when it
# cannot tell us what.
BULLET_GLYPHS = set("•o·▪◦-–—*")
# Docling skips pictures smaller than this fraction of page area BY DEFAULT, silently.
# Measured on that same page: 46 of 47 images fall below it, median area 0.0032.
PICTURE_AREA_DEFAULT = 0.05

MONEY = re.compile(r"\$\s?[0-9][0-9,\.]*(?:\s?(?:million|billion|M|B)\b)?", re.I)
PERCENT = re.compile(r"[0-9][0-9,\.]*\s?%")
UNIT_NUM = re.compile(r"[0-9][0-9,\.]*\s?(?:MW|kW|GWh|MWh|tons?|homes?|acres?|trees?)\b", re.I)


@dataclass
class Flag:
    check: str
    severity: str            # high | medium | low
    page: int | None
    detail: str
    evidence: dict = field(default_factory=dict)


@dataclass
class PageStat:
    page: int
    words: int
    chars: int
    images: int
    tables: int
    table_rows: int
    money: int
    percents: int
    bullet_glyphs: int = 0        # list markers present as text
    content_tokens: int = 0       # tokens that are NOT list markers
    small_images: int = 0         # images below PICTURE_AREA_DEFAULT


def page_stats(pdf_path: str | Path) -> list[PageStat]:
    import pdfplumber
    out: list[PageStat] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            t = pg.extract_text() or ""
            try:
                tbls = pg.find_tables()
            except Exception:
                tbls = []
            toks = t.split()
            bullets = sum(1 for x in toks if x and all(ch in BULLET_GLYPHS for ch in x))
            page_area = float(pg.width) * float(pg.height) or 1.0
            small = sum(1 for im in pg.images
                        if abs((im["x1"] - im["x0"]) * (im["bottom"] - im["top"])) / page_area
                        < PICTURE_AREA_DEFAULT)
            out.append(PageStat(
                page=i, words=len(toks), chars=len(pg.chars), images=len(pg.images),
                tables=len(tbls), table_rows=sum(len(x.rows) for x in tbls),
                money=len(MONEY.findall(t)), percents=len(PERCENT.findall(t)),
                bullet_glyphs=bullets, content_tokens=len(toks) - bullets, small_images=small))
    return out


def tier1(stats: list[PageStat]) -> list[Flag]:
    """Deterministic, free, needs no reference and no model."""
    flags: list[Flag] = []
    if not stats:
        return [Flag("no_pages", "high", None, "PDF yielded no pages")]

    for s in stats:
        # PRIMARY DETECTOR. Measured to be the one that actually separates Year 2.
        if s.chars and (s.images / max(s.chars, 1)) * 100 >= IMAGE_DOMINANCE:
            flags.append(Flag(
                "image_dominance", "high", s.page,
                f"page is graphics, not text: {s.images} images vs {s.chars} chars",
                {"images": s.images, "chars": s.chars}))
        if s.chars < MIN_PAGE_CHARS:
            flags.append(Flag(
                "empty_page", "high", s.page,
                f"only {s.chars} chars extracted", {"chars": s.chars, "images": s.images}))
        # ORPHANED LIST MARKERS. The single most informative check we have: it reports HOW
        # MANY items are missing, not merely that something is wrong. A page whose text is
        # bullets and nothing else contains a list whose content is unextractable.
        if s.bullet_glyphs >= 3 and s.content_tokens <= s.bullet_glyphs:
            flags.append(Flag(
                "orphan_list_markers", "high", s.page,
                f"~{s.bullet_glyphs} list items detected but their content is not extractable",
                {"bullet_glyphs": s.bullet_glyphs, "content_tokens": s.content_tokens}))
        # The picture_area_threshold trap, surfaced BEFORE it silently drops content.
        if s.images >= 5 and s.small_images / s.images >= 0.8:
            flags.append(Flag(
                "subthreshold_images", "medium", s.page,
                f"{s.small_images} of {s.images} images are under the {PICTURE_AREA_DEFAULT} "
                f"default area threshold and would be skipped silently",
                {"small_images": s.small_images, "images": s.images}))
        # Secondary: a detected table whose rows did not survive into the text.
        if s.table_rows >= 3 and s.words < s.table_rows:
            flags.append(Flag(
                "table_row_shortfall", "medium", s.page,
                f"{s.table_rows} table rows detected but only {s.words} words extracted",
                {"table_rows": s.table_rows, "words": s.words}))

    med = statistics.median([s.words for s in stats]) or 0
    if med:
        for s in stats:
            if s.words < med * DENSITY_FLOOR:
                flags.append(Flag(
                    "density_outlier", "medium", s.page,
                    f"{s.words} words vs document median {med:.0f}",
                    {"words": s.words, "median": med}))

    # Document-level: a report with money on its pages that yields none at all.
    total_money = sum(s.money for s in stats)
    thin = sum(1 for s in stats if s.chars < MIN_PAGE_CHARS)
    if total_money == 0 and thin:
        flags.append(Flag(
            "no_numerics", "high", None,
            f"zero dollar figures across {len(stats)} pages, {thin} of them near-empty",
            {"pages": len(stats), "thin_pages": thin}))
    return flags


def conservation(pdf_text: str, converted: str) -> list[Flag]:
    """Numbers present in the PDF's own text layer that vanished from the conversion."""
    flags = []
    for name, rx in (("money", MONEY), ("percent", PERCENT), ("unit_number", UNIT_NUM)):
        src = {m.strip() for m in rx.findall(pdf_text)}
        got = {m.strip() for m in rx.findall(converted)}
        lost = src - got
        if lost:
            flags.append(Flag(
                f"{name}_dropped", "high", None,
                f"{len(lost)} {name} value(s) in the PDF text layer are absent from the conversion",
                {"lost": sorted(lost)[:20]}))
    return flags


def verdict(flags: list[Flag], n_pages: int) -> tuple[str, float]:
    """Fail closed, but SCALE MATTERS — measured, not guessed.

    Years 4 and 5 flag on pages 1, 2 and 24: cover, contents, back cover. Those are
    structurally low-text, not parse failures. Year 2 flags all 14 of 14 pages. A verdict
    that calls both "suspect" tells a reviewer nothing and burns their attention on covers.

    So the discriminator is the FRACTION of pages carrying a high-severity flag:
      unusable  most of the document failed to parse -- do not ingest
      suspect   isolated pages need a human, the rest may be fine
      clean     nothing fired
    """
    hi = {f.page for f in flags if f.severity == "high" and f.page is not None}
    frac = len(hi) / n_pages if n_pages else 0.0
    if frac >= 0.5:
        return "unusable", frac
    if flags:
        return "suspect", frac
    return "clean", frac


def audit(pdf_path: str | Path, converted: str | None = None) -> dict:
    stats = page_stats(pdf_path)
    flags = tier1(stats)
    if converted is not None:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            raw = "\n".join(p.extract_text() or "" for p in pdf.pages)
        flags += conservation(raw, converted)
    v, frac = verdict(flags, len(stats))
    return {"pdf": str(pdf_path), "pages": len(stats), "verdict": v,
            "high_page_fraction": round(frac, 3),
            "flags": [asdict(f) for f in flags], "stats": [asdict(s) for s in stats]}


def report(res: dict) -> None:
    v = res["verdict"]
    frac = res.get("high_page_fraction", 0)
    note = f"  ({frac:.0%} of pages high-flagged)" if frac else ""
    print(f"\n{Path(res['pdf']).name} — {res['pages']} pages — {v.upper()}{note}")
    if not res["flags"]:
        print("  no checks fired")
        return
    order = {"high": 0, "medium": 1, "low": 2}
    by = sorted(res["flags"], key=lambda f: (order.get(f["severity"], 9), f["page"] or 0))
    print(f"  {'severity':<9}{'page':>5}  {'check':<22} detail")
    print("  " + "-" * 92)
    for f in by[:40]:
        print(f"  {f['severity']:<9}{str(f['page'] or '—'):>5}  {f['check']:<22}{f['detail'][:52]}")
    if len(by) > 40:
        print(f"  … {len(by)-40} more")


def main() -> int:
    ap = argparse.ArgumentParser(description="adversarial parse audit for a PDF conversion")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--against", help="path to a converted .md/.txt to check conservation against")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    conv = Path(a.against).read_text() if a.against else None
    res = audit(a.pdf, conv)
    print(json.dumps(res, indent=1)) if a.json else report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
