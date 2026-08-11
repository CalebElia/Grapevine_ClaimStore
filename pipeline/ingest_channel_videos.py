"""Match CTN channel videos to meetings, recovering what Legistar never linked.

Run after the Legistar ingest. Reports four outcomes and writes only the first:

  recovered   meeting had no video; the channel supplies one     -> INSERT
  corroborated Legistar and the channel agree on the same video  -> mark as such
  CONFLICT    they name DIFFERENT videos for one meeting         -> flag, write nothing
  orphan      channel video whose meeting is not in the store    -> counted only

A conflict is never auto-resolved. Legistar has already been caught filing a video
28 days off, so "the city said so" is not decisive — but neither is a parsed title.
Both stay visible and a human picks.

Usage:
    python -m pipeline.ingest_channel_videos            # report only
    python -m pipeline.ingest_channel_videos --write
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

import psycopg

from pipeline.ctn_channel import harvest

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")
CACHE = Path(os.environ.get("GRAPEVINE_CTN_CACHE", ".cache/ctn"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply changes (default: dry run)")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--offline", action="store_true", default=True)
    args = ap.parse_args()

    videos = [v for v in harvest(CACHE, offline=args.offline) if v.is_meeting_like]
    outcome: Counter[str] = Counter()
    conflicts: list[str] = []
    recovered: list[str] = []
    multipart: list[str] = []

    # GROUP BY MEETING BEFORE DECIDING ANYTHING.
    # A livestream that drops and restarts produces several videos with identical
    # titles — the 2026-01-13 Sustainability meeting has four. Judging them one at a
    # time made the first a "recovery" and the rest "conflicts" purely by arrival
    # order, and the totals changed between a dry run and a write. Several videos for
    # one meeting are PARTS, which is exactly why media_assets is keyed on
    # (event_id, host, external_id) rather than on event_id alone.
    from collections import defaultdict
    grouped: dict[tuple[int, object], list] = defaultdict(list)
    for v in videos:
        grouped[(v.body_id, v.stated_date)].append(v)

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        for (body_id, stated_date), group in grouped.items():
            v = group[0]
            if len(group) > 1:
                multipart.append(
                    f"{stated_date}: {len(group)} recordings — {', '.join(g.video_id for g in group)}"
                )
            row = conn.execute(
                """SELECT e.id, b.name, m.id, m.external_id
                     FROM events e
                     JOIN bodies b ON b.id = e.body_id
                LEFT JOIN media_assets m ON m.event_id = e.id
                    WHERE b.legistar_body_id = %s AND e.event_date = %s""",
                (body_id, stated_date),
            ).fetchall()
            if not row:
                outcome["orphan (meeting not ingested)"] += 1
                continue
            # More than one MEETING on this date for this body is a different problem
            # from more than one recording of one meeting. Refuse rather than guess.
            distinct_events = {r[0] for r in row}
            if len(distinct_events) > 1:
                conflicts.append(
                    f"{stated_date}: {len(distinct_events)} separate events for this body "
                    f"— cannot attribute {len(group)} recording(s)")
                outcome["ambiguous meeting"] += 1
                continue

            event_id, body_name = row[0][0], row[0][1]
            existing_ids = {r[3] for r in row if r[3]}

            for g in group:
                if g.video_id in existing_ids:
                    outcome["corroborated"] += 1
                    if args.write:
                        conn.execute(
                            "UPDATE media_assets SET discovered_via='both_corroborated', "
                            "host_title=COALESCE(host_title,%s) "
                            "WHERE event_id=%s AND external_id=%s",
                            (g.title, event_id, g.video_id))
                else:
                    outcome["recovered"] += 1
                    recovered.append(
                        f"{body_name} {stated_date}  {g.video_id}  ({g.tab})"
                        + (f"  [part {group.index(g)+1}/{len(group)}]" if len(group) > 1 else ""))
                    if args.write:
                        conn.execute(
                            """INSERT INTO media_assets
                                 (event_id, host, external_id, url, has_index_points,
                                  host_title, discovered_via)
                               VALUES (%s,'youtube',%s,%s,FALSE,%s,'host_channel')
                               ON CONFLICT DO NOTHING""",
                            (event_id, g.video_id,
                             f"https://www.youtube.com/watch?v={g.video_id}", g.title))
        if args.write:
            conn.execute("UPDATE media_assets SET discovered_via='legistar_calendar' "
                         "WHERE discovered_via IS NULL")
            conn.commit()

    print(f"=== CTN channel match ({len(videos)} meeting videos parsed) ===")
    for k, n in outcome.most_common():
        print(f"  {k:<32} {n}")
    if recovered:
        print(f"\n  recovered videos (first 12 of {len(recovered)}):")
        for r in recovered[:12]:
            print(f"     {r}")
    if multipart:
        print(f"\n  multi-part recordings ({len(multipart)}) — a stream that dropped and restarted:")
        for m in multipart[:6]:
            print(f"     {m}")
    if conflicts:
        print(f"\n  *** CONFLICTS — not written, need a human ({len(conflicts)}) ***")
        for c in conflicts[:10]:
            print(f"     {c}")
    if not args.write:
        print("\n  (dry run — pass --write to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
