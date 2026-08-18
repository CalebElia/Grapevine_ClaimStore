"""The pdfplumber <-> CU handoff: CU's headings decide WHERE sections start; pdfplumber's
text is what a claim actually cites.

WHY THIS SPLIT. Measured across the bake-off: CU correctly separates and orders headings
(confirmed on the Year 5 CLOSING case, and on modelling the Table of Contents as an actual
HTML table) where pdfplumber has no structural model at all and can scramble reading order
on multi-column pages. But pdfplumber has the better verbatim fidelity of the two, and
carries exact page-anchored offsets CU's markdown does not. So: use CU to find section
BOUNDARIES, then locate those same boundaries inside pdfplumber's text by content, and cite
the pdfplumber span, never the CU one.

REPEATED HEADINGS ARE PAGE CONTINUATIONS, NOT NEW SECTIONS. Measured on the real Year 5
CU output: "GREENHOUSE GAS EMISSIONS SUMMARY" appears twice consecutively, "3: ENERGY
EFFICIENCY" three times, always immediately adjacent to a heading sharing the same core
title -- CU repeats a strategy's heading each time that strategy's content resumes on a new
page, sometimes with a "STRATEGY N:" prefix and sometimes without (the report's own
formatting varies, not a CU artifact). 18 raw headings on the real document collapse to 11
logical sections this way, matching the report's actual structure: Introduction, GHG
Summary, Strategies 1-7, Year 6 Priorities, Closing.

THE ANCHOR SEARCH CAN FAIL, AND MUST SAY SO. A section whose opening words were rewritten,
reordered, or dropped between CU and pdfplumber (the two converters disagree constantly on
small things -- whitespace, link syntax, bullet glyphs) will not be found by exact
substring search. `located=False` on a Section is not an error to hide; it means that
section's boundary is unverified and should not be trusted for citation until a human or a
looser match resolves it. Silently guessing a nearby offset would be the same mistake as
guessing a chart value.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING_LINE = re.compile(r"^##\s+(.+?)\s*$", re.M)
# Strip a leading "STRATEGY N:" or bare "N:" so "STRATEGY 2: BENEFICIAL ELECTRIFICATION"
# and its continuation "2: BENEFICIAL ELECTRIFICATION" reduce to the same core.
_NUM_PREFIX = re.compile(r"^(?:STRATEGY\s+)?\d+\s*:\s*", re.I)
# The leading number ALONE. Measured on Year 4: a continuation can carry a WHOLLY
# DIFFERENT title from its main heading -- "STRATEGY 1: Powering Our Electrical Grid..."
# followed later by "STRATEGY 1: 100% RENEWABLES" -- so comparing the remaining text
# after the prefix (Year 5's pattern) fails to merge them; only the number survives
# both. Numbered headings are grouped by number; everything else still needs an exact
# core-text match, since a non-numbered heading has no such anchor.
_LEADING_NUM = re.compile(r"^(?:STRATEGY\s+)?(\d+)\s*:", re.I)


@dataclass
class Section:
    heading: str                  # CU's heading text, first occurrence verbatim
    anchor: str                   # opening phrase used to locate this section
    pdf_start: int | None = None  # located span in pdfplumber's text
    pdf_end: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    located: bool = False
    words: int = 0


def _core(heading: str) -> str:
    """The part of a heading that survives a page-continuation repeat."""
    return _NUM_PREFIX.sub("", heading).strip().upper()


def _same_section(a: str, b: str) -> bool:
    """Two consecutive headings are the same logical section if they share a leading
    strategy number (continuation, wording may differ entirely -- see _LEADING_NUM), or
    -- for anything unnumbered -- if their core text matches exactly (Year 5's repeated
    "GREENHOUSE GAS EMISSIONS SUMMARY").
    """
    ma, mb = _LEADING_NUM.match(a), _LEADING_NUM.match(b)
    if ma and mb:
        return ma.group(1) == mb.group(1)
    if ma or mb:
        return False    # one numbered, one not -- never the same section
    return bool(_core(a)) and _core(a) == _core(b)


def _norm_quotes(s: str) -> str:
    """Collapse every character-class difference measured between CU and pdfplumber that
    has NOTHING to do with whether the anchor is genuinely present -- each was found by a
    real anchor failing to locate, not anticipated in advance:

      quotes      CU straightens curly quotes; pdfplumber preserves the PDF's own glyphs.
                  "community's" (straight ') vs "community's" (curly U+2019).
      dashes      CU flattens en/em dashes to a plain hyphen; pdfplumber does not.
                  "Energy efficiency - or..." vs "Energy efficiency – or...".
      superscript CU renders "A2ZERO"; pdfplumber preserves the PDF's superscript glyph,
                  "A²ZERO". Two of seven Year 4 strategy anchors failed on this
                  alone -- it is not a one-off.

    None of these mean the text disagrees. Normalising before comparison is what keeps a
    typography difference from reading as a located-vs-not-located section boundary.
    """
    s = re.sub(r"[‘’]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"[‒-―]", "-", s)     # en dash, em dash, figure/horizontal bar
    s = s.replace("²", "2")                # superscript two, as in "A²ZERO"
    return s


def _anchor_phrase(body: str, n_words: int = 8) -> str:
    """First N words of real prose, skipping list markers and link syntax that pdfplumber
    and CU are likely to render differently -- an exact-substring search must not fail on
    formatting alone.
    """
    # CU emits a MARKDOWN-ESCAPED leading dash ("\- The City...", backslash included),
    # not a plain "-". Measured: the unescaped regex left the backslash in the anchor and
    # it never matched pdfplumber's plain "- The City...". Strip the optional backslash too.
    text = re.sub(r"^\s*\\?[-*]\s*", "", body.strip())
    # A STRAY LEADING PERIOD. Measured directly in CU's own Year 4 output: a section body
    # begins ". Supported the [gas leaf blower phase-out](...)" -- CU appears to have
    # stripped a list marker (numeral, letter) and left its trailing punctuation behind.
    # Not present in pdfplumber's text at all, so an anchor starting with it can never
    # match. Strip once more after the dash/asterisk pass, since the period can follow it.
    text = re.sub(r"^\s*\.\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # markdown links -> their text
    words = text.split()
    return _norm_quotes(" ".join(words[:n_words]))


def cu_sections(cu_text: str) -> list[Section]:
    """Split CU's markdown on '## ' headings, merging consecutive continuations."""
    matches = list(_HEADING_LINE.finditer(cu_text))
    raw = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(cu_text)
        raw.append((m.group(1).strip(), cu_text[body_start:body_end]))

    out: list[Section] = []
    for heading, body in raw:
        if out and _same_section(heading, out[-1].heading):
            continue    # continuation of the previous section -- same anchor still applies
        out.append(Section(heading=heading, anchor=_anchor_phrase(body)))
    return out


def locate(sections: list[Section], pdf_conv) -> list[Section]:
    """Find each section's start in pdfplumber's text via its anchor phrase.

    Takes a `Conversion` object (pipeline.convert_document), not a bare string plus a
    separate page-lookup callback. MEASURED WHY THIS MATTERS: an earlier version took
    text and page_for as independent arguments, and a stale on-disk text dump (46,038
    chars, written before a later footer-stripping fix landed) was paired with a freshly
    computed page_for (45,989 chars) from the SAME conversion re-run. The mismatch did
    not raise -- it silently returned None for a page lookup near the end of the
    document, on whichever section happened to land past the shorter text's length.
    Tying text and page_for to one Conversion object makes that drift structurally
    impossible rather than something to remember to avoid.

    A section's END is the NEXT LOCATED section's start (or end of document) -- never
    independently searched, so sections can't overlap and no span goes unowned.
    """
    norm = lambda s: _norm_quotes(" ".join(s.split()))
    hay = pdf_conv.text
    hay_norm = norm(hay)

    for sec in sections:
        idx = hay_norm.find(norm(sec.anchor)) if sec.anchor else -1
        if idx < 0:
            continue
        # Map the normalised-text offset back to a real offset in pdf_text. norm() only
        # ever collapses whitespace RUNS, never removes non-space characters, so counting
        # how many words precede the match and walking that many words into the ORIGINAL
        # text lands on the true position -- exact, no char-by-char whitespace bookkeeping.
        words_before = hay_norm[:idx].split()
        n = len(words_before)
        pos, seen = 0, 0
        for m in re.finditer(r"\S+", hay):
            if seen == n:
                pos = m.start()
                break
            seen += 1
        else:
            pos = len(hay)
        sec.pdf_start = pos
        sec.located = True

    located = [s for s in sections if s.located]
    for i, sec in enumerate(located):
        sec.pdf_end = located[i + 1].pdf_start if i + 1 < len(located) else len(hay)
        sec.words = len(hay[sec.pdf_start:sec.pdf_end].split())
        sec.page_start = pdf_conv.page_for(sec.pdf_start)
        sec.page_end = pdf_conv.page_for(max(sec.pdf_end - 1, sec.pdf_start))
    return sections


def build(cu_text: str, pdf_conv) -> list[Section]:
    return locate(cu_sections(cu_text), pdf_conv)


def report(sections: list[Section]) -> None:
    ok = [s for s in sections if s.located]
    print(f"{len(sections)} logical sections, {len(ok)} located in pdfplumber's text "
          f"({len(sections)-len(ok)} NOT located -- unverified, do not cite)")
    print(f"\n{'heading':<40}{'pages':>10}{'words':>8}{'anchor':<40}")
    print("-" * 98)
    for s in sections:
        pages = f"{s.page_start}-{s.page_end}" if s.page_start else "?"
        mark = "" if s.located else "  <-- NOT LOCATED"
        print(f"{s.heading[:38]:<40}{pages:>10}{s.words:>8}  {s.anchor[:34]:<34}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description="pdfplumber<->CU section boundary handoff")
    ap.add_argument("--cu", required=True, help="CU markdown output")
    ap.add_argument("--pdf", required=True,
                    help="original PDF -- pdfplumber's text is converted fresh here, "
                         "never read from a separate dump, so it can't drift out of "
                         "sync with the page map computed from the same conversion")
    a = ap.parse_args()
    from pipeline.convert_document import convert
    cu_text = Path(a.cu).read_text()
    conv = convert(a.pdf, "pdfplumber")
    report(build(cu_text, conv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
