"""Backfill host metadata for every media asset, and audit meeting↔video linkage.

WHY. Two linkage defects were found by hand while picking a starting corpus: a video
attached to the wrong meeting by Legistar's own calendar, and four uploads on one event
of which three were aborted streams lasting under a minute. Both were caught because
somebody looked. This does the same check for all 261 videos, mechanically.

The evidence is the host's own title. CTN names each recording after the meeting it
records, so a title date that disagrees with the event date is a linkage defect — that is
exactly how IkZ4APPWNgY was caught sitting on 2026-05-12 while titled "4/14/26".

TWO TITLE FORMATS, and missing the second one is what caused the defect in the first
place. CTN publishes both `M/D/YY` and `- Month D, YYYY`; the original discovery pass
recognised only the former, so the real 2026-05-12 recording was never ingested while a
wrong video occupied its row.

`duration_seconds` matters beyond dedup: with it empty there is no way to estimate the
review hours a candidate corpus would cost, which is the number a verification budget is
built from.

Nothing is deleted and no link is rewritten. Mismatches are REPORTED for a human, because
a title is strong evidence and not proof — a clerk can mistype a title too.

READ `date_verification` NARROWLY: it answers "does the host's title agree with the meeting
date on this row", and nothing else. In particular it does NOT mean the video is fetchable.
Five 2020 Council videos are private or removed, yet their stored titles parse and agree, so
they read 'matched'. The availability signal is `duration_seconds IS NULL` — a probe that
could not reach the video never writes a duration. Anything that plans transcription work
should filter on duration, not on date_verification.

Usage:
    python -m pipeline.backfill_media --body "Sustainability Commission"
    python -m pipeline.backfill_media --all
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess

MONTHS = ("january february march april may june july august september october "
          "november december").split()
# THREE FORMATS, and each one was found only after a defect. CTN has renamed its convention
# at least twice; a parser that knows one form silently marks the others unverifiable, and an
# unverifiable video is where a mislink hides. Missing the second form is what let a wrong
# video sit on 2026-05-12 while the real recording was never ingested at all.
#   "1/13/26"  "1/13/2026"
SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
#   "May 12, 2026"  "- May 12 2026"
WORDY = re.compile(r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
                   re.I)
#   "1-6-2020"  "2-3-20"  "8-11-20"  — the 2020-era titles, 34 of them
HYPHEN = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b")


def title_date(title: str) -> dt.date | None:
    if not title:
        return None
    for pat in (SLASH, HYPHEN):
        if m := pat.search(title):
            mo, d, y = (int(x) for x in m.groups())
            y += 2000 if y < 100 else 0
            try:
                return dt.date(y, mo, d)
            except ValueError:
                return None
    if m := WORDY.search(title):
        mo = MONTHS.index(m.group(1).lower()) + 1
        try:
            return dt.date(int(m.group(3)), mo, int(m.group(2)))
        except ValueError:
            return None
    return None


def probe(external_id: str) -> dict | None:
    """One yt-dlp metadata call. Returns None when the video is gone or private."""
    try:
        out = subprocess.run(
            ["yt-dlp", "--skip-download", "--no-warnings", "--quiet",
             "--print", "%(duration)s\t%(upload_date)s\t%(title)s",
             f"https://www.youtube.com/watch?v={external_id}"],
            capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return None
    line = (out.stdout or "").strip().splitlines()
    if out.returncode != 0 or not line:
        return None
    parts = line[-1].split("\t")
    if len(parts) < 3:
        return None
    dur, up, title = parts[0], parts[1], "\t".join(parts[2:])
    up_d = None
    if re.fullmatch(r"\d{8}", up):
        up_d = dt.date(int(up[:4]), int(up[4:6]), int(up[6:]))
    return {"duration": int(dur) if dur.isdigit() else None,
            "upload": up_d, "title": title}


def verdict(td: dt.date | None, ed: dt.date, up: dt.date | None) -> str:
    if td is None:
        return "no_title_date"
    if td != ed:
        return "title_mismatch"
    # Uploaded before the meeting it claims to record: the title agrees but the
    # chronology cannot. Flagged separately so it is never read as clean.
    if up and up < ed:
        return "upload_implausible"
    return "matched"


def reparse(dsn: str) -> None:
    """Re-derive title dates from titles ALREADY stored — no network, no re-probe.

    Exists because the title-format list grows: each new CTN naming convention is found
    only after it has hidden something. Re-probing 261 videos to apply a new regex would
    be wasteful and rate-limited, and the titles are already in the database.
    """
    import psycopg
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT m.id, m.external_id, e.event_date, b.name, m.host_title, "
            "m.host_upload_date, m.date_verification FROM media_assets m "
            "JOIN events e ON e.id = m.event_id JOIN bodies b ON b.id = e.body_id "
            "WHERE m.host_title IS NOT NULL ORDER BY e.event_date").fetchall()
        changed, stats, mism = 0, {}, []
        for mid, ext, ed, bname, title, up, was in rows:
            td = title_date(title)
            v = verdict(td, ed, up)
            stats[v] = stats.get(v, 0) + 1
            if v != was:
                changed += 1
                c.execute("UPDATE media_assets SET title_stated_date=%s, "
                          "date_verification=%s WHERE id=%s", (td, v, mid))
            if v in ("title_mismatch", "upload_implausible"):
                mism.append((ed, bname, ext, td, title[:56]))
        c.commit()
    print(f"[media] reparsed {len(rows)} titles, {changed} changed")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<20}{v:>5}")
    if mism:
        print(f"\nLINKAGE DEFECTS — title disagrees with the meeting it is on ({len(mism)}):")
        for ed, b, ext, td, t in sorted(mism, key=lambda r: str(r[0])):
            print(f"  {str(ed):<12}{b[:24]:<25}{ext:<13}title says {str(td or '—'):<12}{t}")


def run(body: str | None, dsn: str, limit: int | None = None) -> None:
    import psycopg
    with psycopg.connect(dsn) as c:
        q = ("SELECT m.id, m.external_id, e.event_date, b.name, m.host_title "
             "FROM media_assets m JOIN events e ON e.id = m.event_id "
             "JOIN bodies b ON b.id = e.body_id WHERE m.external_id IS NOT NULL "
             + ("AND b.name ILIKE %s " if body else "")
             + "ORDER BY e.event_date DESC")
        rows = c.execute(q, (f"%{body}%",) if body else ()).fetchall()
        rows = rows[:limit] if limit else rows
        print(f"[media] probing {len(rows)} videos", flush=True)

        stats: dict[str, int] = {}
        gone: list[tuple] = []
        mism: list[tuple] = []
        for i, (mid, ext, ed, bname, old_title) in enumerate(rows, 1):
            info = probe(ext)
            if not info:
                gone.append((ed, bname, ext))
                stats["unavailable"] = stats.get("unavailable", 0) + 1
            else:
                td = title_date(info["title"])
                v = verdict(td, ed, info["upload"])
                stats[v] = stats.get(v, 0) + 1
                c.execute(
                    "UPDATE media_assets SET duration_seconds=%s, host_upload_date=%s, "
                    "host_title=%s, title_stated_date=%s, date_verification=%s WHERE id=%s",
                    (info["duration"], info["upload"], info["title"], td, v, mid))
                if v in ("title_mismatch", "upload_implausible"):
                    mism.append((ed, bname, ext, td, info["title"][:58], v))
            if i % 25 == 0 or i == len(rows):
                c.commit()
                print(f"  {i}/{len(rows)}  " +
                      "  ".join(f"{k}={v}" for k, v in sorted(stats.items())), flush=True)
        c.commit()

    print(f"\n[media] {sum(stats.values())} probed")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<20}{v:>5}")
    if mism:
        print(f"\nLINKAGE DEFECTS — video title disagrees with the meeting it is on ({len(mism)}):")
        print(f"  {'event date':<12}{'body':<26}{'video':<14}{'title says':<12} title")
        for ed, b, ext, td, t, v in sorted(mism, key=lambda r: str(r[0])):
            print(f"  {str(ed):<12}{b[:25]:<26}{ext:<14}{str(td or '—'):<12}{t}")
    if gone:
        print(f"\nUNAVAILABLE (private, removed, or region-blocked) ({len(gone)}):")
        for ed, b, ext in sorted(gone, key=lambda r: str(r[0]))[:20]:
            print(f"  {str(ed):<12}{b[:25]:<26}{ext}")


def main() -> int:
    from pipeline.fetch_documents import DSN
    ap = argparse.ArgumentParser(description="backfill media metadata + audit linkage")
    ap.add_argument("--body")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--reparse", action="store_true",
                    help="re-derive title dates from stored titles; no network")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    if a.reparse:
        reparse(a.dsn)
        return 0
    if not a.body and not a.all:
        ap.error("pass --body NAME, --all, or --reparse")
    run(a.body, a.dsn, a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
