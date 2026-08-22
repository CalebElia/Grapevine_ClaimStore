"""The canonical text: the one string a span is allowed to be an offset into.

WHY THIS IS NOT THE MARKDOWN FILE. Two comments in every rendered document change on every
run -- the generation timestamp and the gate banner -- and the run title changes with the
command line. If claims.span_start were an offset into the FILE, regenerating a document
would move every span in the store, and it would move them SILENTLY: each one still
round-trips perfectly against the text it was written from, and points at the wrong words
in the text you now have.

So the store's coordinate space is defined here instead, by three properties:

  * it contains every word of the DOCUMENT and nothing the pipeline said about it,
  * it is unchanged by anything that changes between runs of the same conversion, and
  * every unit reports the offsets at which its own text appears in it.

WHY NOT strip_markup(). That function exists to compare a conversion against a hand-healed
reference. It replaces each comment with a single space -- leaving ragged, run-dependent
whitespace -- and keeps markdown syntax, so `## `, `- ` and `> ` all land inside spans. It
is right for counting words and wrong for indexing them.

THE TAGS ARE NOT TEXT, THEY ARE STRUCTURE. FURNITURE, CAPTION, the sweep's placement
warning and the [OCR] provenance mark are how the renderer tells ingest what it decided.
They come back as fields on a Unit rather than as characters in the text, so a caption is
findable, filterable and never mistaken for prose -- which is what tagging bought instead
of deleting.
"""
from __future__ import annotations

import hashlib
import re

from pipeline.footnotes import parse_footnote_body
from dataclasses import dataclass, field

# Comments the renderer writes ABOUT the document. Structure, not content.
_TAG = re.compile(r"^<!--\s*(?P<body>.*?)\s*-->\s*$", re.S)
_PAGE = re.compile(r"^p\.(\d+)$")
_FURNITURE = re.compile(r"^FURNITURE:\s*(.*)$", re.S)
_CAPTION = re.compile(r"^CAPTION:\s*(.*)$", re.S)
_SWEPT = re.compile(r"^recovered by (?:the )?(?:coverage|content) sweep", re.I)
_FIGURE_NOTE = re.compile(r"^(ORNAMENTAL|UNEXTRACTED) FIGURE:\s*(.*)$", re.S)
_PROVENANCE = re.compile(r"^\[(OCR|text layer)\]\s*")
_FIGCAP = re.compile(r"^\*\*Figure \(([^,]*), page (\d+)\):\*\*\s*(.*)$")

# THE DOCUMENT-LEVEL RULE, WHICH IS THE HALF THIS MODULE USED TO DROP.
# render_blocks marks a block's provenance ONLY when it differs from the document's
# dominant source -- marking all 133 blocks of a 96%-OCR report would bury the signal the
# mark exists to carry -- and states the rule itself in a header comment. Reading the
# exceptions without reading the rule inverts the meaning of an unmarked block: it means
# "text layer" in Years 1, 3, 4 and 5 and "OCR" in Year 2, and nothing recorded which.
#
# The cost was not theoretical. ingest_document sets has_ocr_blocks when a unit reports
# docling_ocr, and section_audit already demotes any section carrying that flag out of
# `clean`. Year 2's marks all say [text layer], so no unit ever reported OCR, the flag never
# fired, and all ten sections of the 96%-OCR document sat at the same `clean` grade as
# Year 3's character-exact prose. The demotion logic was correct the whole time and the
# signal never reached it.
_OCR_HEADER = re.compile(
    r"^(?P<pct>\d+)% OCR: (?P<n>\d+) of (?P<total>\d+) text blocks.*?"
    r"Dominant source: (?P<dominant>\w+)\.", re.S)

BLOCK_SEP = "\n\n"


@dataclass
class Unit:
    """One block of document text, with what the pipeline decided about it."""
    kind: str                       # title|heading|para|list_item|caption|furniture|figure
    text: str
    page_no: int | None = None
    level: int | None = None        # list nesting depth
    char_start: int = -1
    char_end: int = -1
    flags: dict = field(default_factory=dict)

    @property
    def is_prose(self) -> bool:
        """Whether this unit's words belong in the canonical text at all."""
        return self.kind != "figure" and bool(self.text.strip())


@dataclass
class Canonical:
    text: str
    content_hash: str
    units: list[Unit]
    # What an UNMARKED block means in this document, and how much of it was OCR.
    dominant_text_source: str = "pdfplumber"
    ocr_pct: int = 0

    def slice(self, a: int, b: int) -> str:
        return self.text[a:b]


def _clean(line: str) -> tuple[str, dict]:
    """Strip a leading provenance mark, returning it as a flag rather than as text."""
    flags: dict = {}
    m = _PROVENANCE.match(line)
    if m:
        flags["text_source"] = "docling_ocr" if m.group(1) == "OCR" else "pdfplumber"
        line = line[m.end():]
    return line, flags


def document_provenance(md: str) -> tuple[str, int]:
    """(dominant text source, OCR percentage) from the renderer's header comment.

    Defaults to pdfplumber/0 when absent, which is what every pre-provenance conversion in
    this corpus is -- but a document whose header is missing is not the same as a document
    known to have a text layer, so callers that grade quality should prefer an explicit
    flag over this default.
    """
    # LINE BY LINE, because _TAG is anchored ^...$ WITHOUT re.M -- it is built to match one
    # already-stripped line, which is how parse() calls it. Running finditer over a
    # multi-line slice matches nothing and returns the default, silently: the first version
    # of this reported every document as 0% OCR, including the one that is 96%.
    for raw in md.splitlines()[:40]:
        m = _TAG.match(raw.strip())
        if m and (h := _OCR_HEADER.match(m.group("body").strip())):
            return h.group("dominant"), int(h.group("pct"))
    return "pdfplumber", 0


def parse(md: str, dominant: str | None = None) -> list[Unit]:
    """Markdown -> units, with the renderer's tags resolved onto them.

    A tag applies to the NEXT text block, which is how render_blocks emits them: the
    comment announces what is coming rather than describing what came.
    """
    units: list[Unit] = []
    page: int | None = None
    pending: dict = {}
    in_figure = False
    # EVERY UNIT CARRIES A SOURCE, NEVER AN ABSENCE. An unmarked block inherits the
    # document's dominant source, so "this text came from OCR" is a fact on the row rather
    # than something a later reader has to reconstruct from which document it is in.
    if dominant is None:
        dominant, _ = document_provenance(md)

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        if line.lstrip().startswith("<figure_description"):
            in_figure = True
        if in_figure:
            if "</figure_description>" in line:
                in_figure = False
            continue                                # vision output, not document text

        m = _TAG.match(line.strip())
        if m:
            body = m.group("body")
            if (p := _PAGE.match(body)):
                page = int(p.group(1))
            elif _FURNITURE.match(body):
                pending["is_furniture"] = True
            elif _CAPTION.match(body):
                pending["is_caption"] = True
            elif _SWEPT.match(body):
                pending["placement_inferred"] = True
            elif (f := _FIGURE_NOTE.match(body)):
                units.append(Unit("figure", "", page,
                                  flags={"figure_state": f.group(1).lower(),
                                         "note": f.group(2)}))
            continue                                # every other comment is pipeline chatter

        indent = len(line) - len(line.lstrip())
        s = line.strip()

        if s.startswith("# "):
            kind, text, level = "title", s[2:], None
        elif s.startswith("## "):
            kind, text, level = "heading", s[3:], None
        elif s.startswith("- "):
            kind, text, level = "list_item", s[2:], indent // 2
        elif s.startswith("> "):
            text = s[2:]
            kind = ("caption" if pending.get("is_caption")
                    else "furniture" if pending.get("is_furniture") else "para")
            level = None
        elif (fc := _FIGCAP.match(s)):
            units.append(Unit("caption", fc.group(3), int(fc.group(2)),
                              flags={"is_caption": True, "figure_label": fc.group(1)}))
            pending = {}
            continue
        else:
            kind, text, level = "para", s, None

        text, flags = _clean(text)
        flags.setdefault("text_source", dominant)

        # A FOOTNOTE IS NOT GENERIC FURNITURE. Both are non-prose page apparatus, but a
        # footnote carries a NUMBER that points back into the body text, and in this corpus
        # those seven blocks name the officer responsible for each strategy. Retyping only:
        # the characters and their offsets are untouched, because every stored claim span is
        # an offset into this string and a reclassification that moved text would invalidate
        # the store.
        if kind == "furniture":
            fn = parse_footnote_body(text)
            if fn:
                kind = "footnote"
                flags["footnote_number"] = fn["number"]
        units.append(Unit(kind, text, page, level, flags={**pending, **flags}))
        pending = {}

    return units


def build(md: str) -> Canonical:
    """Markdown -> the canonical text, its hash, and units carrying their own offsets.

    Offsets are assigned by CONSTRUCTION rather than by searching for each unit's text
    afterwards: two identical bullets on one page would both find the first occurrence, and
    the second claim would cite the first bullet while round-tripping perfectly.
    """
    dominant, ocr_pct = document_provenance(md)
    units = parse(md, dominant)
    parts: list[str] = []
    at = 0
    for u in units:
        if not u.is_prose:
            continue
        u.char_start = at
        u.char_end = at + len(u.text)
        parts.append(u.text)
        at = u.char_end + len(BLOCK_SEP)
    text = BLOCK_SEP.join(parts)
    return Canonical(text, hashlib.sha256(text.encode()).hexdigest(), units,
                     dominant, ocr_pct)


def sections(canon: Canonical) -> list[dict]:
    """Units grouped under their `##` heading, ready for document_sections.

    Front matter -- everything before the first heading -- is its own section with a NULL
    heading rather than being attached to the first one or dropped. On these reports it
    holds the title, the coverage period and the sign-off, all of which ingest wants.
    """
    out: list[dict] = []
    cur: dict | None = None
    for u in canon.units:
        if not u.is_prose:
            continue
        if u.kind == "heading" or cur is None:
            if cur is not None:
                out.append(cur)
            cur = {"sequence": len(out),
                   "heading": u.text if u.kind == "heading" else None,
                   "char_start": u.char_start, "char_end": u.char_end,
                   "page_start": u.page_no, "page_end": u.page_no, "units": [u]}
            if u.kind != "heading":
                cur["char_start"] = u.char_start
            continue
        cur["char_end"] = u.char_end
        if u.page_no is not None:
            cur["page_end"] = u.page_no
            if cur["page_start"] is None:
                cur["page_start"] = u.page_no
        cur["units"].append(u)
    if cur is not None:
        out.append(cur)

    # A CAPTION THAT OPENS THE NEXT SECTION'S PAGE BELONGS TO IT. Years 4 and 5 open each
    # section with a full-bleed photograph, and its caption is emitted BEFORE the heading,
    # so it lands at the end of the previous section. Fifteen sections across the corpus
    # end on a caption and fourteen of them are that section's own closing photo -- the
    # page tells them apart. Year 5's page 23 carries "Emergency kit supplies distribution."
    # and YEAR 6 PRIORITIES; a caption sharing a page with the NEXT heading is that
    # section's opening image, not this one's parting shot.
    #
    # No claim is at risk either way -- a caption is tagged and never extracted -- but the
    # section's char range and content_hash would otherwise cover text belonging to its
    # neighbour, and a figure attributed to the wrong section is wrong in the store.
    for i in range(len(out) - 1):
        here, nxt = out[i], out[i + 1]
        prose = [u for u in here["units"] if u.is_prose]
        head = next((u for u in nxt["units"] if u.is_prose), None)
        if (len(prose) > 1 and prose[-1].kind == "caption" and head is not None
                and prose[-1].page_no is not None
                and prose[-1].page_no == head.page_no):
            moved = prose[-1]
            here["units"].remove(moved)
            nxt["units"].insert(0, moved)
            here["char_end"] = max(u.char_end for u in here["units"] if u.is_prose)
            nxt["char_start"] = moved.char_start
            if here["page_end"] is not None and prose[-2].page_no is not None:
                here["page_end"] = prose[-2].page_no

    for s in out:
        s["content_hash"] = hashlib.sha256(
            canon.text[s["char_start"]:s["char_end"]].encode()).hexdigest()
    return out
