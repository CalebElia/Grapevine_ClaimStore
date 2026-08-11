"""Rank meetings by how much they discuss a topic, from the MEETING RECORD.

Supersedes ranking by `event_items` titles. An agenda title is a heading written before
the meeting; minutes are the narrative agreed after it. "Bryant" appearing in a minutes
paragraph means it was discussed; appearing in an agenda title means it was scheduled.

TWO NUMBERS PER MEETING, and they answer different questions:
  breadth  how many of the topic terms appear at all — is this meeting ABOUT the subject
  depth    total mentions per 1,000 words — how much of the meeting it occupied

Depth is normalised because a Council packet runs 30,000 words and a commission minutes
file 3,000; raw counts would rank by document length.

EVERY ROW CARRIES ITS BASIS (minutes | agenda | none). Minutes coverage is 88% for
Council and 22% for the Energy and Environmental Commissions, so a table that silently
mixed them would rank bodies by their filing habits and look like a finding.

Usage:
    python -m pipeline.topic_scan --body "Sustainability Commission"
    python -m pipeline.topic_scan --body "City Council" --top 12
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).parent.parent

# The Bryant neighbourhood decarbonization argument. Patterns, not bare strings, so
# 'geothermal'/'geo-thermal' and 'electrify'/'electrification' both land — and so that
# 'SEU' does not match inside 'museum', which a naive substring search does.
# ACRONYMS MUST MATCH CASE-SENSITIVELY. Matched case-insensitively, `\bCAN\b` hits the
# ordinary verb "can" — which reported 88 Community Action Network mentions in a
# transcript that contains none. `SEU` and `DTE` are safer but get the same treatment for
# the same reason. Listed separately so the distinction is enforced, not remembered.
CASE_SENSITIVE = {"Community Action Network", "SEU", "DTE", "A2Zero"}

TOPICS: dict[str, str] = {
    "Bryant":            r"\bbryant\b",
    "geothermal":        r"\bgeo-?thermal\b",
    "SEU":               r"\bSEU\b|[Ss]ustainable [Ee]nergy [Uu]tility",
    "DTE":               r"\bDTE\b",
    "weatherization":    r"\bweatheriz",
    # SPELLED OUT ONLY. `\bCAN\b` is unusable here even case-sensitively: Council minutes
    # carry an ALL-CAPS Zoom boilerplate header on every page ("PHONE CALLERS CAN PRESS *9",
    # "ACCOMMODATIONS CAN BE MADE"), which produced 570 false hits across the corpus and made
    # CAN the top-ranked term. An acronym that is also a common English word cannot be
    # matched by shape; it needs the expansion.
    "Community Action Network": r"[Cc]ommunity [Aa]ction [Nn]etwork",
    "electrification":   r"\belectrif",
    "heat pump":         r"heat ?pumps?\b",
    "decarbonization":   r"\bdecarboni",
    "A2Zero":            r"\bA2\s?[Zz]ero\b|\bA2Z\b",
    "franchise":         r"\bfranchise",
    "neighborhood scale": r"neighbou?rhood[- ]scale",
}


def score(text: str) -> dict:
    counts = {}
    for name, pat in TOPICS.items():
        flags = 0 if name in CASE_SENSITIVE else re.I
        n = len(re.findall(pat, text, flags))
        if n:
            counts[name] = n
    words = max(len(text.split()), 1)
    total = sum(counts.values())
    return {"counts": counts, "breadth": len(counts), "mentions": total,
            "words": words, "density": round(total / words * 1000, 2)}


def scan(body: str, dsn: str | None = None, limit: int | None = None) -> list[dict]:
    from pipeline.fetch_documents import rows_for, fetch
    rows = rows_for(body, dsn or __import__("pipeline.fetch_documents",
                                            fromlist=["DSN"]).DSN, limit)
    out = []
    for r in rows:
        if not r["url"]:
            out.append({**r, "breadth": 0, "mentions": 0, "density": 0.0,
                        "counts": {}, "words": 0})
            continue
        d = fetch(r["url"], r["format"], r["basis"])
        s = score(d.get("text", ""))
        out.append({**r, **s})
    return out


def report(rows: list[dict], body: str, top: int = 12) -> None:
    have = [r for r in rows if r["words"] > 100]
    print(f"\n{body} — {len(rows)} events, {len(have)} with usable text "
          f"({sum(1 for r in have if r['basis']=='minutes')} minutes, "
          f"{sum(1 for r in have if r['basis']=='agenda')} agenda)")
    on = [r for r in have if r["breadth"] > 0]
    print(f"  {len(on)}/{len(have)} mention at least one topic "
          f"({len(on)/len(have)*100:.0f}%)" if have else "  no text")
    print(f"\n  {'date':<12}{'basis':<9}{'breadth':>8}{'ments':>7}{'per 1k':>8}  topics")
    print("  " + "-" * 92)
    for r in sorted(have, key=lambda x: (-x["breadth"], -x["density"]))[:top]:
        which = ", ".join(sorted(r["counts"], key=lambda k: -r["counts"][k])[:5])
        print(f"  {str(r['date']):<12}{r['basis']:<9}{r['breadth']:>8}"
              f"{r['mentions']:>7}{r['density']:>8.2f}  {which[:56]}")
    agg: dict[str, int] = {}
    for r in have:
        for k, v in r["counts"].items():
            agg[k] = agg.get(k, 0) + v
    print(f"\n  corpus-wide term totals:")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<26}{v:>6}")


def main() -> int:
    ap = argparse.ArgumentParser(description="rank meetings by topic, from the record")
    ap.add_argument("--body", required=True)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    report(scan(a.body, limit=a.limit), a.body, a.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
