"""Footnotes: the numbered body at the foot of a page, and the marker that points at it.

WHAT WAS ALREADY RIGHT. The bodies were being captured and tagged FURNITURE, which kept them
out of claim-bearing prose. That was correct as far as it went. What FURNITURE cannot say is
that a block is a footnote, which number it carries, or which words point at it -- and in
this corpus those seven blocks name the officer responsible for each A2ZERO strategy, with
an email address. Tagged as generic furniture they are noise; modelled as footnotes they are
seven (person, strategy) facts.

WHAT WAS WRONG. The MARKER side. On Year 2 page 8 the prose reads "In Year Two, we⁴:" and
Docling's OCR rendered the superscript as a letter, giving "wet:". Four of the six markers
vanished entirely. So the prose carried a word that is not a word, the second arm read a
digit there, and the two arms disagreed for ever with no way to settle it -- which is where
two sections of Year 2 have been stuck.

THREE PIECES OF EVIDENCE, ALL REQUIRED. A trailing character is treated as a marker only
when the second arm reads a DIGIT in that position, AND a footnote carrying that number
exists on the page, AND the two readings are otherwise identical. Any one alone is not
enough: OCR arms disagree about trailing characters constantly, and a digit that matches no
footnote is a quantity or a year. Requiring all three is what stops "week" losing its "k"
because some footnote happens to be numbered 4.
"""
from __future__ import annotations

import re

# A footnote marker is small. Two digits is generous for a document with seven.
_MAX_FOOTNOTE = 99

# "4 For more information..." -- a small integer, then a capitalised sentence. The capital
# is load-bearing: it separates a footnote from prose that opens on a quantity, like
# "250 residents registered as participants".
_BODY = re.compile(r"^\s*(\d{1,2})\s+([A-Z].*)$", re.S)


def parse_footnote_body(text: str) -> dict | None:
    """A footnote body -> its number and its text, or None if this is not one."""
    m = _BODY.match(text or "")
    if not m:
        return None
    n = int(m.group(1))
    if not 1 <= n <= _MAX_FOOTNOTE:
        return None
    body = m.group(2).strip()
    # A body has to say something. A bare number with a stray capital is a page artefact.
    if len(body.split()) < 3:
        return None
    return {"number": n, "text": body}


def strip_reference(primary: str, second: str,
                    footnote_numbers: set[int]) -> tuple[str, list[int]]:
    """Remove footnote markers from prose, returning the cleaned text and the numbers found.

    Compares the two arms token by token. A token is a marker-bearing word when the second
    arm's version is the primary's stem plus a digit, or the primary's version is that stem
    plus one stray character where the second arm has the digit -- and the digit names a
    footnote that exists.
    """
    p_words = primary.split()
    # THE SECOND ARM EMITS STANDALONE PUNCTUATION. It renders bullets as a lone ".", so its
    # token count exceeds the primary's and a strict one-for-one check bails out -- which is
    # what left both real Year 2 markers unresolved. Tokens with no word characters at all
    # carry no content, so dropping them loses nothing and the two sides must still align
    # exactly afterwards.
    s_words = [w for w in second.split() if re.search(r"\w", w)]
    if len(p_words) != len(s_words):
        return primary, []

    refs: list[int] = []
    out: list[str] = []
    for pw, sw in zip(p_words, s_words):
        stem, n = _marker(pw, sw, footnote_numbers)
        if n is None:
            out.append(pw)
            continue
        refs.append(n)
        out.append(stem)
    return " ".join(out), refs


def _marker(pw: str, sw: str, numbers: set[int]) -> tuple[str, int | None]:
    """(word without its marker, marker number) for one aligned pair of tokens."""
    # Trailing punctuation belongs to the sentence, not to the marker: "we4:" -> "we4" + ":"
    pm = re.match(r"^(.*?)([^\w]*)$", pw, re.S)
    sm = re.match(r"^(.*?)([^\w]*)$", sw, re.S)
    p_core, p_tail = pm.group(1), pm.group(2)
    s_core = sm.group(1)

    dm = re.match(r"^(.*?)(\d{1,2})$", s_core, re.S)
    if not dm:
        return pw, None
    stem, digits = dm.group(1), int(dm.group(2))
    if digits not in numbers or not stem:
        return pw, None

    # THREE WAYS THE PRIMARY CAN HAVE READ THE SUPERSCRIPT:
    #   dropped it        p_core == stem                    "we"
    #   mangled a letter  p_core == stem + one char          "wet"
    #   mangled a mark    the tail carries one extra leading
    #                     non-word character                 "we'" + ":"
    # The third was missed at first because trailing non-word characters were split off as
    # "punctuation" before comparing -- so an apostroph-ised marker was preserved as part of
    # the sentence. It also escaped corroboration, whose word tokenizer strips apostrophes,
    # making "we'" and "we1" compare equal.
    if p_core == stem:
        s_tail = sm.group(2)
        if p_tail != s_tail and p_tail.endswith(s_tail) and len(p_tail) == len(s_tail) + 1:
            return stem + s_tail, digits          # drop the mangled mark, keep the sentence
        return stem + p_tail, digits
    if len(p_core) == len(stem) + 1 and p_core.startswith(stem):
        return stem + p_tail, digits
    return pw, None


def marker_correction(primary: str, second: str,
                      footnote_numbers: set[int]) -> tuple[str, str] | None:
    """(text as written, text with markers removed), or None if nothing needs writing.

    A marker the primary DROPPED needs no correction: the prose is already what the sentence
    says, and the reference is recorded as a field. Only a marker the primary MANGLED into a
    stray character produces an edit, because that character is in the text and is not a
    word.
    """
    cleaned, refs = strip_reference(primary, second, footnote_numbers)
    if not refs or cleaned == primary:
        return None
    return primary, cleaned


def block_correction(block_text: str, second_text: str,
                     footnote_numbers: set[int]) -> tuple[str, str] | None:
    """A marker correction scoped to ONE block, which is the unit both files share.

    The canonical text joins blocks with a separator and drops the markdown's comments and
    quote prefixes, so a multi-block window exists in the coordinate space and nowhere in
    the file. A single block's text survives into both, which makes it the largest anchor a
    write-back can actually locate.
    """
    aligned = " ".join(w for w in second_text.split() if re.search(r"\w", w))
    return marker_correction(block_text, aligned, footnote_numbers)
