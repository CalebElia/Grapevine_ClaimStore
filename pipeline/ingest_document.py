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
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pipeline.canonical import Canonical, build, sections

def _pipeline_version() -> str:
    """The git sha of the code that produced this conversion, so a converter change is
    detectable rather than merely survivable."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:
        return "?"


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
    period_source: str = "unknown"
    period_note: str | None = None


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


def read_period_registry(path: Path | None, doc_key: str) -> dict | None:
    """A human's ruling on a period the document does not state, or None.

    NEVER OVERRIDES A STATED PERIOD -- the caller applies this only when the document
    printed nothing parseable. A stated range is evidence; this is an inference, and the
    two are stored under different covers_period_source values so a timeline query can
    tell a date the City published from one we decided was probably right.
    """
    if not path or not path.exists():
        return None
    for e in json.loads(path.read_text()).get("periods", []):
        if e.get("document") == doc_key:
            return e
    return None


def plan(md_path: Path, links_path: Path | None = None,
         hyphens_path: Path | None = None, figures_path: Path | None = None,
         pictures_path: Path | None = None, periods_path: Path | None = None) -> dict:
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
    links, figs, pics = load(links_path), load(figures_path), load(pictures_path)

    # A figure's bbox lives in pictures.json, not figures.json -- extract_figures records
    # what it read, not where it read it. Matched on (page, label), which is unique in this
    # corpus; a page with two figures of one label would need a finer key and is not
    # invented here.
    by_pic = {(x["page_no"], x.get("top_label")): x for x in pics}
    for f in figs:
        f["_bbox"] = (by_pic.get((f["page_no"], f.get("top_label")), {}) or {}).get("bbox")
        f["_conf"] = f.get("top_conf")

    ruling = read_period_registry(periods_path, md_path.stem.replace("-reviewed", ""))
    if ruling and not doc.covers_period_start:
        doc.covers_period_start = ruling["covers_period_start"]
        doc.covers_period_end = ruling["covers_period_end"]
        doc.period_source = "human_estimate"
        doc.period_note = ruling["note"]
    elif doc.covers_period_start:
        doc.period_source = "stated"

    return {
        "document": doc,
        "sections": secs,
        "title": next((u.text for u in canon.units if u.kind == "title"), md_path.stem),
        "converter_version": _pipeline_version(),
        "links": {"records": links, "total": len(links),
                  "located": sum(1 for l in links if l.get("located")),
                  "by_model": sum(1 for l in links
                                  if l.get("anchored_by") == "semantic_pass")},
        "hyphen_rulings": len(load(hyphens_path)),
        "figures": {"records": figs, "total": len(figs),
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
            + f"   [{d.period_source}]"
            + (f"\n    note: {d.period_note[:96]}" if d.period_note else "")
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


DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")


def write(p: dict, md_path: Path, jurisdiction_id: int, doc_type: str,
          source_url: str | None, dsn: str) -> tuple[int, dict]:
    """Write the plan. Idempotent on the markdown path; refuses on a changed conversion.

    REFUSING ON A CHANGED HASH IS THE POINT. A span is an offset into one exact string. If
    the conversion changed, every stored span still round-trips against the text it was
    written from and points at different words in the text that is now there -- which is
    silent, and which is why re-ingesting a re-converted document must stop rather than
    update in place. Deleting the document and its claims is a decision for a person.
    """
    import psycopg

    d: DocumentRecord = p["document"]
    if d.verdict == "REFUSE" and not d.override_reason:
        raise SystemExit("[ingest] REFUSED conversion and no override reason recorded")

    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id, content_hash FROM documents WHERE markdown_path = %s",
                    (str(md_path),))
        if (row := cur.fetchone()):
            doc_id, seen = row
            if seen != d.content_hash:
                raise SystemExit(
                    f"[ingest] document {doc_id} was ingested from a DIFFERENT conversion\n"
                    f"         stored {seen[:16]}…  now {d.content_hash[:16]}…\n"
                    f"         every span on it points into the old text. Remove the "
                    f"document and its claims deliberately, then re-ingest.")
            cur.execute("DELETE FROM document_sections WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM document_figures  WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM document_links    WHERE document_id = %s", (doc_id,))
        else:
            cur.execute(
                """INSERT INTO documents
                     (jurisdiction_id, doc_type, title, source_url, markdown_path,
                      page_count, content_hash, covers_period_start, covers_period_end,
                      covers_period_source, covers_period_note, converter,
                      converter_version, parse_verdict, parse_override_reason)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (jurisdiction_id, doc_type, p["title"], source_url, str(md_path),
                 d.page_count, d.content_hash, d.covers_period_start, d.covers_period_end,
                 d.period_source, d.period_note, "docling+pdfplumber",
                 p["converter_version"], d.verdict, d.override_reason))
            doc_id = cur.fetchone()[0]

        for s in p["sections"]:
            cur.execute(
                """INSERT INTO document_sections
                     (document_id, sequence, heading, char_start, char_end, page_start,
                      page_end, extraction_tier, tier_assigned_by, content_hash,
                      parse_flags)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (doc_id, s["sequence"], s["heading"], s["char_start"], s["char_end"],
                 s["page_start"], s["page_end"], s["tier"], "ingest_document",
                 s["content_hash"], json.dumps(s["parse_flags"]) if s["parse_flags"]
                 else None))
        counts["sections"] = len(p["sections"])

        pts = 0
        for f in p["figures"]["records"]:
            cur.execute(
                """INSERT INTO document_figures
                     (document_id, page_no, bbox, classifier_label, classifier_conf,
                      crop_path, crop_dpi, extracted_by, prompt_version, raw_xml,
                      extracted_at, source_content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (doc_id, f["page_no"], json.dumps(f.get("_bbox") or []),
                 f.get("top_label") or "unknown", f.get("_conf"), f.get("crop_path"),
                 600, f.get("deployment") or "unknown", "xml-v1",
                 f.get("xml") or "(empty)", f.get("extracted_at"), d.content_hash))
            fig_id = cur.fetchone()[0]
            # An ORNAMENTAL figure is stored with its verdict and NO data points. An
            # absent row is indistinguishable from one nobody looked at.
            for pt in re.finditer(r'<point\s+([^/]*)/>', f.get("xml") or ""):
                at = dict(re.findall(r'(\w+)="([^"]*)"', pt.group(1)))
                if not at.get("label") or not at.get("value"):
                    continue
                cur.execute(
                    """INSERT INTO figure_data_points
                         (figure_id, label, value_text, unit, model_confidence)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (fig_id, at["label"], at["value"], at.get("unit"),
                     at.get("confidence")))
                pts += 1
        counts["figures"] = len(p["figures"]["records"])
        counts["data_points"] = pts

        for l in p["links"]["records"]:
            cur.execute(
                """INSERT INTO document_links
                     (document_id, uri, anchor_text, context_sentence, page_no,
                      char_start, char_end, located, source_content_hash, harvested_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (doc_id, l["uri"], (l.get("anchor_text") or l["uri"])[:2000],
                 l.get("context_sentence"), l.get("page_no"), l.get("char_start"),
                 l.get("char_end"), bool(l.get("located")), l.get("source_content_hash"),
                 l.get("harvested_at")))
        counts["links"] = len(p["links"]["records"])
        c.commit()
    return doc_id, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="write a conversion into the claim store")
    ap.add_argument("--md", required=True)
    ap.add_argument("--links")
    ap.add_argument("--hyphens")
    ap.add_argument("--figures")
    ap.add_argument("--pictures", help="<doc>-pictures.json; carries each figure's bbox")
    ap.add_argument("--periods", help="registries/<juris>/document_periods.json")
    ap.add_argument("--jurisdiction", type=int, default=1)
    ap.add_argument("--doc-type", default="annual_report")
    ap.add_argument("--source-url")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and write nothing (the intended first use)")
    a = ap.parse_args()

    opt = lambda v: Path(v) if v else None
    p = plan(Path(a.md), opt(a.links), opt(a.hyphens), opt(a.figures),
             opt(a.pictures), opt(a.periods))
    print(render_plan(p, a.label or Path(a.md).stem))
    if a.dry_run:
        return 0
    doc_id, counts = write(p, Path(a.md), a.jurisdiction, a.doc_type,
                           a.source_url, a.dsn or DSN)
    print(f"\n[ingest] document {doc_id}: " +
          " · ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
