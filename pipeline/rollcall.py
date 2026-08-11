"""Extract attendance from meeting minutes — the strongest presence evidence we have.

WHY THIS IS THE VALUABLE PART OF MINUTES. These files are useless for topic ranking: the
Sustainability Commission's 3/10/2026 minutes are 498 words against an 18,045-word
transcript, with zero mentions of Bryant, geothermal or heat pumps in a meeting that spent
39 minutes on them. What they DO carry is a roll call naming everyone present and absent.

`naming.py:load_present()` currently approximates presence from body membership and agenda
mentions — who COULD have been there. Roll call says who WAS. That distinction is the
presence-vs-existence gate: a `persons` row proves a name is real, not that its owner spoke.

THE PARSE IS SELF-VALIDATING, and it has to be. Legistar renders roll call as HTML table
cells whose label order is not consistent:

    13 -  Present  :  <13 names>          label BEFORE names
     3 -  <3 names>   Absent   :          label AFTER names

A greedy `Present:(.*?)Absent` therefore swallows the absent list into the present list —
measured, not hypothesised: it reported 15 present / 0 absent for a meeting with 13 and 3.
Marking an absent person present is precisely the error that invents attributions.

So every block is anchored on its DECLARED COUNT (`13 -`), and a block whose extracted name
count disagrees with that number is returned as `unreliable` rather than as data. The
document checks our arithmetic against itself.

Usage:
    python -m pipeline.rollcall --body "Sustainability Commission"
"""
from __future__ import annotations

import argparse
import re

COUNT = re.compile(r"\b(\d{1,2})\s*-\s*")
LABEL = re.compile(r"\b(Present|Absent|Excused)\b", re.I)


def _names(seg: str) -> list[str]:
    seg = LABEL.sub("", seg).replace(":", " ")
    seg = re.sub(r"\band\b", ",", seg)
    seg = re.sub(r"\s+", " ", seg)
    out = []
    for n in seg.split(","):
        n = n.strip(" .,;")
        # Two tokens minimum: a roll call lists full names, and single tokens here are
        # fragments of the surrounding table, not people.
        if len(n) > 3 and len(n.split()) >= 2 and not n.lower().startswith("roll"):
            out.append(n)
    return out


def parse(text: str) -> dict:
    """Return {present, absent, reliable, reason}. `reliable` gates all downstream use."""
    flat = re.sub(r"\s+", " ", text)
    m = re.search(r"ROLL CALL\s*(.*?)\s*(?:APPROVAL OF|PUBLIC COMMENT|CONSENT)", flat, re.I)
    if not m:
        return {"present": [], "absent": [], "reliable": False, "reason": "no ROLL CALL block"}
    body = m.group(1)

    # Legistar emits at least three layouts of the same roll call, because the HTML table's
    # column order leaks into the extracted text:
    #     A   Present: 13 - <names>   Absent: 3 - <names>
    #     B   13 - Present : <names>  3 - <names> Absent :
    #     C   10 - 5 - Present : <names>  Absent : <names>      (both counts hoisted first)
    # No positional rule survives all three. What DOES hold in every one is that the labels,
    # the counts, and the name-runs appear in the SAME ORDER. So parse the three sequences
    # independently and zip them, instead of trying to infer the layout.
    counts = [int(c) for c in COUNT.findall(body)]
    labels = [l.lower() for l in LABEL.findall(body)]
    runs = [n for n in (_names(chunk) for chunk in
                        LABEL.sub("|", COUNT.sub("|", body)).split("|")) if n]

    if not (len(counts) == len(labels) == len(runs)):
        return {"present": [], "absent": [], "reliable": False,
                "reason": f"{len(counts)} counts, {len(labels)} labels, {len(runs)} name runs"}

    got: dict[str, list[str]] = {}
    bad: list[str] = []
    for declared, kind, names in zip(counts, labels, runs):
        # THE CHECKSUM. The clerk wrote how many people were in this block; if our split
        # disagrees, the parse is wrong and must not be used as presence evidence.
        if len(names) != declared:
            bad.append(f"{kind}: declared {declared}, extracted {len(names)}")
            continue
        got.setdefault("absent" if kind in ("absent", "excused") else "present", []).extend(names)

    if not got.get("present"):
        return {"present": [], "absent": got.get("absent", []), "reliable": False,
                "reason": "; ".join(bad) or "no counted present block"}
    return {"present": got["present"], "absent": got.get("absent", []),
            "reliable": not bad, "reason": "; ".join(bad)}


def run(body: str, dsn: str) -> list[dict]:
    from pipeline.fetch_documents import rows_for, fetch
    out = []
    for r in sorted((x for x in rows_for(body, dsn) if x["url"]), key=lambda x: str(x["date"])):
        d = fetch(r["url"], r["format"], r["basis"])
        out.append({"date": r["date"], "basis": r["basis"], **parse(d.get("text", ""))})
    ok = [r for r in out if r["reliable"]]
    print(f"\n{body}: roll call verified for {len(ok)}/{len(out)} meetings "
          f"(checksum-matched; the rest are reported, not used)\n")
    print(f"  {'date':<12}{'ok':>4}{'pres':>6}{'abs':>5}   names / why not")
    print("  " + "-" * 84)
    for r in out:
        mark = "yes" if r["reliable"] else "NO"
        detail = ", ".join(r["present"][:3]) if r["reliable"] else r["reason"][:52]
        print(f"  {str(r['date']):<12}{mark:>4}{len(r['present']):>6}{len(r['absent']):>5}"
              f"   {detail[:52]}")
    seen: dict[str, int] = {}
    for r in ok:
        for n in r["present"]:
            seen[n] = seen.get(n, 0) + 1
    print(f"\n  attendance across {len(ok)} verified meetings:")
    for n, c in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"    {c:>3}/{len(ok)}  {n}")
    return out


def main() -> int:
    from pipeline.fetch_documents import DSN
    ap = argparse.ArgumentParser(description="extract verified attendance from minutes")
    ap.add_argument("--body", required=True)
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    run(a.body, a.dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
