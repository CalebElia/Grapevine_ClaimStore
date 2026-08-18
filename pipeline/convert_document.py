"""PDF -> text, in one envelope every backend must satisfy.

WHY AN ENVELOPE AND NOT JUST TEXT. A claim in this store cites a character span. A span is
meaningless without three things travelling beside it: which conversion produced the text
(`converter`), the exact bytes it produced (`content_hash`), and where in the physical
document a given offset lives (`page_map`). Drop any one and a citation silently degrades --
change converters and every offset in the corpus now points somewhere slightly different,
with nothing raising.

WHY THE BACKEND IS A PARAMETER. Measured on the same five A2Zero reports, pdfplumber ranges
from near-perfect (year 5: 7,146 words, matching its reference) to catastrophic (year 2: 234
words, zero of 16 dollar figures, clean exit, no error). No single converter is safe across a
corpus, so the choice has to be per-document, recorded, and revisitable -- never compiled in.

THE PAGE MAP IS THE CITATION SPINE. `page_for(offset)` is what turns "char 4,182" into "page
9", which is the granularity a human can actually check against the original. pdfplumber gives
this for free; any backend added later must supply it or the audit cannot locate what it flags.

Usage:
    python -m pipeline.convert_document --pdf report.pdf
    python -m pipeline.convert_document --pdf report.pdf --out converted.json
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).parent.parent
CACHE = REPO / ".cache" / "conversions"
PAGE_SEP = "\n\n"          # inserted between pages; counted in offsets, never inside a span


@dataclass
class Conversion:
    """One conversion of one PDF. Everything a span needs to stay meaningful."""
    source_path: str
    converter: str
    converter_version: str
    text: str
    page_map: list[tuple[int, int, int]]     # (page_no, char_start, char_end)
    content_hash: str
    n_pages: int
    converted_at: str

    def page_for(self, offset: int) -> int | None:
        """Which physical page a character offset falls on. The citation primitive."""
        for page_no, lo, hi in self.page_map:
            if lo <= offset < hi:
                return page_no
        return None

    def slice(self, start: int, end: int) -> str:
        return self.text[start:end]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["page_map"] = [list(x) for x in self.page_map]
        return d


def _assemble(pages: list[str]) -> tuple[str, list[tuple[int, int, int]]]:
    """Join pages and record where each one landed. Offsets are into the JOINED text."""
    parts, page_map, cursor = [], [], 0
    for i, body in enumerate(pages, 1):
        start = cursor
        parts.append(body)
        cursor += len(body)
        page_map.append((i, start, cursor))
        if i < len(pages):
            parts.append(PAGE_SEP)
            cursor += len(PAGE_SEP)
    return "".join(parts), page_map


# The trailing footer-number bleed, MEASURED not assumed. Checked every page's raw
# trailing token against its own 1-based page index across a24-page real report: page 1
# ends "...2025\n1", page 23 ends "...level. 23", page 24 ends "...org.\n24" -- 18 of 24
# pages end in exactly their own page number, on its own line or glued to the last
# sentence with a single space. Validating the strip against the ACTUAL page index (not
# just "looks like a small number") is what makes this safe: real content coincidentally
# being both the LAST token on a page AND numerically equal to that exact page's ordinal
# position is not a thing that happens by chance across 24 pages.
#
# Left unstripped, this is what corrupted the human review workbook: pdfplumber's raw
# text for the Year 5 closing section is genuinely well-formed --
# "...household\nlevel. 23\n\nCLOSING\nA2ZERO is our community's plan..." -- but the
# trailing "23" from the PRIOR page reads as glued onto the following heading once
# whitespace is collapsed for display, and it has no place in a claim's verbatim text
# regardless of formatting: a page footer is not part of the document's prose.
_FOOTER_TAIL = re.compile(r"\s*\b(\d{1,3})\s*$")


def _strip_footer_number(text: str, page_no: int) -> str:
    m = _FOOTER_TAIL.search(text)
    if m and int(m.group(1)) == page_no:
        return text[:m.start()]
    return text


def convert_pdfplumber(pdf_path: Path) -> Conversion:
    import pdfplumber
    try:
        import importlib.metadata as _m
        ver = _m.version("pdfplumber")
    except Exception:
        ver = "unknown"
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = [_strip_footer_number(p.extract_text() or "", i)
                for i, p in enumerate(pdf.pages, 1)]
    text, page_map = _assemble(pages)
    return Conversion(
        source_path=str(pdf_path), converter="pdfplumber", converter_version=ver,
        text=text, page_map=page_map,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        n_pages=len(pages),
        converted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def convert_from_markdown(md_path: Path, label: str) -> Conversion:
    """Wrap an ALREADY-CONVERTED markdown file in the same envelope.

    Needed because the existing corpus markdown came from two earlier converters
    (Docling+Gemini for years 1-2, pdfplumber+Claude for years 3-5) and has to be
    comparable against fresh conversions. Page map is a single synthetic page: these files
    carry no page anchors, which is itself a reason to prefer converting from PDF.
    """
    text = md_path.read_text()
    return Conversion(
        source_path=str(md_path), converter=label, converter_version="preexisting",
        text=text, page_map=[(1, 0, len(text))],
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        n_pages=1,
        converted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


BACKENDS = {"pdfplumber": convert_pdfplumber}


def convert(pdf_path: str | Path, converter: str = "pdfplumber",
            use_cache: bool = True) -> Conversion:
    pdf_path = Path(pdf_path)
    if converter not in BACKENDS:
        raise ValueError(f"unknown converter {converter!r}; have {sorted(BACKENDS)}")
    key = hashlib.sha256(f"{pdf_path.resolve()}::{converter}".encode()).hexdigest()[:16]
    cached = CACHE / f"{key}.json"
    if use_cache and cached.exists():
        d = json.loads(cached.read_text())
        d["page_map"] = [tuple(x) for x in d["page_map"]]
        return Conversion(**d)
    conv = BACKENDS[converter](pdf_path)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(conv.to_dict()))
    return conv


def main() -> int:
    ap = argparse.ArgumentParser(description="convert a PDF into the standard envelope")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--converter", default="pdfplumber", choices=sorted(BACKENDS))
    ap.add_argument("--out")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    c = convert(a.pdf, a.converter, use_cache=not a.no_cache)
    print(f"{Path(c.source_path).name}  via {c.converter} {c.converter_version}")
    print(f"  pages {c.n_pages}   chars {len(c.text):,}   words {len(c.text.split()):,}")
    print(f"  hash  {c.content_hash[:16]}…")
    # Prove the page map actually resolves, rather than asserting it does.
    for off in (0, len(c.text) // 2, max(len(c.text) - 1, 0)):
        print(f"  offset {off:>7,} -> page {c.page_for(off)}")
    if a.out:
        Path(a.out).write_text(json.dumps(c.to_dict(), indent=1))
        print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
