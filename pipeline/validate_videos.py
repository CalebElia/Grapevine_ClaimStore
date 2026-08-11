"""Check every stored video against the meeting it is linked from, and record the verdict.

Kept separate from the ingest deliberately. It hits YouTube once per video, which is
slower than the Legistar calls, and it is worth re-running on its own when a link
changes without re-running a whole ingest.

The check exists because Legistar's meeting-to-video association is demonstrably not
reliable: video IkZ4APPWNgY is attached to the Sustainability Commission's 2026-05-12
row, but its own title reads "…Meeting 4/14/26" and it was uploaded 2026-04-24. The
2026-04-14 row carries no video at all. One in ten videos on that body failed.

A `title_mismatch` sets `date_verification` and leaves the row in place — the link is
evidence of *something*, and deleting it would lose the trail. Downstream, S1/S2 must
refuse to transcribe against a meeting whose video failed the check, because the result
would carry a date that is wrong by weeks and nothing later can detect that.

Usage:
    python -m pipeline.validate_videos
    python -m pipeline.validate_videos --offline     # cached metadata only
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

import psycopg

from pipeline.video_validation import check

DSN = os.environ.get(
    "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine"
)
CACHE = Path(os.environ.get("GRAPEVINE_YT_CACHE", ".cache/youtube"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify stored videos belong to their meetings")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--recheck", action="store_true",
                    help="re-verify rows already carrying a verdict")
    args = ap.parse_args()

    verdicts: Counter[str] = Counter()
    mismatches: list[str] = []

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        where = "" if args.recheck else "AND (m.date_verification IS NULL OR m.date_verification='unverified')"
        rows = conn.execute(f"""
            SELECT m.id, m.external_id, e.event_date, b.name
            FROM media_assets m
            JOIN events e ON e.id = m.event_id
            JOIN bodies b ON b.id = e.body_id
            WHERE m.host = 'youtube' {where}
            ORDER BY e.event_date
        """).fetchall()

        for media_id, external_id, event_date, body_name in rows:
            res = check(external_id, event_date, cache_dir=CACHE, offline=args.offline)
            verdicts[res.verdict] += 1
            conn.execute(
                """UPDATE media_assets
                      SET host_title        = %s,
                          host_upload_date  = %s,
                          title_stated_date = %s,
                          date_verification = %s,
                          duration_seconds  = COALESCE(duration_seconds, %s)
                    WHERE id = %s""",
                (res.host_title, res.host_upload_date, res.title_stated_date,
                 res.verdict, None, media_id),
            )
            if res.blocks_extraction:
                mismatches.append(
                    f"{body_name} {event_date}: {external_id} — {res.detail}"
                )
        conn.commit()

    print(f"=== video verification ({len(rows)} checked) ===")
    for v, n in verdicts.most_common():
        print(f"  {v:<20} {n}")
    if mismatches:
        print()
        print("  *** LINKED TO THE WRONG MEETING — do not transcribe against these ***")
        for m in mismatches:
            print(f"     {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
