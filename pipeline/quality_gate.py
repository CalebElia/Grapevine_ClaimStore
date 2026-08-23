"""Refuse a bad conversion instead of reporting success on it.

WHY THIS IS THE LOAD-BEARING PIECE. Every failure this corpus has produced was SILENT.
pdfplumber returned 234 words and zero of Year 2's dollar figures, exited cleanly, and
raised nothing. The block pipeline reproduced that exactly -- same 234 words, same zero
figures, same success message -- and would have carried it into the claim store if a
human had not asked for the test. Reporting a number is not the same as refusing to
proceed on it, and "nothing errored" has never been evidence of a good parse here.

THE CHECKS THAT EARN THEIR PLACE, each tied to a failure actually observed:

  text_recovery         the assembled text against the best INDEPENDENT read available.
                        Year 2 assembled 234 words where 2,995 were readable -- 8%. This
                        is the check that catches a converter silently failing on a whole
                        document, and it is the one that would have fired first.
  numeric_conservation  a figure present in an independent read and absent from the
                        output. Year 2 lost all 15 dollar figures. A number is the most
                        citable thing a parse can drop and the least visible when it
                        goes.
  span_round_trip       text[char_start:char_end] must BE the block. The offsets are the
                        citation; if they drift, every claim anchored to them points at
                        something else, and nothing about the output looks wrong.
  page_map              offsets must advance monotonically. A page map that overlaps
                        misattributes a citation to the wrong page while still resolving.
  ocr_fraction          MEDIUM, never high. Year 2 is 94% OCR and is nonetheless the best
                        reading of that document that exists -- refusing it would discard
                        a real source for the sin of being a scan. But OCR text is a
                        model's reading of pixels, not character-exact text, and a claim
                        drawn from it deserves a different evidence grade.

WHY OCR IS NOT A FAILURE AND SHORTFALL IS. The distinction the whole gate turns on: a
scanned document is fully readable and merely lower-grade, while a document whose text
was dropped is not readable at all and looks identical to a clean one from the outside.
The first needs a label; the second needs a stop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A conversion recovering less of the document than this, against the best independent
# read, is not a degraded parse but a failed one. Calibrated against the three real
# documents: Year 2 post-fix sits at 96%, Years 4 and 5 at ~100%, and Year 2 pre-fix --
# the failure this exists to catch -- at 8%. Nothing observed lands between 8% and 96%,
# so the threshold is set where the gap is, not at a round number.
_MIN_TEXT_RECOVERY = 0.60
_MIN_NUMERIC_RECOVERY = 0.80
_OCR_NOTABLE = 0.20

# Words that cannot end a sentence. A block ending on one, with no terminal punctuation,
# is a sentence that stopped rather than one that finished -- the signature of a dropped
# continuation line. Found on Year 2, where RapidOCR truncated three grant bullets at
# "improvements at", "the purchase of" and "the purchase of another" while every currency
# figure and 96% of the word volume survived, so no volume-based check could see it.
_DANGLING = {
    "a", "an", "and", "at", "but", "by", "for", "from", "in", "including", "into", "of",
    "on", "or", "our", "the", "their", "to", "with", "another", "not", "as", "that",
}

_MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s?(?:million|billion|k|M|B))?", re.I)


@dataclass
class Finding:
    check: str
    severity: str          # high | medium
    evidence: str
    # The specific blocks this finding is ABOUT. `evidence` is a summary for a console
    # line; a reviewer needs to visit each one. Emitting only the summary produced a work
    # item reading "2 block(s) end on a dangling word" with a single example, whose
    # snippet had been sliced mid-word and so could not be located at all.
    items: list = field(default_factory=list)


def find_numbers(text: str) -> set[str]:
    """Currency figures, normalised on whitespace so a double space cannot hide one.

    Deliberately currency only. Every integer in a document includes page numbers, years
    and list markers, and a check that fires on those would be ignored within a week --
    which is the same as not having it.
    """
    return {re.sub(r"\s+", " ", m.group(0)).strip() for m in _MONEY.finditer(text)}


# LEGISLATIVE ENUMERATION. A resolution, ordinance or set of findings joins its clauses with
# a semicolon and a conjunction, so every clause but the last ends "; and". Those clauses are
# COMPLETE. The CAP closes with two pages of the adopting resolution and every one of them
# tripped the truncation check.
#
# Only a semicolon IMMEDIATELY before the conjunction counts. "Chapter; The American
# Institute of" also contains a semicolon and really is cut off.
_ENUMERATED = re.compile(r";\s*(and|or)\s*$", re.I)


def is_enumerated_clause(text: str | None) -> bool:
    """Whether a block ends on a conjunction by legislative convention rather than by loss."""
    return bool(_ENUMERATED.search((text or "").strip()))


def assess(text: str, page_map: list[tuple[int, int, int]], blocks: list[dict],
           reference_words: int, reference_numbers: set[str]) -> list[Finding]:
    """Every finding for one conversion. Empty means nothing fired.

    `reference_*` describe the best INDEPENDENT read of the same PDF -- the other
    converter arm. Comparing a conversion only against itself cannot detect that it lost
    the document, because a converter that reads nothing is perfectly self-consistent.
    """
    out: list[Finding] = []
    words = len(text.split())

    if reference_words > 0:
        ratio = words / reference_words
        if ratio < _MIN_TEXT_RECOVERY:
            out.append(Finding("text_recovery", "high",
                               f"{words:,} words assembled against {reference_words:,} "
                               f"readable -- {ratio:.0%} of the independent read"))

    if reference_numbers:
        got = find_numbers(text)
        missing = {n for n in reference_numbers
                   if n not in got and n.replace(" ", "") not in
                   {g.replace(" ", "") for g in got}}
        kept = len(reference_numbers) - len(missing)
        if kept / len(reference_numbers) < _MIN_NUMERIC_RECOVERY:
            out.append(Finding("numeric_conservation", "high",
                               f"{len(missing)} of {len(reference_numbers)} currency "
                               f"figures absent from the output: "
                               f"{sorted(missing)[:6]}"))

    bad_spans = [b for b in blocks
                 if b.get("char_start") is not None
                 and text[b["char_start"]:b["char_end"]] != b.get("text")]
    if bad_spans:
        out.append(Finding("span_round_trip", "high",
                           f"{len(bad_spans)} block(s) whose span does not slice back to "
                           f"their own text, e.g. p.{bad_spans[0].get('page_no')} "
                           f"{(bad_spans[0].get('text') or '')[:40]!r}"))

    for (pa, _, ea), (pb, sb, _) in zip(page_map, page_map[1:]):
        if sb < ea:
            out.append(Finding("page_map", "high",
                               f"page {pb} starts at {sb} before page {pa} ends at {ea}"))
            break

    truncated = []
    for b in blocks:
        if b.get("kind") in ("SectionHeaderItem", "TitleItem", "PictureItem"):
            continue
        t = (b.get("text") or "").strip()
        if not t or t[-1] in ".!?:;\u2019\"')":
            continue
        if is_enumerated_clause(t):
            continue        # "...; and" is a complete clause, not a lost line
        if t.split()[-1].lower().strip(",") in _DANGLING:
            truncated.append(b)
    if truncated:
        out.append(Finding("truncation", "medium",
                           f"{len(truncated)} block(s) end on a dangling word -- a "
                           f"continuation line was probably dropped",
                           items=truncated))

    # EVERY OTHER CHECK HERE COUNTS THINGS. Words recovered, numbers conserved, spans
    # round-tripping, blocks ending short -- all quantity, and all of them pass a block
    # whose characters are perfectly preserved and merely in the wrong ORDER. Year 3
    # shipped two sentences woven together, "EnhaEnNciHngA NthCeE rTeHsiEli eRnEcSeIL",
    # through a clean gate and a 135-item checklist, and a human found it by reading.
    #
    # This is the first check that asks whether the output is PLAUSIBLE rather than
    # complete. Interleaving is self-announcing once anything looks: English words do not
    # alternate case internally, so a token with two or more lower-to-upper transitions
    # inside it is not a word. Measured across all five reports it fires on 13 tokens in
    # the broken Year 3 and none at all in Years 1, 2, 4 and 5 -- no threshold to tune and
    # nothing legitimate caught, because "AmeriCorps" and "SolSmart" have exactly one
    # transition and a woven line has one per syllable.
    garbled = []
    for b in blocks:
        for tok in (b.get("text") or "").split():
            if len(re.findall(r"[a-z][A-Z]", tok)) >= 2:
                garbled.append({"page_no": b.get("page_no"), "token": tok,
                                "text": " ".join((b.get("text") or "").split())[:160]})
    if garbled:
        out.append(Finding("garbled_text", "high",
                           f"{len(garbled)} token(s) alternate case internally, which no "
                           f"English word does -- two text runs were probably interleaved",
                           items=garbled))

    srcs = [b.get("text_source") for b in blocks if b.get("text_source")]
    if srcs:
        frac = sum(1 for s in srcs if s == "docling_ocr") / len(srcs)
        if frac >= _OCR_NOTABLE:
            out.append(Finding("ocr_fraction", "medium",
                               f"{frac:.0%} of text blocks read by OCR -- not "
                               f"character-exact; claims from this document need a "
                               f"lower evidence grade"))
    return out


def verdict(findings: list[Finding]) -> str:
    """'refuse' | 'review' | 'pass'.

    A high finding refuses: the document is not readable as converted, and storing claims
    against it would anchor them to text that is not there. A medium finding passes with
    a label -- Year 2 is 94% OCR and still the best reading of that report in existence.
    """
    if any(f.severity == "high" for f in findings):
        return "refuse"
    return "review" if findings else "pass"


def report(findings: list[Finding], label: str = "") -> str:
    v = verdict(findings)
    lines = [f"[gate] {label}: {v.upper()}"]
    for f in findings:
        lines.append(f"[gate]   {f.severity.upper():<6} {f.check}: {f.evidence}")
    if v == "pass":
        lines.append("[gate]   no findings")
    return "\n".join(lines)
