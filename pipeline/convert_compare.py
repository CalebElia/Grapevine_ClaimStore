"""Cross-converter disagreement — Tier 2 of the parse audit.

WHAT THIS IS FOR. Tier 1 checks a conversion against itself (is this page suspiciously
sparse, are there orphaned bullets). It cannot see content that is missing from EVERY read,
and it cannot tell you which of two conversions is better. Tier 2 runs independent reads over
the same PDF and reports where they differ.

WHAT IT DELIBERATELY DOES NOT DO. It never declares a winner per disagreement. A July 2026
audit of 265,000 samples measured model agreement against correctness at Spearman 0.20-0.59 --
agreement is a ROUTER, not a validator. This is the same lesson the ASR benchmark taught on
speaker names: consensus chose 'dish' over the correct 'Disch', and no system produced
'Radina' at all. So disagreement points a human at a page; it never votes.

WHY NUMBERS AND NOT TEXT SIMILARITY. Two conversions of a designed document differ constantly
in whitespace, bullet glyphs and heading case, none of which matters. What matters is whether
a dollar figure, a percentage or a measured quantity is present in one read and absent from
another -- because those become `quantities` and `fiscal_references` rows, and a silently
dropped one is a claim that will never exist with nothing to indicate it should have.

RANKED BY WHAT IS AT STAKE. A missing dollar figure outranks a missing percentage outranks a
missing unit-number, because that is the order in which they carry load in this store. The
output is a worklist, and a worklist nobody finishes is worse than a shorter honest one.

Usage:
    python -m pipeline.convert_compare --pdf report.pdf --other converted.md
    python -m pipeline.convert_compare --pdf report.pdf --other a.md --label docling
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pipeline.convert_document import Conversion, convert, convert_from_markdown
from pipeline.parse_audit import MONEY, PERCENT, UNIT_NUM

# Load-bearing order. Money first because fiscal_references and quantities are what an
# annual report is mostly FOR.
KINDS = [("money", MONEY, 3), ("percent", PERCENT, 2), ("unit_number", UNIT_NUM, 1)]


def _norm_money(s: str) -> str:
    """$2.5 million and $2,500,000 are the same figure written two ways.

    Both appear in this corpus -- the Year 2 report writes '$2.5 million' in prose and
    '$2,500,000' in the grant list. Treating them as different values would manufacture a
    disagreement that is really a formatting choice, and a worklist full of those is one a
    reviewer learns to ignore.
    """
    t = s.lower().replace("$", "").replace(",", "").replace(" ", "").strip()
    mult = 1
    for suf, m in (("billion", 1_000_000_000), ("million", 1_000_000), ("b", 1_000_000_000),
                   ("m", 1_000_000)):
        if t.endswith(suf):
            t, mult = t[: -len(suf)], m
            break
    try:
        return f"{int(round(float(t) * mult)):d}"
    except ValueError:
        return s.strip()


def extract(kind: str, rx: re.Pattern, text: str) -> Counter:
    vals = [m.strip() for m in rx.findall(text)]
    if kind == "money":
        vals = [_norm_money(v) for v in vals]
    return Counter(v for v in vals if v)


@dataclass
class Divergence:
    kind: str
    value: str
    present_in: str
    missing_from: str
    page: int | None
    weight: int
    context: str


def _context(text: str, needle_rx: re.Pattern, value: str, kind: str) -> tuple[int, str]:
    """Locate a value's first occurrence, returning (offset, surrounding text)."""
    for m in needle_rx.finditer(text):
        raw = m.group(0).strip()
        if (_norm_money(raw) if kind == "money" else raw) == value:
            lo = max(0, m.start() - 60)
            return m.start(), re.sub(r"\s+", " ", text[lo:m.end() + 60]).strip()
    return -1, ""


def compare(a: Conversion, b: Conversion) -> list[Divergence]:
    out: list[Divergence] = []
    for kind, rx, weight in KINDS:
        ca, cb = extract(kind, rx, a.text), extract(kind, rx, b.text)
        for value in (ca.keys() - cb.keys()):
            off, ctx = _context(a.text, rx, value, kind)
            out.append(Divergence(kind, value, a.converter, b.converter,
                                  a.page_for(off) if off >= 0 else None, weight, ctx))
        for value in (cb.keys() - ca.keys()):
            off, ctx = _context(b.text, rx, value, kind)
            out.append(Divergence(kind, value, b.converter, a.converter,
                                  b.page_for(off) if off >= 0 else None, weight, ctx))
    # Highest stake first, then group by page so a reviewer opens each page once.
    out.sort(key=lambda d: (-d.weight, d.page if d.page is not None else 10**6, d.value))
    return out


def report(a: Conversion, b: Conversion, divs: list[Divergence], top: int = 30) -> None:
    print(f"\n{Path(a.source_path).name}")
    print(f"  A = {a.converter:<22} {len(a.text.split()):>7,} words, {a.n_pages} page(s)")
    print(f"  B = {b.converter:<22} {len(b.text.split()):>7,} words, {b.n_pages} page(s)")
    if not divs:
        print("\n  no numeric divergence — the two reads agree on every figure")
        return
    only_a = sum(1 for d in divs if d.present_in == a.converter)
    print(f"\n  {len(divs)} divergent value(s): {only_a} only in A, {len(divs)-only_a} only in B")
    print(f"\n  {'kind':<12}{'value':<16}{'page':>5}  only-in          context")
    print("  " + "-" * 100)
    for d in divs[:top]:
        print(f"  {d.kind:<12}{d.value[:15]:<16}{str(d.page or '—'):>5}  {d.present_in[:15]:<16} "
              f"{d.context[:44]}")
    if len(divs) > top:
        print(f"  … {len(divs)-top} more")
    pages = Counter(d.page for d in divs if d.page is not None)
    if pages:
        print(f"\n  WORKLIST — pages to verify, most divergence first:")
        for pg, n in pages.most_common(10):
            print(f"    page {pg:>3}   {n} divergent value(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description="cross-converter disagreement (Tier 2)")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--other", required=True, help="an already-converted .md to compare against")
    ap.add_argument("--label", default="reference", help="name for the --other conversion")
    ap.add_argument("--converter", default="pdfplumber")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    ca = convert(a.pdf, a.converter)
    cb = convert_from_markdown(Path(a.other), a.label)
    report(ca, cb, compare(ca, cb), a.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
