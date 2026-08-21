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
from collections import Counter

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


_INDEX_ENTRY = re.compile(r"\S.*\s\d{1,3}$")


_OCR_MIN_WORDS = 3       # below this there is not enough signal to call pdfplumber broken
_OCR_RATIO = 0.5         # pdfplumber below this share of Docling's words = no text layer


def normalize_ocr_terms(text: str, terms: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """Repair known OCR manglings of corpus terms. Returns (text, corrections made).

    Only exact variants listed in the registry, matched as whole tokens, so "LAZEROS" is
    never mistaken for a mangled A2ZERO. Every substitution is returned rather than
    applied silently -- the same rule as the ambiguous hyphens, and for the same reason:
    a change to a citable span has to be inspectable.
    """
    fixes: list[tuple[str, str]] = []
    for term in terms:
        canon = term["canonical"]
        for var in term["variants"]:
            if var.upper() == canon.upper():
                continue
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(var) + r"(?![A-Za-z0-9])",
                             re.I)
            text, n = pat.subn(canon, text)
            fixes.extend([(var, canon)] * n)
    return text, fixes


def trim_to_docling(pdf_text: str, docling_text: str) -> str:
    """Cut an over-captured crop back to the block Docling actually described.

    Docling's bbox can be imprecise while its TEXT is right -- on Year 1 the boxes sit
    ~8pt below their own lines, so pdfplumber's crop (which keeps any word partially
    intersecting the box) returned three lines where the block had two, and every bullet
    carried the opening of the next. The document came out 43% longer than the human
    reference purely through duplication.

    The boundary therefore comes from Docling and the characters from pdfplumber, which
    is this module's founding split applied one level down. pdfplumber's exact characters
    are preserved -- the trim only chooses WHERE to stop, never what the text says, so
    "A2ZERO" is not replaced by Docling's fragmented "A 2 ZERO".

    Trims only when the crop is LONGER than Docling's reading. Under-capture is a
    different failure and papering over it here would hide it.

    IT SLICES, IT DOES NOT REJOIN. Both trims used to rebuild the block with
    " ".join(tokens), which silently flattened every newline inside it -- and the very
    next line of convert() is apply_hyphen_decisions, whose whole job is to read the
    `word-\nword` splits this destroyed. Its docstring calls that case "unrecoverable"
    once the newline is gone, and it was right: 18 line-break hyphens across Years 3-5
    came out as "zero- emissions", "plant- forward", "income- qualified" -- a hyphen and
    a space where the page has neither. Choosing WHERE to stop must not rewrite what lies
    in between, so the boundaries are character offsets into the original string.
    """
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", pdf_text)]
    pw, dw = [t[0] for t in toks], docling_text.split()
    # Both sides must have tokens. Year 2 is image-based, so most crops are EMPTY and the
    # OCR fallback supplies the text further down -- reordering the trims removed a length
    # guard that had been protecting pw[0] by accident, and the document crashed.
    if not dw or not pw:
        return pdf_text
    norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())

    # FRONT TRIM, the tail trim's mirror. Merging Year 3's label and title heading boxes
    # widened the crop enough to catch the decorative strategy numeral set beside the
    # heading, so it rendered as "3 STRATEGY THREE: SIGNIFICANTLY IMPROVE...". Docling's
    # text begins at "STRATEGY", which is the boundary. Bounded to a couple of tokens: a
    # wholesale mismatch means something else is wrong, and silently deleting the opening
    # of a block is exactly the failure this module keeps finding.
    # Only a leading token Docling lacks ENTIRELY. Merging split heading boxes
    # concatenates their text in block order, which is not reading order -- Year 1's
    # merged heading reads "Switch our appliances and vehicles Strategy 2: from fossil
    # fuels to electric" -- so aligning to Docling's FIRST token stripped "Strategy 2:"
    # off the front of a correct heading. A token Docling has somewhere is a token
    # Docling read; only the ordering differs, and ordering is not over-capture.
    dl_tokens = {norm(w) for w in dw}
    if norm(pw[0]) != norm(dw[0]) and norm(pw[0]) not in dl_tokens:
        for i in range(1, min(3, len(pw))):
            if norm(pw[i]) == norm(dw[0]):
                pdf_text = pdf_text[toks[i][1]:]
                toks, pw = toks[i:], pw[i:]
                break

    # The TAIL trim only applies when the crop is genuinely longer. Under-capture is a
    # different failure and papering over it here would hide it.
    if len(pw) <= len(dw):
        return pdf_text
    target = norm(dw[-1])
    if not target:
        return pdf_text
    # Search near where Docling's reading ends, not from the start: a token repeated
    # earlier in the block would otherwise cut it short.
    lo = max(len(dw) - 3, 0)
    for i in range(lo, min(len(pw), len(dw) + 6)):
        if norm(pw[i]) == target:
            return pdf_text[:toks[i][2]]
    return pdf_text


def choose_block_text(pdf_text: str, docling_text: str) -> tuple[str, str]:
    """(text, source) for one block: pdfplumber's characters, or Docling's OCR.

    pdfplumber is PREFERRED wherever it actually has a text layer, because it is
    character-exact and Docling fragments styled glyph runs (A2ZERO -> "A 2 ZERO" 40
    times of 53 on Year 5). That fragmentation also means Docling frequently reports MORE
    words than pdfplumber on a perfectly healthy page, so "Docling has more" cannot be
    the trigger -- only pdfplumber having almost NOTHING can.

    Year 2 is why this exists. It is an image-based PDF: the prose is rendered as
    pictures, so pdfplumber's text layer yields 9 words across the first 22 blocks where
    Docling's OCR yields 445. Cropping pdfplumber to Docling's blocks discarded a
    document Docling had read successfully -- reproducing the corpus's signature
    234-word, zero-dollar-figure failure, and reporting success while doing it.

    THE SOURCE IS RETURNED, NOT JUST THE TEXT. OCR output is a model's reading of pixels,
    the same category as the vision figure extraction and NOT character-exact verbatim.
    Mixing the two silently would let an OCR guess be cited exactly like a quotation.
    """
    p_words, d_words = len(pdf_text.split()), len(docling_text.split())
    if d_words >= _OCR_MIN_WORDS and p_words < _OCR_RATIO * d_words:
        return docling_text, "docling_ocr"
    return pdf_text, "pdfplumber"


def split_index_lines(raw: str) -> list[str] | None:
    """The lines of `raw` if it is an index (contents list), else None.

    Runs on the RAW crop, before flatten_block collapses the line breaks that make an
    index recognisable at all. Year 4's table of contents arrives as ONE Docling block
    (typed CodeItem) and was flattened into a single run-on line; Year 5 has no block for
    its equivalent and reaches the same place through the coverage sweep. Same rule, two
    entry points.

    Requires at least three entries, and most of them to end in a page number, so a
    wrapped sentence that happens to end in a figure ("saving residents $101,650 on costs
    in 2024") is never mistaken for an index.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None
    hits = sum(1 for ln in lines if _INDEX_ENTRY.match(ln))
    return lines if hits >= max(3, int(0.7 * len(lines))) else None


# A column gutter is hundreds of points wide; a paragraph indent is one or two ems, so
# 18-40pt at this type size. Nothing in the corpus sits between, which is why a single
# threshold separates them cleanly rather than merely fitting.
_MIN_GUTTER = 100.0
# Children of one parent vary by ~20pt on the real page 13 (88.6 to 108.1) from marker
# width and OCR jitter. A genuine nesting step must clear that.
_SCRIPT_SIZE = 0.92        # smaller than this share of the line's type = super/subscript
_INDENT_EMS = 1.25         # an indent level is at least this many ems; less is jitter
_DEFAULT_EM = 10.0         # body size assumed on a page with no text layer at all
_OCR_INDENT_STEP = 24.0    # points; the conservative step an OCR-predicted box keeps


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

_PERIOD = re.compile(
    r"([A-Z][a-z]+|[A-Z]{3,})\.?\s+(\d{1,2}),?\s*(\d{4})"      # July 1, 2023
    r"\s*[-\u2013\u2014]\s*"
    r"([A-Z][a-z]+|[A-Z]{3,})\.?\s+(\d{1,2}),?\s*(\d{4})", re.I)


def coverage_period(text: str) -> dict | None:
    """The period this report says it covers, with its length in days.

    WHY THIS IS EXTRACTED RATHER THAN LEFT IN THE PROSE. It is stated once, in a heading
    or a subtitle, and after sectioning that heading is just a string like any other --
    the wiki lost a report's period exactly this way and documents.covers_period_start /
    _end exist because of it. Pulling it out here means the value survives independently
    of how any later stage decides to treat headings.

    NO FISCAL-YEAR ASSUMPTION. Ann Arbor's fiscal year runs July 1 - June 30 and Years 3
    and 4 do use it, but Year 5 covers June 1 - May 31 deliberately, so checking against
    the Charter would flag a correct document. What every one of them shares is simpler:
    an ANNUAL report covers a year. `days` is returned so a caller can say so -- Years 3
    and 4 state periods of 337 and 338 days, because both print "June 3" where the page
    means June 30, and that is a typo in the source, not an extraction fault (verified
    against the content stream: the '3' is followed directly by a comma, with no dropped
    glyph anywhere to its right).

    The verbatim string travels with the parse, because it is what the document says and
    the interpretation is a separate, human-curatable decision.
    """
    m = _PERIOD.search(text)
    if not m:
        return None
    a_mon, a_day, a_yr, b_mon, b_day, b_yr = m.groups()
    am, bm = _MONTHS.get(a_mon.lower()), _MONTHS.get(b_mon.lower())
    if not am or not bm:
        return None
    from datetime import date
    try:
        start, end = (date(int(a_yr), am, int(a_day)), date(int(b_yr), bm, int(b_day)))
    except ValueError:
        return None
    if end <= start:
        return None
    return {"text": " ".join(m.group(0).split()), "start": start.isoformat(),
            "end": end.isoformat(), "days": (end - start).days}


_BULLETS = "\u00b7\u2022\u25cf\u25aa\u25e6o"


def split_on_docling_bullets(pdf_text: str, docling_text: str) -> list[str] | None:
    """Split one over-merged block into the list items Docling says it holds.

    Year 3 page 14 lists thirteen grants. Docling models them as blocks holding one or two
    each and puts a real bullet in its own text -- "\u00b7 AmeriCorps program ($229,000)..." --
    but the PDF draws that bullet as a latin 'o' with a 2.34pt kern after it, under
    pdfplumber's 3pt word threshold, so the character-exact read returns "oAmeriCorps" and
    thirteen grants collapse into a handful of run-on paragraphs.

    Lowering the word threshold is the wrong lever: 2.34pt is this block's inter-word gap
    and other blocks kern differently, so it would split real words elsewhere to fix this.
    The structure is not in doubt anyway -- Docling already reported it, which is this
    module's whole division of labour. So the split is CORROBORATED rather than guessed:
    Docling's bullet count must equal the number of marker-plus-capital boundaries
    pdfplumber's text actually contains. Disagree by one and nothing is split, because a
    split in the wrong place silently rewrites a sentence.

    Returns the items, or None if this is not such a block.
    """
    # THE MARKER MUST BE A TOKEN OF ITS OWN in Docling's text. 'o' is in the marker set
    # because this document draws its bullet as one, and matching it anywhere also matched
    # the last letter of "to" in "to OSI" -- inflating the count to 3 where the block holds
    # 2, so the corroboration check rejected every multi-grant block it was built for.
    n_docling = len(re.findall(rf"(?:^|\s)[{_BULLETS}]\s+[A-Z(]", docling_text))
    if n_docling < 1:
        return None
    parts = re.split(rf"(?:^|(?<=[.\s]))[{_BULLETS}](?=[A-Z(])", pdf_text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) != n_docling:
        return None
    return parts


_OPEN_END = re.compile(r'[.!?:;]["\u201d)]?\s*$')


def rejoin_open_sentences(resolved: list[dict], words: set[str] | None = None,
                          hyph: set[str] | None = None) -> int:
    """Merge a block that ends mid-sentence with the block that finishes it.

    A sentence that runs into the next column -- especially where the continuation sits
    under an image -- is modelled by Docling as a SEPARATE block, and it lands wherever
    reading order puts it. Year 3 shipped ten of them:

        "The Greenbelt reached 7,600 acres of farmland and"
        "natural areas permanently protected surrounding the City of Ann Arbor..."

    Split like that, neither half is a claim. The first asserts nothing and the second has
    no subject, so extraction against either produces a fragment with a verbatim that is
    real and a meaning that is not. Keeping the sentence whole matters more downstream
    than any other structural property, which is Caleb's standing rule and the reason this
    runs before nesting and captions.

    TWO CONDITIONS, BOTH REQUIRED. The first block must end OPEN -- no terminal
    punctuation -- and the second must begin lowercase. A well-formed list item begins
    with a capital, so a lowercase opening is not a new item under any reading; and a
    block ending in a full stop is finished no matter what follows it. Measured across the
    corpus the pair fires ten times on Year 3 and NOT ONCE on Years 1, 2, 4 or 5, so it
    cannot disturb documents that were already correct.

    A TRAILING HYPHEN IS DECIDED, NOT ASSUMED. "collect feedback on soon-" plus "to-be
    created" is one hyphenated word broken across a column and keeps its hyphen; "col-"
    plus "laborations" is a line-break split and loses it. That is the same question
    apply_hyphen_decisions answers inside a block, so it is answered the same way here,
    against the same document-built vocabulary, rather than by picking one and being
    wrong half the time.
    """
    def eligible(x: dict) -> bool:
        return (x["kind"] not in ("SectionHeaderItem", "TitleItem", "PictureItem")
                and not x.get("is_furniture") and not x.get("caption_for"))

    def weld(a: dict, b: dict) -> None:
        at, bt = a["text"].strip(), b["text"].strip()
        if at.endswith("-"):
            lead, tail = at.split()[-1][:-1], bt.split()[0]
            keep = decide_hyphen(lead, tail, words or set(), hyph or set()) != "join"
            a["text"] = at + bt if keep else at[:-1] + bt
        else:
            a["text"] = f"{at} {bt}"
        a["_rejoined"] = True

    joined = 0
    i = 0
    while i < len(resolved) - 1:
        a, b = resolved[i], resolved[i + 1]
        at, bt = (a.get("text") or "").strip(), (b.get("text") or "").strip()
        if (at and bt
                and a["kind"] not in ("SectionHeaderItem", "TitleItem", "PictureItem")
                and b["kind"] not in ("SectionHeaderItem", "TitleItem", "PictureItem")
                and not a.get("is_furniture") and not b.get("is_furniture")
                and not a.get("caption_for") and not b.get("caption_for")
                and not _OPEN_END.search(at)
                and re.match(r"^[a-z]", bt)):
            weld(a, b)
            del resolved[i + 1]
            joined += 1
            continue
        i += 1

    # SECOND PASS, ACROSS INTERVENING BLOCKS ON THE SAME PAGE. Two of Year 3's breaks are
    # not adjacent: on page 2 the continuation is separated from its sentence by the pie
    # chart's caption blocks, and on page 7 "households make health, safety, and quality
    # of life improvements." lands after the WHOLE of page 8, because it sits under an
    # image and reading order put it there. Caleb flagged the page-7 one twice.
    #
    # The search is deliberately narrow, because a mis-pairing here writes a sentence the
    # document never contained. It stays on ONE page; it accepts only a TextItem, never a
    # ListItem, since an orphaned continuation is what Docling types a bodiless fragment
    # as; and it stops at the first other block that is itself open-ended, because two
    # unfinished sentences and one continuation is an ambiguity, not a repair.
    for i, a in enumerate(resolved):
        at = (a.get("text") or "").strip()
        if not at or not eligible(a) or _OPEN_END.search(at):
            continue
        for j in range(i + 1, len(resolved)):
            b = resolved[j]
            # A block from ANOTHER page is skipped, not stopped at. Year 3's page-7
            # fragment sits under an image and reading order emits it after the whole of
            # page 8, so the continuation and its sentence are separated by a page of
            # unrelated text. Only a block of the PARENT's own page can be the
            # continuation, and the search gives up once it is a page and a half past.
            if b.get("page_no") != a.get("page_no"):
                if (b.get("page_no") or 0) > (a.get("page_no") or 0) + 1:
                    break
                continue
            bt = (b.get("text") or "").strip()
            if not bt or b.get("caption_for") or b["kind"] == "PictureItem":
                continue
            if b["kind"] in ("SectionHeaderItem", "TitleItem"):
                break
            if not _OPEN_END.search(bt) and not re.match(r"^[a-z]", bt):
                break                    # another open sentence -> ambiguous, give up
            # UncoveredText counts too. A continuation the coverage sweep recovered is
            # the same bodiless fragment as one Docling modelled -- Year 3 page 2's
            # "offset of 6% of community-wide emissions." arrives that way, which is why
            # Caleb found it sitting alone with a recovery flag on it.
            if (b["kind"] in ("TextItem", "UncoveredText")
                    and re.match(r"^[a-z]", bt) and eligible(b)):
                weld(a, b)
                del resolved[j]
                joined += 1
                break
            # A FINISHED block is scanned PAST, not stopped at. Breaking here examined
            # only the parent's immediate neighbour, which is precisely the case the
            # adjacent pass already handled -- so this pass did nothing at all.
    return joined


def _lead_shape(text: str) -> str:
    """A block's LEADING shape -- the first three word-ish tokens, digits collapsed.

    Three, matching body_shapes() and render_blocks(), not _shape()'s eight: a callout
    is recognised by how it opens ("dive deeper into"), and the eight-token form makes
    every one of Year 2's five callouts a different shape, so none of them recur and the
    majority vote they depend on never happens.
    """
    return " ".join(re.findall(r"[a-z#]{2,}", re.sub(r"\d+", "#", (text or "").lower()))[:3])


def absorb_body_shaped_headings(blocks: list[dict], ems: dict[int, float]) -> list[dict]:
    """Fold a mistyped heading into the paragraph whose first line it actually is.

    Year 2 sets five DIVE DEEPER callouts. Four arrive as one TextItem each, reading
    "DIVE DEEPER into X: prose...". The fifth wraps onto a second line and Docling splits
    it, typing the first line SectionHeaderItem and leaving the rest -- including the
    colon that ends the label -- in the block below, so one callout rendered as a stray
    heading followed by a paragraph starting "BUILDINGS: This year,".

    NEITHER SIGNAL IS SUFFICIENT ALONE, WHICH IS WHY BOTH ARE REQUIRED.

    Geometry says these two are consecutive lines of one paragraph: 4.6pt separates them,
    where every genuine heading-to-body gap on Year 2 is 23-50pt. But measured against the
    page's type size that test alone also swallows Year 1's real "Next Steps" heading
    (5.4pt), Year 5's cover title, and two more -- four verified headings destroyed to fix
    one.

    So the block must ALSO carry a shape the document itself uses for body text.
    "dive deeper into" is body shape here because four of the five callouts are plain
    TextItems saying exactly that; "next steps" and "zer# annual report" are not. Across
    all five reports these two conditions overlap on exactly one block -- the broken one.
    """
    shaped = [{**b, "text": b.get("docling_text") or ""} for b in blocks]
    shapes = body_shapes(shaped)
    out: list[dict] = []
    skip = -1
    for i, b in enumerate(blocks):
        if i == skip:
            continue
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if (b["kind"] == "SectionHeaderItem" and nxt is not None
                and nxt["kind"] not in ("PictureItem", "SectionHeaderItem")
                and nxt["page_no"] == b["page_no"]
                and _lead_shape(b.get("docling_text") or "") in shapes
                and abs(b["bbox"][0] - nxt["bbox"][0]) <= 3.0
                and 0 <= b["bbox"][3] - nxt["bbox"][1] < (ems.get(b["page_no"]) or _DEFAULT_EM)):
            bl, nl = b["bbox"], nxt["bbox"]
            out.append({**nxt,
                        "bbox": [min(bl[0], nl[0]), max(bl[1], nl[1]),
                                 max(bl[2], nl[2]), min(bl[3], nl[3])],
                        "docling_text": " ".join(
                            x for x in ((b.get("docling_text") or "").strip(),
                                        (nxt.get("docling_text") or "").strip()) if x)})
            skip = i + 1
            continue
        out.append(b)
    return out


def snap_scripts(chars: list[dict]) -> tuple[list[dict], int]:
    """Put super/subscript glyphs back on the line they belong to.

    NOT A TYPOGRAPHY PREFERENCE -- THESE ARE WRONG CHARACTERS. A2ZERO is printed with a
    superscript 2, and the PDF draws that glyph in a SEPARATE text pass after the body
    run: on Year 1 the three page-6 superscripts are the last three chars in the whole
    content stream. pdfplumber groups chars into lines by clustering `top` within
    y_tolerance=3, and Year 1 raises its superscripts 3.4-4.1pt -- just outside that band,
    where Years 3-5 raise theirs 0.2-1.4pt and are unaffected. Three distinct corruptions
    followed, all on Year 1 page 6:

        A2ZERO Week ... the adoption of the A2ZERO Plan       what the page says
        A ZERO Week ... of 2 the adoption of the A ZERO Plan  the 2 became its own "line"
        Th2e launch of our YouTube channel                    ... and hit the line above

    Neither pdfplumber knob helps. y_tolerance=5 interleaves adjacent lines into mush
    ('TAh2 eZ ElaRuOnc Wh oefe ok,'), and use_text_flow=True keeps the lines but dumps
    every superscript at the end of the block. So the glyph's coordinates are corrected
    and pdfplumber then does its ordinary job. A script glyph is assigned to the body line
    it overlaps MOST vertically -- not the nearest baseline -- because a raised glyph
    still sits inside its own line's band and reaches into no other.

    THE SPACE IS PART OF THE ARTEFACT. Year 1 alone sets the baseline run as "A ZERO",
    with a real space char the superscript is drawn over; Years 3-5 set "AZERO" with no
    space at all. A space a glyph sits on top of is the room it was given, not a word
    boundary -- keeping it yields "A2 ZERO", one token read as two.

    MAJORITY OVERLAP, NOT ENCLOSURE. Requiring the glyph to bracket the space entirely
    left two of Year 1's nine occurrences split, for two different reasons: one space is
    covered 66.7% because the glyph is set a point to its right, and two occurrences are
    given DOUBLED spaces of which only the fully covered one qualified. Overlapping more
    than half a space is the honest test of sitting on it. Simulated across all five
    reports it deletes 13 spaces, every one joining "A" to "ZERO", and nothing at all in
    Years 2-5 -- so no footnote marker or ordinal suffix is at risk of being welded to the
    word after it, which is the failure this rule has to avoid.

    Returns the corrected chars and how many glyphs moved.
    """
    ink = [c for c in chars if not c["text"].isspace()]
    if not ink:
        return chars, 0
    em = Counter(round(c["size"], 1) for c in ink).most_common(1)[0][0]
    body = [c for c in chars if c["size"] >= em * _SCRIPT_SIZE]
    if not body:
        return chars, 0

    # A SPACE IS NEVER A LINE OF ITS OWN. Year 3 page 14 draws its word spaces in a
    # DIFFERENT FONT (MinionPro where the text is SofiaPro) on a baseline 5.5pt below the
    # text, so pdfplumber clusters every space on a line into a phantom line of their own
    # and the remaining glyphs come back touching at a 0.00 gap:
    #
    #   oAmeriCorps program($229,000)   NaturalAreas   Preservation,submitted
    #   community-basedorganizations    SustainingAnn  Careprogram,a
    #
    # It is the superscript problem again with a different glyph: something drawn in a
    # separate pass at its own baseline, clustered away from the line it belongs to. A
    # space carries no ink, so moving it cannot alter what the page says -- and a run of
    # nothing but spaces is not a line of text under any reading.
    baselines = {round(c["top"], 1) for c in chars
                 if not c["text"].isspace() and c["size"] >= em * _SCRIPT_SIZE}
    out: list[dict] = []
    rooms: list[tuple[float, float]] = []
    for c in chars:
        if c["text"].isspace() and round(c["top"], 1) not in baselines:
            # INKED body chars only. Searching `body` let each space find ITSELF as its
            # best overlap -- it snapped onto its own baseline and nothing moved.
            host, ov = None, 0.0
            for d in body:
                if d["text"].isspace():
                    continue
                o = min(c["bottom"], d["bottom"]) - max(c["top"], d["top"])
                if o > ov:
                    host, ov = d, o
            # ONLY WHERE THERE IS ROOM. A space whose x-range lands on top of an inked
            # char of the host line did not come from that line, and inserting it splits a
            # word: "SEMCOG" became "SE MCOG" when a space from a neighbouring line was
            # snapped into the middle of it. A real word space sits in a gap.
            occupied = host is not None and any(
                not d["text"].isspace() and abs(d["top"] - host["top"]) < 0.5
                and d["x0"] < c["x1"] - 0.1 and d["x1"] > c["x0"] + 0.1
                for d in body)
            if host is not None and not occupied:
                out.append({**c, "top": host["top"], "bottom": host["bottom"],
                            "doctop": host["doctop"], "y0": host["y0"], "y1": host["y1"]})
                continue
        if c["text"].isspace() or c["size"] >= em * _SCRIPT_SIZE:
            out.append(c)
            continue
        best, ov = None, 0.0
        for d in body:
            o = min(c["bottom"], d["bottom"]) - max(c["top"], d["top"])
            if o > ov:
                best, ov = d, o
        if best is None:
            out.append(c)
            continue
        out.append({**c, "top": best["top"], "bottom": best["bottom"],
                    "doctop": best["doctop"], "y0": best["y0"], "y1": best["y1"]})
        # ON THAT LINE. The room a glyph was given is a space it brackets on its OWN line;
        # an x-range alone also matches spaces directly above and below it, which deleted
        # the gap in Year 3's "turns three" and Year 5's "slated for" -- words joined by a
        # superscript sitting one line away and nowhere near them.
        rooms.append((c["x0"], c["x1"], best["top"]))

    kept = [c for c in out
            if not (c["text"].isspace()
                    and any(abs(c["top"] - t) < 0.5
                            and min(b, c["x1"]) - max(a, c["x0"])
                                > (c["x1"] - c["x0"]) / 2
                            for a, b, t in rooms))]
    return kept, len(rooms)


def column_edges(x0s: list[float], min_gap: float = _MIN_GUTTER) -> list[float]:
    """Left edge of each text column on a page, from the gaps between block left edges."""
    if not x0s:
        return []
    xs = sorted(set(round(x, 1) for x in x0s))
    edges, start = [xs[0]], xs[0]
    for a, b in zip(xs, xs[1:]):
        if b - a >= min_gap:
            edges.append(b)
    return edges


def assign_nesting(blocks: list[dict], ems: dict[int, float] | None = None) -> None:
    """Set `list_level` on each block, in place.

    Indentation is measured from the block's OWN COLUMN's left edge, because on Year 2
    page 13 the right column's grant bullets start at x0=378 while their left-column
    siblings start at 108 -- a page-wide comparison makes the right column's items
    grandchildren of a sibling. Docling is no help: it reports level=2 and marker '·' for
    the parent bullet and all twelve children alike, so geometry is the only signal left.

    A LIST CONTINUING INTO A NEW COLUMN CARRIES ITS DEPTH OVER. The first block of the
    right column has indent 0 relative to that column, because the column's leftmost
    block IS a child -- no parent bullet was ever set there. Restarting at level 0 would
    promote twelve grants to siblings of the sentence that introduces them. So a column
    change rebases rather than resets: the new column continues at the depth the previous
    one reached, and only a further indent shift changes it.

    A non-list block ends the list entirely: a heading or paragraph closes whatever
    nesting was open, so the next bullet starts fresh.

    THE INDENT STEP IS MEASURED IN EMS, NOT POINTS. A fixed 24pt missed Year 1 page 6,
    whose sub-list is indented 18.4pt while its siblings jitter by 1.2 -- eleven children
    of "through the following avenues:" rendered as siblings of it. But the threshold
    cannot simply be lowered, because jitter scales with the page: Year 3 page 15's
    siblings scatter over 5.8pt and Year 2 page 13's over 10.3pt, since Year 2 is OCR'd
    and its boxes are model-predicted rather than text-layer exact. Points cannot separate
    an 18.4pt indent from a 10.3pt wobble; ems can, because both quantities scale with the
    type. An indent is a tab stop of at least a quarter more than one em, and glyph
    jitter -- which comes from differing bullet advance widths -- is less.

    BUT ONLY WHERE THE BOX IS EXACT. In ems, Year 2's jitter is LARGER than Year 1's real
    indent: 19.5pt at 10pt type is 1.95em, against Year 1's 18.4pt at 12pt type at 1.53em.
    The two are geometrically indistinguishable because the difference is not geometry --
    it is where the box came from. Year 1's edges are text-layer exact, so 18.4pt is a
    typographic decision; Year 2 is image-based, its boxes are predicted by OCR, and the
    same 19.5pt is model noise across twelve grants that are all one level. An OCR box
    cannot support a fine indent judgement, so an OCR-sourced block keeps the conservative
    fixed step this pipeline has already verified against the hand-healed Year 2, and only
    a character-exact box is measured in ems.
    """
    # PER PAGE. Columns are a property of a page layout, not of a document: computing
    # one edge set across the whole file made Year 5 page 6's right-column bullets
    # (x0=282, plain siblings of the left column's x0=54) come out as level 1, because
    # edges contributed by other pages landed between them.
    by_page: dict[int, list[float]] = {}
    for b in blocks:
        if b.get("x0") is not None:
            by_page.setdefault(b.get("page_no"), []).append(b["x0"])
    edges_for = {pg: column_edges(xs) for pg, xs in by_page.items()}

    # A LADDER, NOT A STACK. Each column keeps the indents it has seen, in order, and a
    # block's level is its rung's distance from the rung the column ENTERED on. A stack
    # made the entry depth a floor, so Year 2 page 12's right column -- which enters mid
    # sub-list at indent 38 and later returns to the parent level at indent 1 -- could
    # never come back up, and four parent bullets rendered as children of an awards list
    # they have nothing to do with.
    ladder: list[float] = []
    anchor = 0
    level, base, cur_col = 0, 0, None
    for b in blocks:
        edges = edges_for.get(b.get("page_no")) or [b.get("x0", 0.0)]
        if b.get("kind") != "ListItem" or b.get("x0") is None:
            b["list_level"] = None
            ladder, anchor, level, base, cur_col = [], 0, 0, 0, None
            continue
        col = max([e for e in edges if e <= b["x0"] + 1] or [edges[0]])
        indent = b["x0"] - col
        if col != cur_col:
            base = level if cur_col is not None else 0
            cur_col, ladder, anchor = col, [indent], 0
            level = base
        else:
            near = min(range(len(ladder)), key=lambda i: abs(ladder[i] - indent))
            step = (_OCR_INDENT_STEP if b.get("text_source") == "docling_ocr"
                    else _INDENT_EMS * ((ems or {}).get(b.get("page_no")) or _DEFAULT_EM))
            if indent > ladder[-1] + step:
                ladder.append(indent)
                rung = len(ladder) - 1
            elif indent < ladder[0] - step:
                ladder.insert(0, indent)
                anchor += 1
                rung = 0
            else:
                rung = near
                del ladder[rung + 1:]
            level = max(base + rung - anchor, 0)
        b["list_level"] = level


def is_marker_run(words) -> bool:
    """True if every token is a bare list-marker glyph, so the run asserts nothing.

    BOTH SWEEPS NEED THIS, which is why it is a function and not a line in one of them.
    Year 2 pages 12-13 emitted "> o o o o o" and "> o o o o o o o o o o o o" -- the
    markers of a sub-list whose WORDS Docling had already claimed. The geometric sweep
    can see them as words outside every box; the content sweep sees them as tokens
    pdfplumber read that never reached the assembled text, which is literally true and
    still not content. Fixing only the geometric path left both runs in place.

    Only a run that is ENTIRELY markers is dropped. A marker beside real words travels
    with them, because then the words are the evidence.
    """
    toks = [t.strip() for t in words if t and t.strip()]
    return bool(toks) and all(t in _MARKER_GLYPHS for t in toks)


_MARKER_GLYPHS = {"o", "O", "\u2022", "\u00b7", "\u25aa", "\u25e6", "\u2023", "\u2043",
                  "-", "\u2013", "\u2014", "*", "\u25cf", "\u25a0", "\u2219"}

_FURNITURE_BAND = 0.88     # fraction of page height below which a footer can sit


def _shape(text: str) -> str:
    """A block's structural shape: digits and names collapsed, so a footer template
    matches across pages even though its number, strategy and staff member differ."""
    t = re.sub(r"\d+", "#", (text or "").lower())
    return " ".join(re.findall(r"[a-z#]{2,}", t)[:8])


def mark_furniture(blocks: list[dict], min_occurrences: int = 3) -> None:
    """Set `is_furniture` on repeating bottom-margin blocks, in place.

    Year 2 repeats a per-strategy footer on seven pages: "N For more information on
    activities to support Strategy N, please contact <staff> (<email>)". That text is
    worth keeping -- it is the staff roster, and belongs to `people` -- but it asserts
    nothing about the world, and extracting seven of them as claims would manufacture
    seven statements the report never makes.

    Detected on POSITION PLUS REPETITION, never on keywords. Position alone would catch
    the last bullet of every page; repetition alone would catch any recurring sentence in
    the body. A template whose numbers and names vary is normalised by _shape() so the
    seven variants recognise each other as one pattern.
    """
    cand: dict[str, list[dict]] = {}
    for b in blocks:
        b.setdefault("is_furniture", False)
        top, ph = b.get("top"), b.get("page_h")
        if top is None or not ph or top < ph * _FURNITURE_BAND:
            continue
        cand.setdefault(_shape(b.get("text", "")), []).append(b)
    for shape, group in cand.items():
        if shape and len({b["page_no"] for b in group}) >= min_occurrences:
            for b in group:
                b["is_furniture"] = True


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


def body_shapes(blocks: list[dict], min_body: int = 2) -> set[str]:
    """Leading shapes that appear mostly on BODY blocks, so a heading carrying one is
    almost certainly a mistype.

    The mirror of heading_shapes(), and needed for the same reason in the other
    direction. Year 2 has five "DIVE DEEPER into X:" pull-out boxes; Docling typed four
    as TextItem and one as a SectionHeaderItem, which split that callout in half and
    inserted a fake `##` that detached the following bullets from their real strategy
    heading. The document's own majority says what the shape is.
    """
    body: dict[str, int] = {}
    head: dict[str, int] = {}
    for b in blocks:
        sh = " ".join(re.findall(r"[a-z#]{2,}",
                                 re.sub(r"\d+", "#", (b.get("text") or "").lower()))[:3])
        if not sh:
            continue
        (head if b.get("kind") in ("SectionHeaderItem", "TitleItem") else body)[sh] = \
            (head if b.get("kind") in ("SectionHeaderItem", "TitleItem") else body).get(sh, 0) + 1
    return {sh for sh, n in body.items() if n >= min_body and n > head.get(sh, 0)}


_HEADING_MERGE_GAP = 6.0    # pt; heading fragments sit within this of each other


def merge_overlapping_headings(blocks: list[dict]) -> list[dict]:
    """Merge heading boxes that overlap or touch into one box covering their union.

    Year 1 sets its strategy headings as display type over two or three lines, and
    Docling emits a box per fragment. The boxes overlap, and on page 3 one NESTS inside
    another while being narrower: box B (x143-414) spans box A (x144-504) vertically but
    stops 90pt short of it horizontally, so cropping B alone cut "vehicles" out of the
    middle of the heading. The result was not a split heading but a scrambled one --
    "Strategy 2: Switch our appliances and from fossil fuels to electric".

    Cropping the UNION restores reading order and the full width, which is why the merge
    happens here on the boxes rather than by pasting the fragments' text together: the
    fragments' own text is exactly what is missing a word.

    Headings only. Merging a bullet into a heading would swallow real content, and the
    fragments of a heading are always themselves typed as headings.
    """
    HEAD = ("SectionHeaderItem", "TitleItem")
    out: list[dict] = []
    for b in blocks:
        prev = out[-1] if out else None
        if (prev is not None and b.get("kind") in HEAD and prev.get("kind") in HEAD
                and b.get("page_no") == prev.get("page_no")):
            ph = prev.get("page_h") or 792.0
            pt, pb = sorted((ph - prev["bbox"][1], ph - prev["bbox"][3]))
            bt, bb = sorted((ph - b["bbox"][1], ph - b["bbox"][3]))
            px0, px1 = sorted((prev["bbox"][0], prev["bbox"][2]))
            bx0, bx1 = sorted((b["bbox"][0], b["bbox"][2]))
            v_touch = bt <= pb + _HEADING_MERGE_GAP and bb >= pt - _HEADING_MERGE_GAP
            h_touch = bx0 <= px1 and bx1 >= px0
            # A HEADING ENDING IN A COLON IS HALF A HEADING. Year 3 separates its label
            # from its title by ~50pt of page -- "STRATEGY ONE:" then "POWER OUR
            # ELECTRICAL GRID WITH 100% RENEWABLE ENERGY" -- which is far too wide to
            # merge on proximity without risking two real headings. The colon says it
            # outright. Adjacency in the block stream is the guard: if body text sits
            # between them they are two sections, whatever the punctuation.
            label_split = prev.get("docling_text", "").rstrip().endswith(":")
            if (v_touch and h_touch) or label_split:
                prev["bbox"] = [min(px0, bx0), ph - min(pt, bt),
                                max(px1, bx1), ph - max(pb, bb)]
                prev["docling_text"] = (prev.get("docling_text", "") + " "
                                        + b.get("docling_text", "")).strip()
                continue
        out.append(dict(b))
    return out


def recurring_lead_ins(blocks: list[dict], min_sections: int = 3) -> set[str]:
    """Heading texts that recur under several DIFFERENT sections -- list lead-ins.

    Year 1 types "In Year One, we:" as a heading seven times, once under each strategy.
    It introduces that strategy's bullet list; promoting it to `##` cut every strategy in
    half and detached its achievements from the strategy they belong to.

    body_shapes() cannot catch this, because Docling types the line as a heading EVERY
    time -- the document's own majority agrees with the mistake. What distinguishes a
    lead-in is that it recurs under DIFFERENT sections, where a real section heading names
    exactly one. Consecutive repeats are excluded: those are one section resuming on a new
    page (Year 5's "GREENHOUSE GAS EMISSIONS SUMMARY"), which the continuation merge
    already handles and which must not be demoted to body text.
    """
    HEAD = ("SectionHeaderItem", "TitleItem")
    seq = [" ".join((b.get("text") or b.get("docling_text") or "").lower().split())
           for b in blocks if b.get("kind") in HEAD]
    seen: dict[str, int] = {}
    for i, t in enumerate(seq):
        if not t or (i and seq[i - 1] == t):
            continue                      # consecutive repeat = continuation, not lead-in
        seen[t] = seen.get(t, 0) + 1
    return {t for t, n in seen.items() if n >= min_sections}


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


def overrides_caption_link(block: dict, shapes: set[str] | None) -> bool:
    """Positive counter-evidence that Docling's caption link is wrong.

    Two signals, both independent of who proposed the link:
      heading shape    the block opens with a "WORD N:" shape the document confirms as a
                       heading elsewhere (Year 4's STRATEGY 6).
      lowercase start  the block opens mid-sentence, so it continues the block above it
                       rather than describing a picture (Year 3 page 9's "the downtown,
                       reducing vehicle/bicyclist conflicts.", which Docling linked to a
                       photograph and which therefore vanished from the report).

    A caption is a standalone phrase; neither signal becomes weaker because Docling
    rather than geometry proposed the link.
    """
    if _matches_heading_shape(block, shapes):
        return True
    words = (block.get("text") or "").split()
    return bool(words) and words[0][:1].islower()


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
    if not (0 < len(words) <= _CAPTION_MAX_WORDS):
        return False
    # A CAPTION BEGINS AS A STANDALONE PHRASE. Docling sometimes splits one sentence into
    # two blocks, and on Year 3 page 13 the tail -- "and in-person events designed to
    # unlock their potential..." -- sat beside a photo, ran to 14 words, and was dropped
    # as that photo's caption, taking "grew to over 120 organizations!" with it. A block
    # opening with a lowercase word is the continuation of the sentence above it.
    return not words[0][:1].islower()


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


_RUN_NEUTRAL = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "at", "by", "with",
    "is", "are", "was", "were", "as", "that", "this", "it", "its", "our", "we",
}


def missing_runs(page_words: list[str], assembled: str,
                 min_run: int = 3) -> list[list[str]]:
    """Runs of consecutive page words absent from the assembled text.

    The content counterpart to uncovered_words(), which asks only whether a block's BOX
    contains a word. Year 3 page 9 proves that insufficient: "the downtown, reducing
    vehicle/bicyclist conflicts" lies inside a block's box -- so the geometric sweep
    skipped it -- while that block's crop never returned it, and the sentence shipped as
    "...restrict turns on red lights in".

    Runs, not single words, because one token differing is a hyphen decision or OCR
    jitter; three consecutive words missing is a dropped line.
    """
    norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())
    hay = {w for w in (norm(x) for x in assembled.split()) if w}

    # A STOPWORD NEITHER PROVES NOR BREAKS A GAP. Plain membership missed "the downtown,
    # reducing vehicle/bicyclist conflicts" entirely, because "the" occurs elsewhere on
    # the page and split the run into fragments below the minimum. Requiring whole
    # n-grams instead over-flagged, marking every position that merely PRECEDED a gap.
    # Treating function words as neutral -- they extend an open run but never start one
    # and never close one -- separates both cases.
    runs, cur = [], []
    for w in page_words:
        n = norm(w)
        if not n:
            continue
        if n in _RUN_NEUTRAL:
            if cur:
                cur.append(w)
            continue
        if n in hay:
            if len(cur) >= min_run:
                runs.append(cur)
            cur = []
        else:
            cur.append(w)
    if len(cur) >= min_run:
        runs.append(cur)
    return [r for r in runs
            if sum(1 for w in r if norm(w) not in _RUN_NEUTRAL) >= min_run]


def already_present(candidate: str, assembled: str) -> bool:
    """Whether a recovered region's text is already in the assembled page text.

    The geometric sweep asks whether a word's CENTRE lies in some block's box; pdfplumber's
    crop() keeps any word INTERSECTING the box. Where a box sits slightly off its own text
    -- Year 1's are ~8pt low -- the two disagree, and the sweep re-adds text the crop
    already captured. That produced a duplicate stub of three real bullets and an eighth
    copy of "In Year One, we:".

    Being outside a box is not the same as being absent from the document, and only the
    second one justifies recovery.
    """
    norm = lambda t: " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split())
    c = norm(candidate)
    return bool(c) and c in norm(assembled)


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

    # A RUN OF BARE BULLET GLYPHS IS NOT RECOVERED TEXT. Year 2 pages 12 and 13 emitted
    # "> o o o o o" and "> o o": the markers of a sub-list whose words Docling had already
    # claimed, left behind by the crop and swept up as though they were content. They
    # assert nothing, and unlike a stray word there is no reading under which they are
    # text. PER GROUP, not per page -- a page's leftovers are grouped into lines only
    # here, so testing the whole page fired solely when the markers were all that was
    # left over, which on Year 2 they were not.
    groups = [g for g in groups if not is_marker_run(w["text"] for w in g)]
    if not groups:
        return []

    # AN INDEX KEEPS ITS LINES. Merging a group's lines is right for a wrapped caption
    # and wrong for a table of contents, where each line is a separate entry -- Year 4's
    # TOC arrived as one run-on line. The document supplies the discriminator: index
    # entries each end in their own page number, and one such line proves nothing while a
    # repeated structure does.
    def _is_index(lines: list[list[dict]]) -> bool:
        if len(lines) < 2:
            return False
        ends = sum(1 for ln in lines
                   if re.search(r"\b\d{1,3}$", " ".join(w["text"] for w in ln).strip()))
        return ends >= max(2, int(0.6 * len(lines)))

    expanded = []
    for g in groups:
        rows: dict[int, list[dict]] = {}
        for w in g:
            rows.setdefault(round(w["top"] / 6), []).append(w)
        lines = [rows[k] for k in sorted(rows)]
        expanded.extend(lines if _is_index(lines) else [g])
    groups = expanded

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
        if not text or text.isdigit() or not re.search(r"[A-Za-z0-9]", text):
            continue        # a bullet glyph with no words is punctuation, not content
        out.append({"kind": "UncoveredText", "page_no": page_no,
                    "bbox": [min(w["x0"] for w in g), min(w["top"] for w in g),
                             max(w["x1"] for w in g), max(w["bottom"] for w in g)],
                    "coord_origin": "CoordOrigin.TOPLEFT",
                    "page_w": None, "page_h": None, "self_ref": None,
                    "caption_for": None, "docling_text": "", "text": text})
    return out


def load_ocr_terms(path="registries/ann_arbor/ocr_terms.json") -> list[dict]:
    """Corpus terms OCR reliably mangles. Missing registry -> no corrections, never a crash."""
    import json as _json
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        return []
    return _json.loads(p.read_text()).get("terms", [])


def convert(pdf_path, blocks_path, ocr_terms: list[dict] | None = None):
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
    from pdfplumber import utils as pdf_text
    from pathlib import Path
    from pipeline.convert_document import Conversion

    if ocr_terms is None:
        ocr_terms = load_ocr_terms()
    spec = json.loads(Path(blocks_path).read_text())
    n_pages, blocks = spec["n_pages"], spec["blocks"]
    blocks = merge_overlapping_headings(blocks)

    raws: list[str] = []
    ems: dict[int, float] = {}
    n_snapped = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            ink = [c for c in page.chars if not c["text"].isspace()]
            if ink:
                ems[pno] = Counter(round(c["size"], 1) for c in ink).most_common(1)[0][0]
        blocks = absorb_body_shaped_headings(blocks, ems)
        for b in blocks:
            if b["kind"] == "PictureItem":
                raws.append("")
                continue
            page = pdf.pages[b["page_no"] - 1]
            box = bbox_to_crop(tuple(b["bbox"]), b["coord_origin"],
                               b["page_h"], b["page_w"])
            # THE PAD IS FOR GLYPHS, NOT FOR NEIGHBOURS. bbox_to_crop pads by 2pt so a
            # block's own descenders and side-bearings are not clipped, but pdfplumber's
            # crop() then keeps any word INTERSECTING the padded box -- and on Year 3 that
            # 2pt is exactly enough to swallow the line above. Page 12's header sits at
            # top=132.67 and the paragraph below it at 135.70, so both land in one crop
            # and, being 3.03pt apart, inside pdfplumber's 3pt line tolerance. The two runs
            # are then sorted together by x0 and woven character by character:
            #
            #   EnhaEnNciHngA NthCeE rTeHsiEli eRnEcSeIL oIEf NoCurE ...
            #   thJurlye 1,e 20 A22-nJunneu 3a, 20l2 3R
            #
            # Both are two real sentences interleaved, and neither is recoverable
            # afterwards -- de-hyphenation, heading repair and the gate all run on the
            # wreckage. So the crop stays generous and the DECISION is made per character:
            # a char belongs to the block whose TRUE box holds its vertical centre. That
            # is the same centre test the coverage sweep already uses; the two halves of
            # the pipeline simply disagreed about it, and the crop's half was wrong.
            true_box = bbox_to_crop(tuple(b["bbox"]), b["coord_origin"],
                                    b["page_h"], b["page_w"], pad=0.0)
            crop = page.crop(box)
            # Only take the corrected path when a glyph actually moved, so a document
            # with no super/subscripts extracts through exactly the code it always did.
            # SNAP FIRST, THEN DECIDE. A superscript is drawn ABOVE its line, so its
            # centre can sit outside the block's box while the line it belongs to sits
            # inside -- filtering first deleted the 2 from three of Year 1's A2ZEROs.
            # snap_scripts puts each script glyph on its own line's band, after which the
            # centre test asks the question it means to ask: which line is this, and does
            # that line belong to this block.
            snapped, moved = snap_scripts(crop.chars)
            fixed = [c for c in snapped
                     if true_box[1] <= (c["top"] + c["bottom"]) / 2 <= true_box[3]]
            n_snapped += moved
            raws.append((pdf_text.extract_text(fixed) if fixed else "") or "")

    words, hyph = build_evidence("\n".join(raws))

    ambiguous: list[tuple[str, str]] = []
    term_fixes: list[tuple[str, str]] = []
    content_recovered: list[tuple[int, str]] = []
    captioned: list[tuple[int, str]] = []
    resolved: list[dict] = []
    for i, (b, raw) in enumerate(zip(blocks, raws)):
        if b["kind"] == "PictureItem":
            continue                      # no text; re-interleaved for the renderer below
        raw = trim_to_docling(raw, b.get("docling_text") or "")
        raw, src = choose_block_text(raw, b.get("docling_text") or "")
        if src == "docling_ocr" and ocr_terms:
            # OCR-sourced only: a text layer's characters are authoritative, and A2ZERO
            # set as A²ZERO is typography to preserve, not a misread to repair.
            raw, fixes = normalize_ocr_terms(raw, ocr_terms)
            term_fixes.extend(fixes)
        fixed, amb = apply_hyphen_decisions(raw, words, hyph)
        ambiguous.extend(amb)
        bullets = split_on_docling_bullets(fixed, b.get("docling_text") or "")
        if bullets:
            for j, item in enumerate(bullets):
                resolved.append({**b, "kind": "ListItem", "text": flatten_block(item),
                                 "_ord": float(i) + 0.001 * j, "text_source": src,
                                 "_caption_src": None, "caption_for": None})
            continue

        idx = split_index_lines(fixed)
        if idx:
            for j, ln in enumerate(idx):
                resolved.append({**b, "text": ln, "_ord": float(i) + 0.001 * j,
                                 "text_source": src,
                                 "_caption_src": None, "caption_for": None})
            continue
        text = flatten_block(fixed)
        if text and is_page_footer({"text": text, "page_no": b["page_no"]}):
            continue                     # a page footer is not prose
        if text:
            resolved.append({**b, "text": text, "_ord": float(i), "text_source": src,
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
            # Geometry proposes; the assembled text decides. See already_present().
            page_text = " ".join(b["text"] for b in resolved if b["page_no"] == pno)
            strays = [s for s in strays if not already_present(s["text"], page_text)]
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
        ok = (not overrides_caption_link(b, shapes)
              if b.get("_caption_src") == "docling"
              else is_caption_candidate(b, shapes))
        if ok:
            captioned.append((b["page_no"], b["text"][:70]))
        else:
            b["caption_for"] = None          # not a caption; keep it in the document

    # Geometry in TOP-LEFT space for the two structural passes below. Docling reports
    # BOTTOMLEFT, and both passes reason about "further down the page" and "further in
    # from the left", so they need the same orientation the reader has.
    page_h = {b["page_no"]: b.get("page_h") for b in blocks if b.get("page_h")}
    for b in resolved:
        bb = b.get("bbox")
        if not bb:
            continue
        ph = b.get("page_h") or page_h.get(b["page_no"])
        if b.get("coord_origin", "").find("BOTTOMLEFT") >= 0 and ph:
            b["top"] = min(ph - bb[1], ph - bb[3])
        else:
            b["top"] = min(bb[1], bb[3])
        b["x0"] = min(bb[0], bb[2])
        b["page_h"] = ph
    # SECOND SWEEP, BY CONTENT. The geometric sweep asks whether a block's BOX contains a
    # word; this asks whether the word survived into the text we actually assembled. Year
    # 3 page 9 needs both: "the downtown, reducing vehicle/bicyclist conflicts" sits
    # inside a block's box -- so geometry called it covered -- while that block's crop
    # never returned it, and the sentence shipped as "...restrict turns on red lights in".
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno in range(1, n_pages + 1):
            assembled = " ".join(b["text"] for b in resolved if b["page_no"] == pno)
            if not assembled:
                continue
            page = pdf.pages[pno - 1]
            words = [w["text"] for w in page.extract_words()]
            for run in missing_runs(words, assembled):
                txt = " ".join(run)
                if is_page_footer({"text": txt, "page_no": pno}):
                    continue
                if is_marker_run(run):
                    continue        # bullet glyphs whose words are already assembled
                at = max((i for i, b in enumerate(resolved) if b["page_no"] <= pno),
                         default=-1) + 1
                prev = resolved[at - 1]["_ord"] if at > 0 else -1.0
                resolved.insert(at, {"kind": "UncoveredText", "page_no": pno,
                                     "text": txt, "_ord": prev + 0.0005,
                                     "_swept": True, "caption_for": None,
                                     "text_source": "pdfplumber", "bbox": None,
                                     "coord_origin": "", "page_w": None,
                                     "page_h": None, "self_ref": None,
                                     "docling_text": ""})
                content_recovered.append((pno, txt[:70]))

    n_joined = rejoin_open_sentences(resolved, words, hyph)
    assign_nesting(resolved, ems)
    mark_furniture(resolved)

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
                                 "captions_associated": captioned,
                                 "ocr_term_fixes": term_fixes,
                                 "content_recovered": content_recovered}
