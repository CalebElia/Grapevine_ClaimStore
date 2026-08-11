"""Does ranking on MINUTES actually reorder anything versus AGENDA TITLES?

The first corpus ranking scored `event_items` — Legistar agenda item titles. The objection
was that minutes are the agreed post-hoc record and therefore the better instrument. That
objection is sound in principle. This module tests whether it is true in THIS corpus.

It matters because Legistar minutes for City Council are largely the agenda titles
reproduced verbatim, each followed by an enactment number and a disposition. If the two
rank meetings identically, switching basis buys nothing and the extra fetch is cost with
no signal. If they diverge, the minutes-based ranking supersedes.

Reports Spearman rank correlation plus the meetings each basis finds ALONE, since a
correlation of 0.9 can still hide the handful of meetings you would actually watch.

Usage:
    python -m pipeline.basis_compare --body "City Council"
"""
from __future__ import annotations

import argparse

from pipeline.topic_scan import score


def agenda_scores(body: str, dsn: str) -> dict[int, dict]:
    """Score each meeting on the concatenated text of its agenda item titles."""
    import psycopg
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT e.id, e.event_date, string_agg(coalesce(i.title,''), ' ') "
            "FROM events e JOIN bodies b ON b.id = e.body_id "
            "LEFT JOIN event_items i ON i.event_id = e.id "
            "WHERE b.name ILIKE %s GROUP BY e.id, e.event_date", (f"%{body}%",)).fetchall()
    return {eid: {"date": d, **score(txt or "")} for eid, d, txt in rows}


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation without scipy — ties get averaged ranks."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: -v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    if n < 2:
        return float("nan")
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else float("nan")


def run(body: str, dsn: str, top: int = 10) -> None:
    from pipeline.fetch_documents import rows_for, fetch
    ag = agenda_scores(body, dsn)
    doc: dict[int, dict] = {}
    for r in rows_for(body, dsn):
        if r["url"] and r["basis"] == "minutes":
            d = fetch(r["url"], r["format"], r["basis"])
            if d.get("chars", 0) > 200:
                doc[r["event_id"]] = {"date": r["date"], **score(d["text"])}

    both = [e for e in doc if e in ag]
    print(f"\n{body}: {len(both)} meetings scored on BOTH agenda titles and minutes text")
    if not both:
        return
    a = [ag[e]["mentions"] for e in both]
    m = [doc[e]["mentions"] for e in both]
    print(f"  total topic mentions   agenda titles {sum(a):>6}   minutes {sum(m):>6}")
    print(f"  meetings on topic      agenda titles "
          f"{sum(1 for x in a if x):>6}   minutes {sum(1 for x in m if x):>6}")
    print(f"  Spearman rank correlation between the two rankings: {spearman(a, m):.3f}")

    only_m = sorted((e for e in both if doc[e]["mentions"] and not ag[e]["mentions"]),
                    key=lambda e: -doc[e]["mentions"])
    only_a = sorted((e for e in both if ag[e]["mentions"] and not doc[e]["mentions"]),
                    key=lambda e: -ag[e]["mentions"])
    print(f"\n  found ONLY by minutes ({len(only_m)}):")
    for e in only_m[:top]:
        print(f"    {doc[e]['date']}  {doc[e]['mentions']:>3} mentions  "
              f"{', '.join(doc[e]['counts'])[:52]}")
    print(f"\n  found ONLY by agenda titles ({len(only_a)}):")
    for e in only_a[:top]:
        print(f"    {ag[e]['date']}  {ag[e]['mentions']:>3} mentions  "
              f"{', '.join(ag[e]['counts'])[:52]}")


def main() -> int:
    from pipeline.fetch_documents import DSN
    ap = argparse.ArgumentParser(description="compare agenda-title vs minutes ranking")
    ap.add_argument("--body", required=True)
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    run(a.body, a.dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
