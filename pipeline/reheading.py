"""Fit a document's heading tree to the two levels the store actually has.

WHY THIS IS NOT A LOSS OF STRUCTURE, WHICH IS THE FIRST THING ANYONE WILL ASK. `canonical.py`
understands exactly two: `# ` makes a `title` unit that does NOT start a section, and `## `
makes a `heading` unit that does. Anything deeper falls through to `para` WITH THE HASH MARKS
STILL IN THE TEXT -- on cap-2020, 120 units were headings stored as paragraphs and "###"
appeared 120 times inside the canonical text, so `### Public Engagement` would have been
stored as a claim-citable paragraph reading exactly that.

The CAP -> Strategy -> Action hierarchy does not live in these headings and never did. It
lives in `frameworks` / `framework_categories` / `subject_framework_categories`, where an
Action is a subject with subject_kind='initiative' linked to one or -- the case a tree
forbids -- two strategies. schema/claim_store.sql names the example: "CAP-2020 defines TWO
levels: Strategy, then named Actions beneath it ('Implement Community Choice Aggregation')."

Flattening here is what makes that link ATTACHABLE. A claim cites a section; with the 44
Actions buried inside seven 1,700-3,700-word Strategy sections there is nothing at the Action
level to cite. Promoting them gives each Action its own section, whose heading is the
Action's name -- which is what a heading-to-subject rule matches on.

WHAT IS DELIBERATELY NOT DONE HERE: no word of the document is touched. Only the markup that
precedes a heading changes, and `main()` refuses to write unless the prose is byte-identical.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_ATX = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")
_FIG_OPEN = re.compile(r"^\s*<figure_description")
_FIG_CLOSE = re.compile(r"</figure_description>")

# Level 4 and deeper are FIELD LABELS, not sections. On the CAP these are the eight fields
# every Action carries -- Vision, Party Responsible, Collaborators, Assumptions, Timeline,
# Equity Impacts, Indicators of Success, Target Demographic -- and the hand-prepared wiki
# file rendered them as bold labels before a later pass turned them into headings.
_LABEL_FROM = 4


def _bold(text: str) -> str:
    """A field label. Asterisks inside the text are escaped so the emphasis still closes."""
    return f"**{text.replace('*', chr(92) + '*')}**"


def reheading(md: str) -> tuple[str, dict]:
    """(rewritten markdown, counts).

    Level 1 and 3 become level 2, because only level 2 starts a section. Level 4+ become bold
    labels. The FIRST prose block -- the one before any heading -- is promoted to the single
    level 1, which is the document title and the only thing `# ` should ever be.
    """
    lines = md.splitlines()
    out: list[str] = []
    counts = {"title_promoted": 0, "to_section": 0, "to_label": 0, "unchanged": 0}
    in_figure = False
    seen_heading = False
    titled = False

    for raw in lines:
        if _FIG_OPEN.match(raw):
            in_figure = True
        if in_figure:
            out.append(raw)
            if _FIG_CLOSE.search(raw):
                in_figure = False
            continue

        s = raw.strip()
        m = _ATX.match(s) if s.startswith("#") else None

        if m is None:
            # THE TITLE IS THE FIRST PROSE, and only before any heading has appeared. On the
            # CAP that is "ANN ARBOR'S LIVING CARBON NEUTRALITY PLAN APRIL 2020", which the
            # source file left as an ordinary paragraph.
            if (not titled and not seen_heading and s
                    and not s.startswith(("<!--", ">", "|", "-", "[", "<"))):
                out.append(f"# {s}")
                counts["title_promoted"] += 1
                titled = True
                continue
            out.append(raw)
            continue

        seen_heading = True
        level, text = len(m.group("hashes")), m.group("text")
        if level >= _LABEL_FROM:
            out.append(_bold(text))
            counts["to_label"] += 1
        elif level == 2:
            out.append(f"## {text}")
            counts["unchanged"] += 1
        else:
            out.append(f"## {text}")
            counts["to_section"] += 1

    return "\n".join(out) + ("\n" if md.endswith("\n") else ""), counts


def prose_words(md: str) -> list[str]:
    """Every word of the document with heading and label MARKUP removed.

    The equality check main() refuses to write without. Strips `#` runs and the `**` a label
    is wrapped in, so a heading that merely changed level compares equal, while a word that
    moved, vanished or appeared does not.
    """
    body = re.sub(r"^#{1,6}\s+", "", md, flags=re.M)
    body = body.replace("**", "")
    return body.split()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", required=True)
    ap.add_argument("--out", help="defaults to rewriting --md in place")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src = Path(a.md).read_text()
    new, counts = reheading(src)

    # THE GUARD, NOT A TEST. The whole justification for using a hand-prepared file is that a
    # human validated its text; a reheading pass that alters a word destroys that, and it
    # would do so invisibly.
    before, after = prose_words(src), prose_words(new)
    if before != after:
        d = next((i for i, (x, y) in enumerate(zip(before, after)) if x != y), len(after))
        raise SystemExit(f"[reheading] REFUSING TO WRITE: prose changed at word {d}: "
                         f"{before[d:d+6]} -> {after[d:d+6]}")

    print(f"[reheading] title promoted: {counts['title_promoted']}  "
          f"-> ##: {counts['to_section']}  -> **label**: {counts['to_label']}  "
          f"already ##: {counts['unchanged']}")
    print(f"[reheading] prose verified identical ({len(before):,} words)")
    if a.dry_run:
        print("[reheading] --dry-run: nothing written")
        return 0
    Path(a.out or a.md).write_text(new)
    print(f"[reheading] wrote {a.out or a.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
