"""Harvest every hyperlink a document points at, anchored to the verbatim text citing it.

WHY THIS IS A SOURCE-DISCOVERY MECHANISM, NOT A NICE-TO-HAVE. PLAN.md's corpus-growth
rule routes new sources through research_questions -> source_targets rather than guessing
what to search for next. A document's own outbound links are the strongest form of that
signal: the source names, at a specific claim, where its evidence lives. Year 5 carries
109 such links -- a queue of candidate sources that cost nothing to discover because the
corpus volunteered them.

WHAT EACH RECORD HAS TO CARRY TO STAY USEFUL. A bare URL list decays fast: six months on
it cannot answer "which sentence relied on this?" or "which version of that page did we
read?". So a record ties the URI to its anchor text, its page, and the character span of
the block containing it -- the same span a claim cites -- plus the harvest time and the
conversion's content_hash, so a link is always traceable to the exact reading of the
exact document that produced it.

WHAT THIS DELIBERATELY DOES NOT DO: fetch anything. Harvesting is not visiting. Whether a
URL is worth retrieving, and what it says, is a separate decision with separate costs and
its own provenance -- and the dark-matter rule is explicit that a lead queue is never a
finding.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

_LINE_GAP = 20.0     # pt; rects of one URI closer than this vertically are one anchor


def merge_link_rects(links: list[dict]) -> list[dict]:
    """Group a page's raw link rects into logical links.

    pdfplumber reports one rect per LINE, so an anchor phrase that wraps produces two
    rects sharing a URI -- real Year 5 page 7 case, where the net-zero-fire-station link
    spans x=393..564 on one line and x=154..186 on the next. Counting rects would imply
    the report cites that page twice when it cites it once.

    The same URI appearing far apart on the page stays SEPARATE, because those are two
    genuine citations and collapsing them would understate how much the document leans on
    that source.
    """
    out: list[dict] = []
    for ln in sorted(links, key=lambda l: (l["uri"], l["top"])):
        prev = out[-1] if out else None
        if (prev and prev["uri"] == ln["uri"]
                and ln["top"] - prev["bottom"] <= _LINE_GAP):
            prev["bottom"] = max(prev["bottom"], ln["bottom"])
            prev["x0"] = min(prev["x0"], ln["x0"])
            prev["x1"] = max(prev["x1"], ln["x1"])
            continue
        out.append(dict(ln))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def anchor_span(anchor: str, block: dict) -> tuple[int, int] | None:
    """Absolute (start, end) of `anchor` inside `block`, or None if it isn't there.

    Absolute, not block-relative, so a link record and a claim describe the same
    coordinate system and can be joined without a second lookup.

    Returns None rather than approximating. pdfplumber's link rectangle and its text
    extraction disagree at the margins (a rect can clip a trailing space or overshoot a
    punctuation mark), and a wrong span here would attribute a URL to a sentence that
    never cited it -- worse than recording no span at all.
    """
    a, text = _norm(anchor), block["text"]
    if not a:
        return None
    i = _norm(text).find(a)
    if i < 0:
        return None
    # Map the normalised offset back by walking words, the same technique
    # section_boundaries.locate() uses: normalisation only collapses whitespace runs, so
    # counting preceding words lands on the true position in the original string.
    n = len(_norm(text)[:i].split())
    pos, seen = 0, 0
    for m in re.finditer(r"\S+", text):
        if seen == n:
            pos = m.start()
            break
        seen += 1
    else:
        pos = len(text)
    return block["char_start"] + pos, block["char_start"] + pos + len(a)


# A sentence ends at . ! or ? followed by whitespace and a capital/quote/dash -- but NOT
# after a known abbreviation, and NOT when the period sits inside a number. Both cases are
# live in this corpus: "Dr. Missy Stults" appears throughout, and "5.4MW" / "$5,000,000"
# would otherwise split a sentence mid-figure, truncating exactly the quantity a claim
# would cite.
_ABBREV = r"(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bSt)(?<!\bAve)(?<!\bInc)(?<!\bNo)(?<!\bU\.S)"
_SENT_END = re.compile(r"(?<![0-9])" + _ABBREV + r"[.!?](?=\s+[\"\u201c(A-Z0-9-])")


def sentence_around(text: str, start: int, end: int) -> str:
    """The whole sentence containing [start, end) in the compiled spine.

    Read from the COMPILED text on purpose. In the raw converter arms a sentence may
    still be broken across visual lines, or -- on a two-column page -- interleaved with a
    neighbouring column, so a "sentence" harvested there can be a splice of two unrelated
    ones. The spine has already had blocks flattened and columns ordered, so a sentence
    read here is the sentence the document actually contains.

    Block boundaries (the blank line between blocks) always terminate a sentence: bullets
    are separate assertions, and letting one run into the next would attribute a link to
    text from a different bullet.
    """
    if not (0 <= start < len(text)) or end > len(text):
        return ""
    # rfind returns -1 when absent; -1 + 2 = 1 would silently chop the document's first
    # character, which looked like a sentence-splitting bug rather than an offset one.
    prev_break = text.rfind("\n\n", 0, start)
    lo = 0 if prev_break < 0 else prev_break + 2
    hi = text.find("\n\n", end)
    hi = len(text) if hi < 0 else hi
    # Searched unbounded then filtered, NOT finditer(text, lo, start): passing an endpos
    # truncates the string the regex can see, so the trailing lookahead (whitespace then a
    # capital) fails for a sentence end sitting right before `start` -- the boundary is
    # missed exactly when it matters most and the whole preceding sentence bleeds in.
    for m in _SENT_END.finditer(text, lo):
        if m.end() > start:
            break
        lo = m.end()
    m = _SENT_END.search(text, max(end - 1, lo), hi)
    if m:
        hi = m.end()
    return text[lo:hi].strip()


def harvest(pdf_path, blocks: list[dict], content_hash: str = "",
            spine: str = "") -> list[dict]:
    """Every link in the PDF, tied to the block whose text cites it."""
    import pdfplumber

    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get("text") and b.get("char_start") is not None:
            by_page.setdefault(b["page_no"], []).append(b)

    harvested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out: list[dict] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for ln in merge_link_rects(page.hyperlinks or []):
                crop = page.crop((max(ln["x0"] - 1, 0), max(ln["top"] - 1, 0),
                                  min(ln["x1"] + 1, page.width),
                                  min(ln["bottom"] + 1, page.height)))
                anchor = _norm(crop.extract_text() or "")
                span, owner = None, None
                for b in by_page.get(pno, []):
                    span = anchor_span(anchor, b)
                    if span:
                        owner = b
                        break
                out.append({
                    "uri": ln["uri"], "page_no": pno, "anchor_text": anchor,
                    "context_sentence": (sentence_around(spine, span[0], span[1])
                                         if span and spine else ""),
                    "char_start": span[0] if span else None,
                    "char_end": span[1] if span else None,
                    "block_kind": owner["kind"] if owner else None,
                    "located": span is not None,
                    "source_content_hash": content_hash,
                    "harvested_at": harvested_at,
                })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="harvest hyperlinks with their citing text")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--blocks", required=True, help="<out>.blocks.json from convert_docling")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    from pipeline.convert_blocks import convert
    conv, blocks, _ = convert(a.pdf, a.blocks)
    links = harvest(a.pdf, blocks, conv.content_hash, conv.text)
    Path(a.out).write_text(json.dumps(links, indent=2))

    located = sum(1 for l in links if l["located"])
    hosts: dict[str, int] = {}
    for l in links:
        h = re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", l["uri"])
        hosts[h] = hosts.get(h, 0) + 1
    print(f"[links] {len(links)} link(s), {located} anchored to a text span "
          f"({len(links) - located} unanchored)")
    print(f"[links] {len(hosts)} distinct host(s):")
    for h, n in sorted(hosts.items(), key=lambda x: -x[1]):
        print(f"[links]   {n:>3}  {h}")
    print(f"[links] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
