"""Emit a human review checklist: every place the pipeline made a judgement call.

WHY A CHECKLIST RATHER THAN "READ IT". Word-by-word review found real losses every single
time it was run, but it does not scale past a handful of documents and it spends most of
its attention on text nothing touched. Every deviation this pipeline can introduce is
already recorded somewhere -- a gate finding, an ambiguous hyphen, a registry correction,
a recovered region, a caption tag, an extracted figure value. This turns those records
into line-numbered items so review time goes where a judgement was actually made.

WHAT IS DELIBERATELY NOT LISTED. Text that came straight from the text layer, in a block
Docling typed unambiguously, with no correction applied. That is the majority of every
document and nothing in the pipeline had an opinion about it.

RANKED BY WHAT GOES WRONG IF IT IS WRONG:
    A  a wrong VALUE enters the store  -- figure data, OCR term corrections, truncations
    B  text is present but MISPLACED   -- recovered regions, nesting, structure
    C  a label is wrong                -- captions, furniture, provenance tags
Section A is worth doing properly even when short of time; C can be skimmed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _lines(md: str) -> list[str]:
    return md.splitlines()


def _find(lines: list[str], pattern: str) -> list[tuple[int, str]]:
    rx = re.compile(pattern)
    return [(i + 1, l) for i, l in enumerate(lines) if rx.search(l)]


def _tail(text: str, chars: int = 60) -> str:
    """The last few WORDS of a block, never a mid-word slice.

    text[-58:] cut "customers" to "rs", producing a snippet a reviewer could not find on
    the page it named -- the quoted text has to be searchable in the document, or the
    item cannot be actioned.
    """
    words, out = text.split(), []
    for w in reversed(words):
        if out and len(" ".join([w, *out])) > chars:
            break
        out.insert(0, w)
    return ("… " if len(out) < len(words) else "") + " ".join(out)


def _line_of(lines: list[str], needle: str) -> int | None:
    """The 1-based line carrying `needle`, matched on collapsed whitespace."""
    n = " ".join(needle.split())
    if not n:
        return None
    for i, l in enumerate(lines):
        # Never point at a comment. The document's own gate banner quotes findings
        # verbatim, so an unguarded search resolved a truncated block to line 3 -- the
        # banner describing it -- instead of the block itself.
        if l.lstrip().startswith("<!--"):
            continue
        if n in " ".join(l.split()):
            return i + 1
    return None


def _page_of(lines: list[str], lineno: int) -> str:
    """The last <!-- p.N --> marker at or above this line."""
    for i in range(lineno - 1, -1, -1):
        m = re.search(r"<!-- p\.(\d+) -->", lines[i])
        if m:
            return m.group(1)
    return "?"


def build(md_path: Path, report: dict, gate_findings: list, label: str) -> list[str]:
    md = md_path.read_text()
    lines = _lines(md)
    name = md_path.name
    out: list[str] = [f"## {label}  ({name})", ""]

    # ── A: a wrong value could enter the store ─────────────────────────────────────────
    out.append("### A. Values — check against the PDF")
    a: list[str] = []

    for ln, text in _find(lines, r"<point .*value="):
        p = _page_of(lines, ln)
        m = re.search(r'label="([^"]*)"[^>]*value="([^"]*)"', text)
        if m:
            a.append(f"- [ ] `{name}:{ln}` p.{p} — figure value **{m.group(1)} = "
                     f"{m.group(2)}** — read off an image by a vision model; confirm "
                     f"against the chart")

    fixes = report.get("ocr_term_fixes") or []
    if fixes:
        from collections import Counter
        for pair, n in Counter(f"{v} → {c}" for v, c in fixes).items():
            canon = pair.split(" → ")[-1]
            hits = _find(lines, re.escape(canon))
            where = (f" first at `{name}:{hits[0][0]}`, {len(hits)} occurrence(s) of the "
                     f"canonical form in the file" if hits else "")
            a.append(f"- [ ] `{name}` — OCR term correction applied **{n}×**: {pair}"
                     f"{where} — spot-check two or three against the PDF")

    # ONE ITEM PER BLOCK, not one per finding. The gate summarises ("2 block(s) end on a
    # dangling word") because that suits a console line; a reviewer has to visit each one,
    # so each gets its own row, its own line number, and a snippet cut at a word boundary.
    for f in gate_findings:
        if f.check != "truncation":
            continue
        for blk in f.items:
            t = " ".join((blk.get("text") or "").split())
            ln = _line_of(lines, t[-40:] if len(t) > 40 else t)
            loc = f"`{name}:{ln}`" if ln else f"`{name}`"
            a.append(f"- [ ] {loc} p.{blk.get('page_no')} — block ENDS at "
                     f"\u201c{_tail(t)}\u201d — does the sentence finish there, or was a "
                     f"continuation line dropped?")

    # A HYPHEN FOLLOWED BY A SPACE IS ALMOST ALWAYS WRONG. It is what a line-break split
    # looks like after something has flattened the newline that identified it, and 18 of
    # them reached Years 3-5 that way ("zero- emissions", "income- qualified"). The rare
    # legitimate case is a suspended hyphen -- "City- and community-wide" -- so this is
    # listed for a human rather than repaired, but it is never listed silently.
    for ln, text in _find(lines, r"[A-Za-z]+- [a-z]"):
        if text.lstrip().startswith("<!--"):
            continue
        for m in re.finditer(r"([A-Za-z]+)- ([a-z]+)", text):
            a.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — hyphen then SPACE in "
                     f"**{m.group(1)}- {m.group(2)}** — a line-break split usually joins to "
                     f"*{m.group(1)}-{m.group(2)}*; only a suspended hyphen (\"City- and "
                     f"community-wide\") keeps the space")

    amb = report.get("ambiguous_hyphens") or []
    for x, y in amb:
        hits = _find(lines, re.escape(f"{x}-{y}"))
        loc = f":{hits[0][0]}" if hits else ""
        a.append(f"- [ ] `{name}{loc}` — hyphen kept on **{x}-{y}** because the document "
                 f"gave no evidence either way; should it be *{x}{y}*?")
    out += (a or ["- (nothing in this category)"]) + [""]

    # ── B: text present but possibly misplaced ─────────────────────────────────────────
    out.append("### B. Placement — text is present; is it in the right place?")
    b: list[str] = []
    # The per-region marker only. The document header carries a SUMMARY line using the
    # same words ("N region(s) recovered by the coverage sweep"), and matching it listed
    # the run comment underneath as though it were recovered text.
    for ln, _ in _find(lines, r"^<!-- recovered by (the )?(coverage|content) sweep"):
        nxt = lines[ln] if ln < len(lines) else ""
        b.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — recovered region, "
                 f"placement INFERRED: {nxt.strip()[:80]}")
    heads = _find(lines, r"^## ")
    b.append(f"- [ ] `{name}` — section spine: {len(heads)} headings "
             f"({', '.join(h[1][3:28] for h in heads[:6])}…) — do they match the PDF's "
             f"contents page, in order and with none invented?")
    # A heading immediately followed by another heading is usually ONE heading Docling
    # split across two blocks -- Year 3 shows "STRATEGY ONE:" and "POWER OUR ELECTRICAL
    # GRID" as separate sections where the report has one.
    for i, (ln, txt) in enumerate(heads[:-1]):
        if heads[i + 1][0] - ln <= 2:
            b.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — two headings with "
                     f"nothing between them: {txt[3:40]!r} then "
                     f"{heads[i + 1][1][3:40]!r} — one heading split in two?")

    nested = _find(lines, r"^\s\s+- ")
    if nested:
        b.append(f"- [ ] `{name}:{nested[0][0]}` — {len(nested)} nested list item(s); "
                 f"confirm they really belong under the bullet above them")
    out += b + [""]

    # ── C: labels ──────────────────────────────────────────────────────────────────────
    out.append("### C. Labels — skim; a wrong one is mislabelled, not lost")
    c: list[str] = []
    caps = _find(lines, r"<!-- CAPTION:")
    for ln, _ in caps[:40]:
        nxt = lines[ln] if ln < len(lines) else ""
        c.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — tagged CAPTION: "
                 f"{nxt.strip()[:74]}")
    furn = _find(lines, r"<!-- FURNITURE:")
    for ln, _ in furn:
        nxt = lines[ln] if ln < len(lines) else ""
        c.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — tagged FURNITURE: "
                 f"{nxt.strip()[:74]}")
    for ln, text in _find(lines, r"\[OCR\]|\[text layer\]"):
        c.append(f"- [ ] `{name}:{ln}` p.{_page_of(lines, ln)} — provenance differs from "
                 f"the rest of this document: {text.strip()[:70]}")
    out += (c or ["- (nothing in this category)"]) + [""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="emit a human review checklist")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--blocks", required=True)
    ap.add_argument("--md", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--figures-json")
    a = ap.parse_args()

    from pipeline.convert_blocks import convert
    from pipeline.quality_gate import assess, find_numbers

    conv, blocks, report = convert(a.pdf, a.blocks)
    ref_md = Path(a.blocks).with_suffix("").with_suffix(".md")
    ref = ref_md.read_text() if ref_md.exists() else ""
    findings = assess(conv.text, conv.page_map, blocks,
                      reference_words=len(ref.split()),
                      reference_numbers=find_numbers(ref))
    print("\n".join(build(Path(a.md), report, findings, a.label)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
