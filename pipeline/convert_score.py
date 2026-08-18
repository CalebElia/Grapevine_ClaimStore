"""Score a conversion against verified ground truth. Recall AND precision, separately.

WHY BOTH, AND WHY PRECISION IS THE ONE THAT MATTERS HERE.

Recall asks: of the numbers we know are in the document, how many did this arm find?
Precision asks: of the numbers this arm reported, how many are actually there?

For most parsing work recall is the interesting number. For THIS corpus, on THIS
document, precision is the one that disqualifies. The A2Zero Year 2 report has an
effectively empty text layer -- 234 extractable words across 14 pages, with every figure
living inside an image. Hand a vision model a page with no anchoring text and ask what is
on it, and there is nothing stopping it inventing a plausible dollar amount. A fabricated
"$85,000 from the State of Michigan" is not a parsing error in this store; it is a
`fiscal_references` row with a verbatim that cites a document which never said it.

So an arm with 90% recall and one hallucinated figure is WORSE than an arm with 60%
recall and none. The first quietly poisons the claim store; the second merely leaves a
gap that the parse audit already knows how to flag.

A CRITICAL LIMIT ON PRECISION, MEASURED NOT ASSUMED. The Year 2 ground truth was built
by a pipeline whose healer step explicitly deletes chart-derived content -- its own Rule 4
reads "Delete text/stats already captured inside <figure_description>". Verified: the
pre-healer file carries 8 figure_description blocks, the post-healer file 2. The healer
removed six.

So a numeric value an arm reports that is ABSENT from ground truth is one of two things,
and this module cannot tell them apart:

  1. a hallucination                    -- disqualifying
  2. a real figure/chart value the      -- the whole reason Content Understanding
     healer stripped downstream            and enableFigureAnalysis are being tested

The March-era Docling output trips this: it reports 10%, 40% and 60%, none present in
ground truth. Read in context they are chart readings ("a 10% reduction from the
baseline"), not fabrications. Scoring them as hallucinations would penalise exactly the
capability we are trying to buy.

THEREFORE precision here is reported as UNVERIFIED, not as a hallucination rate, and the
values are listed for a human to adjudicate. The one place precision IS trustworthy is
text rendered as images -- the twelve-grant list -- because that content is text, was
never a chart, and is present and hand-confirmed in ground truth. Use
--grant-check for that scoring.

WHAT COUNTS AS GROUND TRUTH. Only a human-vetted conversion. The Year 2 markdown
qualifies -- it was hand-corrected and confirmed line by line, including the twelve-grant
list that no automated pipeline has ever read correctly (the raw Docling output captured
4 of 12). Scoring against a machine artifact measures agreement between machines, which
is how a previous pass here reported 12/12 for an arm that actually managed 4/12.

Usage:
    python -m pipeline.convert_score --truth vetted.md --against arm_output.md --label cu
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.convert_compare import KINDS, _norm_money, extract


@dataclass
class KindScore:
    kind: str
    found: int
    total: int
    spurious: int
    missing: list[str]
    unverified: list[str]        # NOT proven hallucinations -- see module docstring

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 1.0

    @property
    def precision(self) -> float:
        reported = self.found + self.spurious
        return self.found / reported if reported else 1.0


def score(truth: str, candidate: str) -> list[KindScore]:
    out = []
    for kind, rx, _ in KINDS:
        t, c = set(extract(kind, rx, truth)), set(extract(kind, rx, candidate))
        found = t & c
        out.append(KindScore(kind=kind, found=len(found), total=len(t),
                             spurious=len(c - t), missing=sorted(t - c),
                             unverified=sorted(c - t)))
    return out


def word_coverage(truth: str, candidate: str) -> float:
    """Fraction of ground-truth word TYPES present in the candidate.

    Deliberately type-based, not a sequence diff: reading order and whitespace differ
    constantly between converters and none of that matters for whether a claim can be
    anchored. What matters is whether the words are there at all.
    """
    norm = lambda s: set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())
    t = norm(truth)
    return len(t & norm(candidate)) / len(t) if t else 1.0


# The twelve grants on page 13 of the Year 2 report, hand-confirmed by the human against
# the page image. This is the ONLY fully trustworthy precision test in the corpus: the
# content is text rendered as images (not a chart), so no healer stripped it, and every
# amount is present in ground truth. No automated pipeline has read it correctly -- the
# March Docling captured 4 of 12.
GRANT_AMOUNTS = ["$25,000", "$75,000", "$4,500,000", "$2,500,000", "$170,000", "$270,000",
                 "$406,000", "$8,500", "$5,000", "$2,500", "$54,000", "$25,000"]


def grant_check(candidate: str) -> dict:
    """Score against the twelve verified grant amounts. Duplicates matter: $25,000
    appears twice (U.S. EPA and Solar Moonshot), so a converter that reports it once
    has genuinely lost a grant."""
    from collections import Counter
    want = Counter(_norm_money(a) for a in GRANT_AMOUNTS)
    got = extract("money", KINDS[0][1], candidate)
    found = sum(min(n, got.get(v, 0)) for v, n in want.items())
    missing = sorted(v for v, n in want.items() if got.get(v, 0) < n)
    return {"found": found, "total": sum(want.values()), "missing": missing}


def report(rows: list[tuple[str, str]], truth: str, verbose: bool = False) -> None:
    """rows: [(label, candidate_text), ...]"""
    print(f"\nground truth: {len(truth.split()):,} words")
    print(f"\n{'arm':<22}{'words':>7}{'money R/P':>13}{'pct R/P':>12}{'unit R/P':>12}"
          f"{'UNVERIF':>9}")
    print("-" * 88)
    for label, cand in rows:
        ss = {s.kind: s for s in score(truth, cand)}
        wc = word_coverage(truth, cand)
        unverified = sum(s.spurious for s in ss.values())
        cells = []
        for k in ("money", "percent", "unit_number"):
            s = ss[k]
            cells.append(f"{s.recall:.0%}/{s.precision:.0%}")
        print(f"{label:<22}{wc:>6.0%}{cells[0]:>13}{cells[1]:>12}{cells[2]:>12}"
              f"{unverified:>9}")

    print("\nR = recall (of known facts, how many found) · "
          "P = precision (of reported facts, how many real)")
    print("UNVERIF  = values reported that are absent from ground truth. NOT a\n"
          "           hallucination count: the ground-truth pipeline deliberately\n"
          "           stripped chart-derived stats, so a real chart reading lands here\n"
          "           too. A human must adjudicate these; see the module docstring.")

    if verbose:
        for label, cand in rows:
            for s in score(truth, cand):
                if s.missing or s.unverified:
                    print(f"\n  {label} / {s.kind}:")
                    if s.missing:
                        print(f"    MISSED    ({len(s.missing)}): {', '.join(s.missing[:14])}")
                    if s.unverified:
                        print(f"    UNVERIFIED({len(s.unverified)}): {', '.join(s.unverified[:14])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="score a conversion against vetted truth")
    ap.add_argument("--truth", required=True)
    ap.add_argument("--against", action="append", required=True,
                    metavar="LABEL=PATH", help="repeatable: --against pdfplumber=out.md")
    ap.add_argument("--verbose", action="store_true", help="list missed and unverified values")
    ap.add_argument("--grants", action="store_true",
                    help="also score the 12 hand-verified grant amounts (trustworthy precision)")
    a = ap.parse_args()
    truth = Path(a.truth).read_text()
    rows = []
    for spec in a.against:
        label, _, path = spec.partition("=")
        rows.append((label, Path(path).read_text()))
    report(rows, truth, a.verbose)
    if a.grants:
        print(f"\nGRANT LIST — 12 hand-verified amounts, page 13 (the only clean precision test)")
        print(f"  {'arm':<22}{'found':>8}   missing")
        print("  " + "-" * 76)
        for label, cand in rows:
            g = grant_check(cand)
            miss = ", ".join(f"${int(v):,}" for v in g["missing"][:8]) or "none"
            print(f"  {label:<22}{g['found']:>4}/{g['total']:<3}   {miss[:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
