"""Write a gated conversion into the claim store. No LLM; every judgement already happened.

Reads the artifacts the text path produced -- the reviewed markdown, its links, its hyphen
rulings, its figures -- and turns them into `documents`, `document_sections`,
`document_figures` and `document_links` rows.

WHAT MAKES THIS SAFE IS UPSTREAM, NOT HERE. The markdown already carries every decision as
a tag, the gate already refused what could not be read, and canonical.py already fixed the
coordinate space. This module's whole job is to not lose any of that on the way into SQL.

--dry-run PRINTS AND WRITES NOTHING, and is the intended first use on any new document.
Sectioning is the one part a human should look at before a row exists, because a wrong
section boundary silently changes which text a later claim is allowed to cite.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.canonical import Canonical, build, sections

# ── tiering ───────────────────────────────────────────────────────────────────────────
#
# MEASURED BEFORE IT WAS WRITTEN, across all 59 sections of the five A2Zero reports.
#
# TIER C -- never sent for extraction. NOT, on this corpus, where the cost saving lives:
# measured after the fact, C excludes 1,951 of 141,819 characters, or 1.4%. An annual
# report is almost entirely substantive -- a cover, a contents page, a closing, and claims
# in between. Tiering earns its place here by marking what must not be extracted FROM,
# not by saving money; expect a very different ratio on minutes and dockets. The corpus
# separates cleanly: every front-matter block, contents page, masthead and closing is 411
# characters or fewer and carries no enumerated content, while the smallest section that
# says anything is 792. Rather than encode 411, the rule is the structural fact underneath
# it: a section with no list items and less than a couple of paragraphs is a label, not an
# assertion. Both halves are required -- a short section WITH bullets is a real short
# section, and a long one without them is ordinary prose.
_C_MAX_CHARS = 600

# TIER A -- data-dense, worth the most careful extraction. A currency figure is the
# obvious signal; the number count exists because the GREENHOUSE GAS EMISSIONS SUMMARY
# sections carry 17-24 quantities and not one dollar sign, and they are the single most
# claim-bearing pages in these reports.
_A_MIN_NUMBERS = 15

_MONEY = re.compile(r"\$[\d,]+")
_NUMBER = re.compile(r"\b\d[\d,.]*%?")


def assign_tier(text: str, units: list) -> tuple[str, str]:
    """(tier, why) for one section."""
    has_items = any(u.kind == "list_item" for u in units)
    if not has_items and len(text) < _C_MAX_CHARS:
        return "C", "no enumerated content and shorter than a couple of paragraphs"
    money, numbers = len(_MONEY.findall(text)), len(_NUMBER.findall(text))
    if money:
        return "A", f"{money} currency figure(s)"
    if numbers >= _A_MIN_NUMBERS:
        return "A", f"{numbers} quantities"
    return "B", "prose with enumerated content"


# ── the document record ───────────────────────────────────────────────────────────────

_GATE = re.compile(r"<!--\s*gate:\s*(PASS|REVIEW|REFUSE)", re.I)
_PERIOD = re.compile(r"<!--\s*COVERAGE PERIOD:.*?->\s*(\d{4}-\d\d-\d\d)\.\.(\d{4}-\d\d-\d\d)"
                     r"\s*\((\d+) days\)(.*?)-->", re.S)
_OVERRIDE = re.compile(r"<!--\s*gate override:\s*(.*?)-->", re.S)


@dataclass
class DocumentRecord:
    verdict: str | None
    override_reason: str | None
    covers_period_start: str | None
    covers_period_end: str | None
    period_days: int | None
    period_flagged: bool
    page_count: int
    content_hash: str


def read_document(md: str, canon: Canonical) -> DocumentRecord:
    """Everything about the document itself, taken from the tags rather than re-derived.

    The coverage period is stored EXACTLY as parsed, with a flag when it is not about a
    year long. Years 3 and 4 state 337 and 338 days because both print "June 3" where the
    page means June 30 -- verified against the content stream, it is the source's typo.
    Repairing it here would put a date in the store that the document does not contain;
    the correction is a curator's ruling recorded against the record.
    """
    g = _GATE.search(md)
    p = _PERIOD.search(md)
    pages = [u.page_no for u in canon.units if u.page_no]
    return DocumentRecord(
        verdict=g.group(1).upper() if g else None,
        override_reason=(m.group(1).strip() if (m := _OVERRIDE.search(md)) else None),
        covers_period_start=p.group(1) if p else None,
        covers_period_end=p.group(2) if p else None,
        period_days=int(p.group(3)) if p else None,
        period_flagged=bool(p and "NOT A YEAR" in p.group(4)),
        page_count=max(pages) if pages else 0,
        content_hash=canon.content_hash,
    )


def plan(md_path: Path, links_path: Path | None = None,
         hyphens_path: Path | None = None, figures_path: Path | None = None) -> dict:
    """Everything that would be written, without writing any of it."""
    md = md_path.read_text()
    canon = build(md)
    doc = read_document(md, canon)

    secs = []
    for s in sections(canon):
        body = canon.text[s["char_start"]:s["char_end"]]
        tier, why = assign_tier(body, s["units"])
        flags: dict = {}
        if any(u.flags.get("placement_inferred") for u in s["units"]):
            flags["placement_inferred"] = True
        if any(u.flags.get("text_source") == "docling_ocr" for u in s["units"]):
            flags["has_ocr_blocks"] = True
        if any(u.flags.get("is_furniture") for u in s["units"]):
            flags["has_furniture"] = True
        if any(u.flags.get("is_caption") for u in s["units"]):
            flags["has_captions"] = True
        secs.append({**{k: v for k, v in s.items() if k != "units"},
                     "chars": len(body), "tier": tier, "tier_why": why,
                     "parse_flags": flags or None,
                     "n_units": len(s["units"])})

    load = lambda p: json.loads(p.read_text()) if p and p.exists() else []
    links, figs = load(links_path), load(figures_path)
    return {
        "document": doc,
        "sections": secs,
        "links": {"total": len(links),
                  "located": sum(1 for l in links if l.get("located")),
                  "by_model": sum(1 for l in links
                                  if l.get("anchored_by") == "semantic_pass")},
        "hyphen_rulings": len(load(hyphens_path)),
        "figures": {"total": len(figs),
                    "ornamental": sum(1 for f in figs
                                      if "<relevance>ornamental" in f.get("xml", ""))},
        "canonical": canon,
    }


def render_plan(p: dict, label: str) -> str:
    d: DocumentRecord = p["document"]
    out = [f"=== {label} ===",
           f"  verdict {d.verdict}"
           + (f"  OVERRIDE: {d.override_reason[:48]}" if d.override_reason else "")
           + f"   pages {d.page_count}   canonical {len(p['canonical'].text):,} chars",
           f"  hash {d.content_hash[:16]}…",
           (f"  period {d.covers_period_start} .. {d.covers_period_end} "
            f"({d.period_days} days)"
            + ("   ** NOT A YEAR — flagged, not repaired **" if d.period_flagged else "")
            if d.covers_period_start else
            "  period ** NONE PARSED — covers_period_start/end would be NULL **"),
           f"  links {p['links']['located']}/{p['links']['total']} located "
           f"({p['links']['by_model']} by the semantic pass) · "
           f"figures {p['figures']['total']} ({p['figures']['ornamental']} ornamental) · "
           f"hyphen rulings {p['hyphen_rulings']}",
           "",
           f"  {'#':>2} {'T':>1} {'heading':<46} {'pages':>7} {'chars':>6} {'flags'}"]
    for s in p["sections"]:
        pages = (f"{s['page_start']}-{s['page_end']}"
                 if s["page_start"] is not None else "—")
        flags = ",".join((s["parse_flags"] or {}).keys())
        out.append(f"  {s['sequence']:>2} {s['tier']:>1} "
                   f"{(s['heading'] or '(front matter)')[:46]:<46} {pages:>7} "
                   f"{s['chars']:>6} {flags}")
    tiers = {t: sum(1 for s in p["sections"] if s["tier"] == t) for t in "ABC"}
    ab = sum(s["chars"] for s in p["sections"] if s["tier"] in "AB")
    out += ["", f"  tiers A={tiers['A']} B={tiers['B']} C={tiers['C']} · "
                f"{ab:,} of {len(p['canonical'].text):,} chars would be sent for extraction "
                f"({ab / max(len(p['canonical'].text), 1):.0%})"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="write a conversion into the claim store")
    ap.add_argument("--md", required=True)
    ap.add_argument("--links")
    ap.add_argument("--hyphens")
    ap.add_argument("--figures")
    ap.add_argument("--label", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and write nothing (the intended first use)")
    a = ap.parse_args()

    p = plan(Path(a.md), Path(a.links) if a.links else None,
             Path(a.hyphens) if a.hyphens else None,
             Path(a.figures) if a.figures else None)
    print(render_plan(p, a.label or Path(a.md).stem))
    if not a.dry_run:
        print("\n[ingest] writing is not implemented yet — use --dry-run")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
