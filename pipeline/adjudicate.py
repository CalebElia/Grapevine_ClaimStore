"""Decide cross-arm conflicts from glyph geometry, before spending a model on them.

WHY GEOMETRY IS THE THIRD READ. Two arms disagreeing tells you something is wrong and not
which arm is wrong. The obvious tiebreaker -- the PDF's own text layer -- turns out to be
lossy in exactly the way that produces these conflicts: on AA_AnnualReport_2023.pdf page 12
the text layer contains "Actgrant" with no space CHARACTER, because the space on the page is
glyph positioning. pdfplumber reproduces that faithfully. An OCR arm reading pixels sees the
gap and writes "Act grant".

The gap is measurable and it is not subtle. Inside that token the t->g gap is 2.16pt while
every other gap is 0.00pt, and 2.16 is the largest gap anywhere on the line. Geometry cannot
be lossy about spacing the way a character stream can, because it IS the spacing.

WHAT THIS BUYS. Of eight cross-arm conflicts across Years 1, 3, 4 and 5, seven are decidable
this way with no model and no human. That is the point: a semantic pass should run on the
residue that geometry cannot settle, not on everything two arms happened to disagree about.

IT NEVER GUESSES. Three outcomes, and "unresolved" is a real one:
  * geometry splits the token AND the second arm produced those same words -> second arm
  * geometry shows one word -> the primary
  * anything else, including no glyph run to measure -> unresolved, escalate
Year 2 has no text layer at all, so nothing here can be located and everything escalates.
Absence of evidence is never scored as evidence for the incumbent.
"""
from __future__ import annotations

import statistics

WINNER_PRIMARY = "primary"
WINNER_SECOND = "second_read"
WINNER_UNRESOLVED = "unresolved"

# A gap this fraction of the font size is a word break. Calibrated against the measured
# case: 2.16pt at 9pt type is 0.24em and is a space; the same 2.16pt in 40pt display type
# is 0.05em and is kerning.
_SPACE_EM = 0.15

# ...AND it must stand out from its own token by this factor. Display and small-caps type is
# often tracked wide enough that every gap clears the absolute floor; without this, one
# wide-set word shatters into its letters. Median rather than mean so that the outlier we
# are hunting does not raise its own bar.
_STANDOUT = 2.0

# BELOW this fraction of the font size a gap is kerning and nothing more. Between _TIGHT_EM
# and _SPACE_EM is the band where geometry genuinely does not know: measured on Year 3's
# "Careprogram", the Care->program gap is 0.88pt at 11pt type (0.08em) where every other
# pair in the token is 0.00pt and a real space in the same document at the same size runs
# 2.16-2.34pt. That is plainly not kerning and plainly not a full space. Resolving it by
# defaulting to the incumbent arm would be a confident answer with no evidence behind it,
# which is the failure mode this whole layer exists to avoid. It escalates instead.
_TIGHT_EM = 0.03


def split_points(gaps: list[float], size: float) -> list[int]:
    """Character indices at which a new word begins, from inter-glyph gaps.

    `gaps[i]` is the space between character i and character i+1, so a break at gaps[i]
    means the next word starts at index i+1.
    """
    if size <= 0:
        return []
    if not gaps:
        return []
    floor = max(_SPACE_EM * size, _STANDOUT * statistics.median(gaps))
    return [i + 1 for i, g in enumerate(gaps) if g >= floor]


def ambiguous_points(gaps: list[float], size: float) -> list[int]:
    """Gaps too wide to be kerning and too narrow to be a space. Geometry abstains."""
    if not gaps or size <= 0:
        return []
    space_floor = max(_SPACE_EM * size, _STANDOUT * statistics.median(gaps))
    return [i + 1 for i, g in enumerate(gaps)
            if _TIGHT_EM * size < g < space_floor]


def token_verdict(token: str, splits: list[str] | None,
                  second_read_words: set[str],
                  ambiguous: bool = False) -> tuple[str, str]:
    """Who read `token` correctly: the primary arm, the second arm, or nobody yet."""
    if splits is None:
        return WINNER_UNRESOLVED, "no glyph run located for this token; nothing to measure"
    if ambiguous:
        return (WINNER_UNRESOLVED,
                f"an internal gap in {token!r} is ambiguous: too wide for kerning, too "
                f"narrow for a space. Geometry abstains; this needs a semantic read")
    if len(splits) <= 1:
        return (WINNER_PRIMARY,
                f"glyph gaps show {token!r} is one word; the second read disagreed")
    joined = " ".join(splits)
    if all(p in second_read_words for p in splits):
        return (WINNER_SECOND,
                f"glyph gaps split {token!r} into {joined!r}, which the second read produced")
    return (WINNER_UNRESOLVED,
            f"glyph gaps split {token!r} into {joined!r}, but the second read has neither")


def find_token_gaps(pdf_path, token: str) -> tuple[list[float], float] | None:
    """Inter-glyph gaps inside `token` where it appears in the PDF, with its font size.

    Returns None when the token cannot be located -- which is the normal case for an
    OCR-only document, whose glyphs carry no text layer to search. None means "no
    measurement", never "no gap".

    THE RUN IS REBUILT FROM CHARACTERS, NOT FROM extract_words(). extract_words applies
    pdfplumber's own spacing heuristic, which is the very thing under examination here: it
    is what returned "Actgrant" as a single word. Reading the chars directly asks the page
    where its glyphs actually are.
    """
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            if token not in (page.extract_text() or ""):
                continue
            # Group by text line, then look for the token's characters in reading order.
            lines: dict = {}
            for ch in page.chars:
                lines.setdefault(round(ch["top"]), []).append(ch)
            for _, chars in lines.items():
                chars.sort(key=lambda c: c["x0"])
                text = "".join(c["text"] for c in chars)
                i = text.find(token)
                if i < 0:
                    continue
                run = chars[i:i + len(token)]
                gaps = [b["x0"] - a["x1"] for a, b in zip(run, run[1:])]
                size = max((c.get("size") or 0.0) for c in run)
                return gaps, size
    return None


def split_token(token: str, points: list[int]) -> list[str]:
    """Apply split points to a token, lowercased for comparison against a word set."""
    out, prev = [], 0
    for p in points:
        out.append(token[prev:p])
        prev = p
    out.append(token[prev:])
    return [x.lower() for x in out if x]


DSN = __import__("os").environ.get(
    "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine")


def adjudicate_document(document_id: int, pdf_path, second_md_path,
                        dsn: str = DSN) -> list[dict]:
    """Decide every stored ocr_conflicts item for one document.

    Conflicts are stored lowercased (they come from a word set), while the PDF spells the
    token as it renders it -- "Actgrant", not "actgrant". So each conflict is matched back
    to the document's own casing before the glyph run is sought.
    """
    import re

    import psycopg

    second_words = set(re.findall(r"[a-z0-9$%]+",
                                  open(second_md_path).read().lower()))
    out: list[dict] = []
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT id, sequence, parse_flags->'ocr_conflicts' FROM document_sections "
            "WHERE document_id = %s AND parse_flags ? 'ocr_conflicts' ORDER BY sequence",
            (document_id,)).fetchall()

    for sid, seq, conflicts in rows:
        for raw in (conflicts or []):
            token = str(raw)
            # A figure conflict is recorded as "kind:value" and is not a spacing question.
            if ":" in token:
                out.append({"section_id": sid, "sequence": seq, "token": token,
                            "winner": WINNER_UNRESOLVED,
                            "why": "figure conflict; geometry does not adjudicate values"})
                continue
            found = _locate_any_case(pdf_path, token)
            if found is None:
                out.append({"section_id": sid, "sequence": seq, "token": token,
                            "winner": WINNER_UNRESOLVED,
                            "why": "no glyph run located for this token; nothing to measure"})
                continue
            cased, gaps, size = found
            parts = split_token(cased, split_points(gaps, size))
            w, why = token_verdict(token, parts, second_words,
                                   ambiguous=bool(ambiguous_points(gaps, size)))
            out.append({"section_id": sid, "sequence": seq, "token": token,
                        "winner": w, "why": why,
                        "reads_as": " ".join(parts) if len(parts) > 1 else cased})
    return out


def _locate_any_case(pdf_path, token: str):
    """Find the token in the PDF whatever case it renders in, and measure it."""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            i = text.lower().find(token)
            if i < 0:
                continue
            cased = text[i:i + len(token)]
            got = find_token_gaps(pdf_path, cased)
            if got:
                return cased, got[0], got[1]
    return None


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="decide cross-arm conflicts from glyph geometry")
    ap.add_argument("--document-id", type=int, required=True)
    ap.add_argument("--pdf", help="defaults to the document's recorded snapshot_path")
    ap.add_argument("--second", required=True)
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()

    pdf = a.pdf
    if not pdf:
        import psycopg
        with psycopg.connect(a.dsn) as c:
            row = c.execute("SELECT snapshot_path FROM documents WHERE id=%s",
                            (a.document_id,)).fetchone()
        pdf = row[0] if row else None
        if not pdf:
            raise SystemExit("[adjudicate] no --pdf and no snapshot_path on the document")

    rows = adjudicate_document(a.document_id, pdf, a.second, a.dsn)
    for r in rows:
        print(f"  seq {r['sequence']:>2}  {r['token']:<22} {r['winner']:<12}"
              f"{r.get('reads_as', '')}")
        if r["winner"] == WINNER_UNRESOLVED:
            print(f"        {r['why']}")
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["winner"]] = tally.get(r["winner"], 0) + 1
    print(f"[adjudicate] " + " · ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
