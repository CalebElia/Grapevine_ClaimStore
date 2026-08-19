"""Docling proposes block boundaries and reading order; pdfplumber supplies the exact
characters inside each block.

WHY NEITHER CONVERTER ALONE IS ENOUGH, MEASURED ON a2zero-year5.pdf:

  pdfplumber  has no layout model at all -- it reads glyphs in x-then-y order, which is
              correct by accident on a single-column page and catastrophic on a
              multi-column one. Real page 6 output: "- Our Solarize program reached
              5.4MW of - In deep collaboration with the Ann Arbor". Every line on that
              page interleaves both columns. extract_text(layout=True) returns blank
              here, and a midpoint crop cuts words in half ("Fire Sta" / "ation 4")
              because the gutter sits at x=300 while the left column's ragged right edge
              varies from 98 to 264. The columns also begin BELOW a full-width heading,
              so no page-wide gutter exists to find -- a page-level detector scanned all
              24 pages and missed page 6 entirely.

  docling     reads the order correctly but fragments styled glyph runs. It renders this
              corpus's central term A2ZERO as "A 2 ZERO" 40 times out of 53, versus
              pdfplumber's 51-of-55 correct. Citing "A 2 ZERO" would be worse than the
              column problem, because it is wrong in a way that looks deliberate.

  BOTH        Docling's per-block bbox + reading order, with pdfplumber cropped to each
              box for the characters, reproduces all three hand-verified corrections on
              page 6 exactly AND keeps A2ZERO intact.

This is the same rule the rest of this pipeline runs on -- the structural model proposes,
the character-exact reader decides -- moved one layer down, from sections to blocks.

THE BLOCK JSON COMES FROM A SEPARATE ENV. Docling lives in `grapevine-docling` (torch,
transformers, several GB); pdfplumber lives in the project env. They cannot import each
other, so convert_docling.py writes a `<out>.blocks.json` sidecar and this module reads
it. That split is also why block geometry is plain JSON here rather than Docling objects.
"""
from __future__ import annotations

import re

_SPLIT = re.compile(r"(\w+)-\s*\n\s*(\w+)")
_WORD = re.compile(r"[A-Za-z]{2,}")
_HYPHENATED = re.compile(r"[A-Za-z]+-[A-Za-z]+")


def bbox_to_crop(bbox: tuple[float, float, float, float], coord_origin: str,
                 page_h: float, page_w: float,
                 pad: float = 2.0) -> tuple[float, float, float, float]:
    """Docling (l, t, r, b) in PDF points -> pdfplumber (x0, top, x1, bottom).

    Docling reports BOTTOMLEFT origin (y grows upward); pdfplumber crops in TOP-LEFT
    space (y grows downward). The flip is `page_h - y`. Getting its sign wrong crops a
    mirrored region that still lands on the page and still contains text, so it reads as
    plausible output rather than raising -- which is why coord_origin is honoured
    explicitly instead of assumed.

    `pad` compensates for glyph overhang past the reported box; without it, descenders
    and italic tails clip. It is clamped to the page so padding never produces a box
    pdfplumber will reject.
    """
    l, t, r, b = bbox
    if "BOTTOMLEFT" in coord_origin:
        top, bottom = page_h - t, page_h - b
    else:
        top, bottom = t, b
    x0, x1 = sorted((l, r))
    top, bottom = sorted((top, bottom))
    return (max(x0 - pad, 0.0), max(top - pad, 0.0),
            min(x1 + pad, page_w), min(bottom + pad, page_h))


def flatten_block(raw: str) -> str:
    """Collapse one block's internal whitespace to single spaces.

    A block is a semantic paragraph, so the line breaks inside it are the PDF's visual
    line-wrapping, carrying no meaning. Flattening here is what stops the output from
    mirroring the source's column width -- the reported "newline structure directly
    reflects the PDF" problem. Breaks BETWEEN blocks are preserved by the caller, since
    those are real.
    """
    return " ".join(raw.split())


def build_evidence(text: str) -> tuple[set[str], set[str]]:
    """(words, hyphenated_compounds) observed in text, EXCLUDING every line-break-hyphen
    sequence, so the evidence is independent of the cases it will be used to judge.

    The bug this guards, found on the real document: an earlier version built the
    vocabulary from text with the splits already joined, which made every joined form
    trivially present -- "science-\\nbased" then scored as though "sciencebased" were an
    attested word. Evidence that includes the thing it is judging is not evidence.
    """
    clean = _SPLIT.sub(" ", text)
    return ({w.lower() for w in _WORD.findall(clean)},
            {h.lower() for h in _HYPHENATED.findall(clean)})


def decide_hyphen(a: str, b: str, words: set[str], hyph: set[str]) -> str:
    """"join" | "hyphen" | "ambiguous" for one `a-\\nb` split, on document evidence alone.

    Measured on Year 5's 9 real cases: 2 join, 2 hyphen, 5 ambiguous. The ambiguous ones
    (Collab-orator, science-based, zero-emission, five-e, to-Government) are genuinely
    undecidable from this document's own text -- so this returns "ambiguous" rather than
    falling back to a default. A silent default here would put a wrong word inside a
    citation, which is the one failure this project is built to prevent; the honest move
    is to surface the case for a later pass that can actually judge it.
    """
    joined, hyphenated = f"{a}{b}".lower(), f"{a.lower()}-{b.lower()}"
    ev_join, ev_hyph = joined in words, hyphenated in hyph
    if ev_join and not ev_hyph:
        return "join"
    if ev_hyph and not ev_join:
        return "hyphen"
    return "ambiguous"


def apply_hyphen_decisions(raw: str, words: set[str], hyph: set[str]
                           ) -> tuple[str, list[tuple[str, str]]]:
    """Resolve every `word-\\nword` split in one block. Returns (text, ambiguous_cases).

    MUST run on RAW cropped text, before flatten_block(). Flattening rewrites
    "col-\\nlaborations" to "col- laborations", destroying the newline that identifies a
    split -- after which the case is indistinguishable from an ordinary hyphenated
    compound followed by a space, and unrecoverable.

    An ambiguous case KEEPS its hyphen. That is the conservative direction: the hyphen is
    a character the source genuinely contains, so keeping it preserves the document,
    while joining deletes one on a guess. Either way the case is returned to the caller
    rather than silently resolved.
    """
    ambiguous: list[tuple[str, str]] = []

    def sub(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        d = decide_hyphen(a, b, words, hyph)
        if d == "join":
            return f"{a}{b}"
        if d == "ambiguous":
            ambiguous.append((a, b))
        return f"{a}-{b}"

    return _SPLIT.sub(sub, raw), ambiguous


BLOCK_SEP = "\n\n"      # between blocks on a page; page joins use convert_document.PAGE_SEP


def assemble_blocks(blocks: list[dict], n_pages: int
                    ) -> tuple[str, list[tuple[int, int, int]], list[dict]]:
    """Blocks in reading order -> (text, page_map, blocks annotated with char offsets).

    Reading order is preserved EXACTLY as given. Docling's ordering is the entire fix for
    column interleaving, so any re-sorting here -- even something that looks harmless
    like sorting by bbox top -- would reintroduce the bug this module exists to remove.

    Empty pages keep their slot. A photo-only page contributes no text block, and
    dropping it would renumber every page after it, silently misattributing every
    citation downstream. Page joining is delegated to convert_document._assemble() so
    this backend's page map is produced by the same code, and therefore carries the same
    guarantees, as pdfplumber's.
    """
    from pipeline.convert_document import _assemble

    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        by_page.setdefault(b["page_no"], []).append(b)

    pages: list[str] = []
    local: list[list[tuple[dict, int]]] = []
    for pg in range(1, n_pages + 1):
        here, parts, cursor, offs = by_page.get(pg, []), [], 0, []
        for b in here:
            if parts:
                parts.append(BLOCK_SEP)
                cursor += len(BLOCK_SEP)
            offs.append((b, cursor))
            parts.append(b["text"])
            cursor += len(b["text"])
        pages.append("".join(parts))
        local.append(offs)

    text, page_map = _assemble(pages)

    out = []
    for (pg, page_start, _), offs in zip(page_map, local):
        for b, rel in offs:
            out.append({**b, "char_start": page_start + rel,
                        "char_end": page_start + rel + len(b["text"])})
    return text, page_map, out


_CAPTION_MAX_WORDS = 25


_HEADING_SHAPE = re.compile(r"^\s*([A-Za-z]+\s+\d+)\s*:", re.I)


def is_page_footer(block: dict) -> bool:
    """A block that is nothing but this page's own number.

    Third strip site for the same defect: convert_document handles it for the pdfplumber
    backend and group_uncovered handles recovered regions, but Docling emits some footers
    as ordinary TextItem blocks and that path had none. Validated against the page's OWN
    index rather than "looks like a small number", so a standalone figure that happens to
    be numeric ("2,862") is never mistaken for a footer.
    """
    t = (block.get("text") or "").strip()
    return t.isdigit() and int(t) == block.get("page_no")


def heading_shapes(blocks: list[dict]) -> set[str]:
    """Leading "WORD N" shapes taken from blocks Docling CONFIRMED as headings.

    Document-internal evidence, the same principle the de-hyphenation pass uses, and for
    the same reason: a threshold tuned on one document is a guess about the next one,
    while the document's own confirmed examples are evidence about itself.
    """
    out = set()
    for b in blocks:
        if b.get("kind") in ("SectionHeaderItem", "TitleItem"):
            m = _HEADING_SHAPE.match(b.get("text") or b.get("docling_text") or "")
            if m:
                out.add(re.sub(r"\s+", " ", m.group(1)).strip().lower())
    return out


def _matches_heading_shape(block: dict, shapes: set[str] | None) -> bool:
    """Whether this block opens with a "WORD N:" shape the document confirms elsewhere."""
    if not shapes:
        return False
    m = _HEADING_SHAPE.match(block.get("text") or "")
    return bool(m and re.sub(r"\s+", " ", m.group(1)).strip().lower() in shapes)


def is_caption_candidate(block: dict, shapes: set[str] | None = None) -> bool:
    """Caption-shaped, not front matter, and not a heading Docling happened to mistype.

    THE COVER PAGE IS EXEMPT, and this is a structural claim rather than a tuned one: a
    cover carries a title, a subtitle and imagery, with no body prose for a caption to
    describe. It is exempt because geometry provably cannot decide it -- Year 5's cover
    subtitle sits INSIDE a collage photo on both axes (dy=0, dx=0), indistinguishable
    from a real caption -- and losing a report's own reporting period is a worse error
    than leaving one cover-page caption in.

    A MISTYPED HEADING IS EXEMPT TOO, and this one cost a real deletion before it was
    caught. Running Year 4 unchanged: Docling typed its long-form strategy headings as
    SectionHeaderItem on pages 5, 13, 15 and 19, but typed the page 11 one --
    "STRATEGY 3: Significantly Improve the Energy Efficiency in our Homes, Businesses,
    Schools..." -- as an ordinary TextItem. Twenty words, under the 25-word ceiling that
    Year 5's captions (longest: 15 words) had justified, adjacent to a photo, and so
    deleted as a caption. Lowering the ceiling would just fit two documents instead of
    one; matching the document's OWN confirmed heading shapes generalises.
    """
    if block.get("page_no") == 1 or not looks_like_caption(block):
        return False
    return not _matches_heading_shape(block, shapes)


def looks_like_caption(block: dict) -> bool:
    """Whether a block is SHAPED like a caption, before geometry is consulted.

    Needed because widening caption association from uncovered strays to Docling's own
    unlinked blocks (which is what the p.11 and p.23 bleeds require) also widens the blast
    radius: a body paragraph beside a photograph would be dropped as if it were a caption,
    and a silent deletion of real prose is far worse than a stray caption left in.

    So a caption must be a plain TextItem -- never a ListItem, which carries the bullets
    that are this corpus's actual substance, and never a heading -- and must be short.
    Measured against the real captions: the longest on Year 5 is 15 words ("Michael Hagan
    from the Green Energy Neighbors leading the Net-Zero Home Energy Tour, 2024."), while
    the shortest body paragraph beside a photo runs well past 25.
    """
    # UncoveredText counts: the coverage sweep recovers genuine captions (Year 5's p.6
    # and p.23 among them), and they must pass through the same validation as any other
    # candidate rather than around it.
    if block.get("kind") not in ("TextItem", "UncoveredText"):
        return False
    words = (block.get("text") or "").split()
    return 0 < len(words) <= _CAPTION_MAX_WORDS


_CAPTION_ADJACENT = 15.0   # pt; a caption touches its picture on BOTH axes


def associate_caption(cap: dict, pictures: list[dict]):
    """The picture a stray text region captions, or None. All coordinates top-left space.

    Two real Year 5 cases, which is why containment alone is not enough:
      p.6  "City officials break ground on Fire Station 4..." sits INSIDE the
           photograph's own bbox -- Docling absorbed the region, emitting no text block.
      p.5  "The Renewable Energy tab of the A2ZERO Dashboard." sits just BELOW the
           dashboard image, outside its bbox entirely.

    Association is what lets a caption follow its picture's fate: dropped with a dropped
    photograph, folded into the description of a kept chart. Distance is bounded so the
    page 2 table-of-contents entries -- uncovered text, but not captions -- stay
    unassociated instead of being deleted alongside some distant photo.
    """
    best, best_d = None, None
    for p in pictures:
        if p.get("page_no") != cap.get("page_no"):
            continue
        # ADJACENCY IN BOTH AXES. A caption touches its picture vertically AND
        # horizontally; text near on only one axis belongs to a different region.
        # Measured on the six real Year 5 cases, and both errors below were made before
        # arriving here -- a below-only rule wrongly restored three real captions, and a
        # not-entirely-above rule wrongly swallowed the cover's date subtitle:
        #   drop   p.11 Michael Hagan  dy=6  dx=0     p.23 Emergency kit  dy=0  dx=5
        #          p.18 Bicentennial   dy=0  dx=0     p.22 SunBundle      dy=0  dx=0
        #   KEEP   p.1  "June 1, 2024 - May 31, 2025"  dy=0  dx=134  (cover subtitle,
        #          vertically level with a collage photo but far across the page)
        #          p.3  "Missy, Simi, Steve..."        dy=40 dx=0    (the letter's
        #          sign-off, directly above the team photo but a clear gap away)
        # Margin is wide on the discriminating axis in both keeps (40 and 134 against a
        # 15pt threshold), which is why this separates rather than merely fits.
        dy = 0.0 if (cap["top"] <= p["bottom"] and cap["bottom"] >= p["top"]) else \
             min(abs(cap["top"] - p["bottom"]), abs(p["top"] - cap["bottom"]))
        dx = 0.0 if (cap["x0"] <= p["x1"] and cap["x1"] >= p["x0"]) else \
             min(abs(cap["x0"] - p["x1"]), abs(p["x0"] - cap["x1"]))
        if dy > _CAPTION_ADJACENT or dx > _CAPTION_ADJACENT:
            continue
        d = dy + dx
        if best_d is None or d < best_d:
            best, best_d = p, d
    return best


# Line spacing scales with font size, so the "same block" gap has to as well. Measured on
# Year 4 page 8: the STRATEGY 2 heading is large-font with 24pt leading, which a fixed
# 18pt tolerance split into three fragments -- and the fragments no longer began with
# "STRATEGY 2:", so the heading-shape guard could not recognise them and two thirds of a
# section heading were deleted as captions. Expressed as a multiple of the region's own
# glyph height instead.
_LINE_TOL_RATIO = 1.6
_LINE_TOL_MIN = 14.0


def uncovered_words(words: list[dict], text_boxes: list[tuple]) -> list[dict]:
    """Words whose centre falls in no TEXT block box.

    Picture boxes deliberately do NOT count as coverage. Measured on the real Year 5
    page 6: the caption "City officials break ground on Fire Station 4..." sits INSIDE
    the photograph's own bounding box, so treating a picture box as coverage silently
    deletes the caption along with the photo -- one of the exact losses the human review
    caught. A picture covers pixels, not prose.
    """
    out = []
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if not any(x0 - 3 <= cx <= x1 + 3 and y0 - 3 <= cy <= y1 + 3
                   for x0, y0, x1, y1 in text_boxes):
            out.append(w)
    return out


def group_uncovered(words: list[dict], page_no: int) -> list[dict]:
    """Leftover words -> synthetic UncoveredText blocks, so nothing is dropped in silence.

    Bare page-footer numbers are excluded rather than resurrected: 23 of Year 5's 24
    pages end in one, Docling correctly omits them, and the pdfplumber backend already
    strips them (see convert_document._strip_footer_number). Promoting them here would
    reintroduce the footer bleed that corrupted the first review workbook.
    """
    if not words:
        return []
    rows = sorted(words, key=lambda w: (w["top"], w["x0"]))
    heights = sorted(w["bottom"] - w["top"] for w in rows)
    tol = max(_LINE_TOL_MIN, heights[len(heights) // 2] * _LINE_TOL_RATIO)
    groups, cur = [], [rows[0]]
    for w in rows[1:]:
        if w["top"] - cur[-1]["top"] <= tol:
            cur.append(w)
        else:
            groups.append(cur)
            cur = [w]
    groups.append(cur)

    out = []
    for g in groups:
        g.sort(key=lambda w: (round(w["top"] / 6), w["x0"]))
        text = " ".join(w["text"] for w in g).strip()
        # Strip a trailing footer number, but ONLY when it equals this page's own number
        # -- the same validation convert_document._strip_footer_number uses, so real
        # content that merely ends in a number ("...installed in 2024") is never touched.
        m = re.search(r"\s+(\d{1,3})$", text)
        if m and int(m.group(1)) == page_no:
            text = text[:m.start()].strip()
        if not text or text.isdigit():
            continue
        out.append({"kind": "UncoveredText", "page_no": page_no,
                    "bbox": [min(w["x0"] for w in g), min(w["top"] for w in g),
                             max(w["x1"] for w in g), max(w["bottom"] for w in g)],
                    "coord_origin": "CoordOrigin.TOPLEFT",
                    "page_w": None, "page_h": None, "self_ref": None,
                    "caption_for": None, "docling_text": "", "text": text})
    return out


def convert(pdf_path, blocks_path):
    """(Conversion, blocks_with_offsets, ambiguous_hyphens) for one PDF + its blocks.json.

    TWO PASSES OVER THE CROPS, DELIBERATELY. Hyphen evidence has to be document-wide --
    "collaborations" attesting for "col-\\nlaborations" may sit twenty pages away -- so
    every block is cropped and cached first, evidence is built from all of them, and only
    then are the splits resolved. A single streaming pass could only ever consult text it
    had already seen, which would decide the same case differently depending on where it
    appeared. Cropping is local and cheap; deciding twice is not.
    """
    import hashlib, json, time
    import pdfplumber
    from pathlib import Path
    from pipeline.convert_document import Conversion

    spec = json.loads(Path(blocks_path).read_text())
    n_pages, blocks = spec["n_pages"], spec["blocks"]

    raws: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for b in blocks:
            if b["kind"] == "PictureItem":
                raws.append("")
                continue
            page = pdf.pages[b["page_no"] - 1]
            box = bbox_to_crop(tuple(b["bbox"]), b["coord_origin"],
                               b["page_h"], b["page_w"])
            raws.append(page.crop(box).extract_text() or "")

    words, hyph = build_evidence("\n".join(raws))

    ambiguous: list[tuple[str, str]] = []
    captioned: list[tuple[int, str]] = []
    resolved: list[dict] = []
    for i, (b, raw) in enumerate(zip(blocks, raws)):
        if b["kind"] == "PictureItem":
            continue                      # no text; re-interleaved for the renderer below
        fixed, amb = apply_hyphen_decisions(raw, words, hyph)
        ambiguous.extend(amb)
        text = flatten_block(fixed)
        if text and is_page_footer({"text": text, "page_no": b["page_no"]}):
            continue                     # a page footer is not prose
        if text:
            resolved.append({**b, "text": text, "_ord": float(i),
                             "_caption_src": "docling" if b.get("caption_for") else None})

    # COVERAGE SWEEP. Anything pdfplumber can see on a page that no TEXT block claims is
    # appended to that page, typed UncoveredText. Without this, Docling's block list is
    # taken on trust -- and on the real Year 5 it silently dropped two captions the human
    # review had already flagged. Strays go at the END of their page rather than being
    # sorted into position, because re-sorting is exactly what would undo the reading
    # order this module exists to preserve.
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno in range(1, n_pages + 1):
            page = pdf.pages[pno - 1]
            H = page.height
            boxes = []
            for b in blocks:
                if b["page_no"] != pno or b["kind"] == "PictureItem":
                    continue
                l, t, r, bt = b["bbox"]
                boxes.append((min(l, r), min(H - t, H - bt),
                              max(l, r), max(H - t, H - bt)))
            strays = group_uncovered(uncovered_words(page.extract_words(), boxes), pno)
            # A recovered region may itself be a caption (both real Year 5 cases were).
            # Associating it here, where the geometry lives, lets it follow its picture's
            # fate through the ordinary caption_for path instead of needing a second rule
            # in the renderer.
            pics_tl = [{"page_no": p2["page_no"], "self_ref": p2["self_ref"],
                        "worth_extraction": p2.get("worth_extraction", False),
                        "x0": min(p2["bbox"][0], p2["bbox"][2]),
                        "x1": max(p2["bbox"][0], p2["bbox"][2]),
                        "top": min(H - p2["bbox"][1], H - p2["bbox"][3]),
                        "bottom": max(H - p2["bbox"][1], H - p2["bbox"][3])}
                       for p2 in blocks
                       if p2["kind"] == "PictureItem" and p2["page_no"] == pno]
            for s in strays:
                s["_swept"] = True
                s["top"], s["bottom"] = s["bbox"][1], s["bbox"][3]
                s["x0"], s["x1"] = s["bbox"][0], s["bbox"][2]
                owner = associate_caption(s, pics_tl)
                if owner is not None:
                    s["caption_for"] = owner["self_ref"]
            if strays:
                at = max((i for i, b in enumerate(resolved) if b["page_no"] <= pno),
                         default=-1) + 1
                prev = resolved[at - 1]["_ord"] if at > 0 else -1.0
                for k, s in enumerate(strays):
                    s["_ord"] = prev + 0.001 * (k + 1)
                resolved[at:at] = strays

    # WIDEN CAPTION ASSOCIATION TO DOCLING'S OWN UNLINKED BLOCKS. Docling links only some
    # captions (13 on Year 5); others it emits as ordinary TextItems with no link, which
    # then render as prose stranded where a photo used to be -- both surviving bleeds in
    # the second review were this. Guarded by looks_like_caption() so a body paragraph
    # beside a photograph can never be swallowed, and every association is recorded so a
    # wrong one is auditable rather than an invisible deletion.
    shapes = heading_shapes(blocks)
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno in range(1, n_pages + 1):
            H = pdf.pages[pno - 1].height
            pics_tl = [{"page_no": p2["page_no"], "self_ref": p2["self_ref"],
                        "worth_extraction": p2.get("worth_extraction", False),
                        "x0": min(p2["bbox"][0], p2["bbox"][2]),
                        "x1": max(p2["bbox"][0], p2["bbox"][2]),
                        "top": min(H - p2["bbox"][1], H - p2["bbox"][3]),
                        "bottom": max(H - p2["bbox"][1], H - p2["bbox"][3])}
                       for p2 in blocks
                       if p2["kind"] == "PictureItem" and p2["page_no"] == pno]
            for b in resolved:
                if b["page_no"] != pno or b.get("caption_for") or b.get("_swept"):
                    continue
                if not is_caption_candidate(b, shapes):
                    continue
                bb = b["bbox"]
                probe = {"page_no": pno,
                         "x0": min(bb[0], bb[2]), "x1": max(bb[0], bb[2]),
                         "top": min(H - bb[1], H - bb[3]),
                         "bottom": max(H - bb[1], H - bb[3])}
                owner = associate_caption(probe, pics_tl)
                if owner is not None:
                    b["caption_for"] = owner["self_ref"]

    # ONE GATE FOR EVERY CAPTION PROPOSAL, WHOEVER MADE IT. Three sources can set
    # caption_for -- Docling's own structural linkage, the coverage sweep's geometry, and
    # the widened association above -- and previously only the last was validated or
    # logged. Year 4's line-by-line audit found the cost: two SECTION HEADINGS deleted as
    # captions while the report cheerfully said "0 unlinked captions dropped". Docling's
    # linkage is a proposal like any other, not ground truth, and a guard that only
    # covers the path you were thinking about is not a guard.
    for b in resolved:
        if not b.get("caption_for"):
            continue
        # DOCLING'S OWN LINKAGE IS EVIDENCE; A GEOMETRIC GUESS IS NOT. Docling resolved
        # its 13 Year 5 captions structurally and correctly, so subjecting those to the
        # same word-count/kind heuristic used for geometric guesses just breaks working
        # links -- it wrongly restored a long, genuine p.9 caption on the first attempt.
        # Only POSITIVE counter-evidence overrides Docling: the block matching a heading
        # shape the document itself confirms elsewhere. Geometric proposals, having no
        # such backing, must clear the full candidate test.
        ok = (not _matches_heading_shape(b, shapes) if b.get("_caption_src") == "docling"
              else is_caption_candidate(b, shapes))
        if ok:
            captioned.append((b["page_no"], b["text"][:70]))
        else:
            b["caption_for"] = None          # not a caption; keep it in the document

    text, page_map, with_offsets = assemble_blocks(resolved, n_pages)
    try:
        import importlib.metadata as _m
        ver = _m.version("pdfplumber")
    except Exception:
        ver = "unknown"

    # PICTURES REJOIN THE STREAM HERE. They carry no text, so they are absent from the
    # citation spine above -- but the renderer needs them in reading order to place a
    # figure where the document actually puts it, and to decide each caption's fate via
    # caption_for. Omitting them cost exactly that on the first run: zero figures
    # rendered and every caption stranded as recovered text. This is a MERGE of two
    # already-ordered lists on their original indices, not a re-sort of reading order.
    pic_blocks = [{**b, "_ord": float(i)} for i, b in enumerate(blocks)
                  if b["kind"] == "PictureItem"]
    render_stream = sorted(with_offsets + pic_blocks, key=lambda b: b["_ord"])

    conv = Conversion(
        source_path=str(pdf_path), converter="docling_blocks+pdfplumber",
        converter_version=ver, text=text, page_map=page_map,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        n_pages=n_pages,
        converted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return conv, render_stream, {"ambiguous_hyphens": ambiguous,
                                 "captions_associated": captioned}
