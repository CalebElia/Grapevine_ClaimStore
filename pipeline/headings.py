"""What is a heading, and how deep, decided against the document's own contents page.

Docling marks 604 blocks as SectionHeaderItem in the CAP. Three kinds are mixed together:
headings the contents page names, real sub-headings it does not, and a few blocks that are
not headings at all -- a letter's salutation, its sign-off, and one ordinary paragraph.

THE CONTENTS PAGE IS THE AUTHORITY on the first kind. It states which headings exist and at
what depth, so a heading it names takes ITS level rather than one inferred from font size.

EVERY RULE HERE WAS CALIBRATED AGAINST ALL 604 BLOCKS, not against an example, and the
calibration changed two of them:

  * a naive multi-sentence test demoted 44 of 45 flagged blocks -- every named ACTION in the
    plan -- because "1. IMPLEMENT COMMUNITY CHOICE AGGREGATION" has a period followed by a
    capital. A period preceded by a DIGIT is an ordinal, not a sentence end.
  * length cannot demote anything: the CAP's longest contents entry is 23 words, longer than
    the paragraph that needed demoting.

WHAT SURVIVES IS NARROW ON PURPOSE. Ending on a comma or dash fires on exactly two blocks in
the document; the sentence test on exactly one. A rule that demotes a real heading loses
structure silently, which is worse than leaving one bad heading for a reviewer.
"""
from __future__ import annotations

import re

# A salutation or sign-off: "Friends –", "Sincerely,". Exactly two blocks in the CAP.
_TRAILING = re.compile(r"[,–—-]\s*$")

# A sentence break INSIDE the text. The negative lookbehind keeps ordinals out: "1. IMPLEMENT"
# is a numbered heading, "in the Plan. In total" is prose.
_MULTI_SENTENCE = re.compile(r"(?<![0-9])[.!?]\s+[A-Z“]")

# A leading ordinal, which the body uses and the contents does not: the contents says
# "Implement Community Choice Aggregation", the body "1. IMPLEMENT COMMUNITY CHOICE
# AGGREGATION".
_ORDINAL = re.compile(r"^\s*\d+\s*[.:)]\s*")


def norm_heading(text: str | None) -> str:
    """Comparable form: ordinal stripped, case and punctuation removed."""
    return re.sub(r"[^a-z0-9]", "", _ORDINAL.sub("", (text or "")).lower())


def is_heading_shaped(text: str | None) -> bool:
    """Whether a block can be a heading at all, whatever Docling called it."""
    t = (text or "").strip()
    if not t:
        return False
    if _TRAILING.search(t):
        return False
    if _MULTI_SENTENCE.search(t):
        return False
    # A LEAD-IN IS A SENTENCE THAT ENDS IN A COLON, and Caleb's rule from Year 1 is that the
    # colon announces a list. But "STRATEGY 3:" and "OTHER STRATEGIES:" end in one too and
    # are real headings, so the colon alone cannot decide: a lead-in has several words, most
    # of them lowercase. A heading is a label.
    if t.endswith(":"):
        words = t.split()
        if len(words) >= 4 and sum(1 for w in words if w.islower()) >= 2:
            return False
    return True


def match_toc(text: str | None, toc: list[dict]) -> dict | None:
    """The contents entry this heading names, or None.

    BY CONTAINMENT, not equality. The body renders a contents title with an ordinal, in caps,
    or inside a longer phrase; requiring equality reached 30 of the CAP's 72 entries, and
    containment reaches 65. The longer string must contain the shorter, so "Implement
    Community Choice Aggregation" matches its ACTION heading while "Vision for Implementing
    Community Choice Aggregation" -- a different heading the contents does not name --
    does not.
    """
    h = norm_heading(text)
    if not h:
        return None
    best = None
    for e in toc:
        k = norm_heading(e.get("title"))
        if not k:
            continue
        if k == h:
            return e
        if (k in h or h in k) and (best is None
                                   or len(norm_heading(best.get("title"))) < len(k)):
            best = e
    return best


def heading_level(text: str | None, toc: list[dict], deepest: int) -> int | None:
    """The level this block should render at, or None when it is not a heading.

    A heading the contents names takes its level. One it does not name is a sub-heading --
    "Party Responsible for Implementation" is real, and belongs beneath everything the
    contents declares.
    """
    if not is_heading_shaped(text):
        return None
    hit = match_toc(text, toc)
    return hit["level"] if hit else deepest + 1
