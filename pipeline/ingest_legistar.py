"""Load the Ann Arbor structural layer from Legistar into the claim store.

Roadmap principles this implements literally:

  * RAW LAYER IMMUTABLE — every API response is cached verbatim with URL and fetch
    timestamp by LegistarClient. This module only reads that cache and derives rows,
    so a schema change means a re-parse, not a re-fetch.
  * DERIVED LAYER DISPOSABLE — every write is idempotent (ON CONFLICT on the natural
    key), so re-running is safe and `./scripts/db.sh reset` costs nothing but time.
  * INCREMENTAL FROM DAY ONE — `--since` on every event read.
  * FAIL LOUD — missing fields are counted and reported per record id. Nothing is
    silently null-filled, and coverage is reported rather than assumed.

The last point has teeth here. The API only returns events whose agenda status is
Final or Final-revised, so a body that does not finalize agendas yields zero rows and
no error. The Sustainability Commission — the pilot body — holds monthly meetings and
returns zero. `report_coverage()` names that explicitly instead of letting a clean run
imply complete coverage.

Usage:
    python -m pipeline.ingest_legistar --since 2021-01-01
    python -m pipeline.ingest_legistar --since 2021-01-01 --with-items --with-votes
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from pipeline.legistar_html import CalendarScraper
from pipeline.legistar import (
    A2ZERO_RELEVANT_BODIES,
    BODY_IDS,
    LegistarClient,
)

DSN = os.environ.get(
    "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine"
)
CACHE = Path(os.environ.get("GRAPEVINE_LEGISTAR_CACHE", ".cache/legistar"))
HTML_CACHE = Path(os.environ.get("GRAPEVINE_LEGISTAR_HTML_CACHE", ".cache/legistar_html"))

# Calendar display name -> API BodyId. The HTML filter takes a display name; the
# bodies table is keyed on the API BodyId, so the two must be bridged explicitly.
HTML_BODY_DISPLAY = {
    "Sustainability Commission": BODY_IDS["sustainability_commission"],
    "Energy Commission": BODY_IDS["energy_commission"],
    "Environmental Commission": BODY_IDS["environmental_commission"],
    "City Council": BODY_IDS["city_council"],
}


def _body_id_for_display(name: str) -> int | None:
    return HTML_BODY_DISPLAY.get(name)

OCD_ANN_ARBOR = "ocd-division/country:us/state:mi/place:ann_arbor"

# Legistar BodyTypeName -> our body_classification vocabulary. Anything unmapped falls
# through to the vocabulary trigger, which stores 'other' and logs a proposal — so an
# unrecognised type surfaces in review rather than crashing the ingest.
CLASSIFICATION = {
    "Primary Legislative Body": "legislature",
    "Council Committee": "committee",
    "Citizen Board or Commission": "commission",
    "Planning Commission": "commission",
    "Energy Commission": "commission",
    "Environmental Commission": "commission",
    "Sustainability Commission": "commission",
    "Transportation Commission": "commission",
    "Housing Commission": "commission",
    "Greenbelt Advisory Commission": "commission",
    "Housing Board of Appeals": "board",
}


@dataclass
class Report:
    bodies: int = 0
    persons: int = 0
    events: int = 0
    events_enriched: int = 0
    event_items: int = 0
    media_assets: int = 0
    votes: int = 0
    vote_rows_discarded: int = 0
    lineage: int = 0
    missing_fields: dict[str, int] = field(default_factory=dict)
    api_invisible_bodies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def note_missing(self, field_name: str) -> None:
        self.missing_fields[field_name] = self.missing_fields.get(field_name, 0) + 1


def _jurisdiction(conn) -> int:
    """Ann Arbor. Fiscal year starts July 1 per City Charter — load-bearing for
    interpreting every FY-dated claim downstream."""
    row = conn.execute(
        """INSERT INTO jurisdictions
             (ocd_division_id, name, state, legistar_client, home_rule,
              government_form, utility_governance, fiscal_year_start_month,
              fiscal_year_labeled_by_end_year)
           VALUES (%s,'Ann Arbor','MI','a2gov',TRUE,'council_manager',
                   'iou_under_puc',7,TRUE)
           ON CONFLICT (ocd_division_id) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""",
        (OCD_ANN_ARBOR,),
    ).fetchone()
    return row[0]


def ingest_bodies(client: LegistarClient, conn, jid: int, rep: Report) -> dict[int, int]:
    """Returns {legistar_body_id: bodies.id}."""
    mapping: dict[int, int] = {}
    for b in client.bodies():
        name = b.get("BodyName")
        if not name:
            rep.note_missing("BodyName")
            continue
        row = conn.execute(
            """INSERT INTO bodies
                 (jurisdiction_id, legistar_body_id, name, classification,
                  active, description)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (jurisdiction_id, legistar_body_id) DO UPDATE
                 SET name = EXCLUDED.name,
                     active = EXCLUDED.active,
                     description = EXCLUDED.description
               RETURNING id""",
            (
                jid,
                b["BodyId"],
                name,
                CLASSIFICATION.get(b.get("BodyTypeName", ""), "other"),
                bool(b.get("BodyActiveFlag")),
                (b.get("BodyDescription") or "").strip() or None,
            ),
        ).fetchone()
        mapping[b["BodyId"]] = row[0]
        rep.bodies += 1
    return mapping


def ingest_body_lineage(conn, bmap: dict[int, int], rep: Report) -> None:
    """Energy (220) + Environmental (222) -> Sustainability (1385), 2025.

    Recorded because a body-scoped query that is not lineage-aware silently drops the
    earlier half of the A2Zero record.

    CORRECTED 2026-08-03 — the first version of this was wrong, and wrong in an
    instructive way. It set effective_date 2025-07-01 from API evidence: Energy's last
    meeting appeared to be 2025-06-10 and Environmental's 2024-02-22. But the API only
    exposes meetings with a FINAL agenda, so it had been undercounting these bodies too.
    The calendar HTML shows both kept meeting well past those dates:

        Sustainability Commission  first met  2025-08-12
        Environmental Commission   last met   2025-12-04
        Energy Commission          last met   2025-12-09

    All three bodies met CONCURRENTLY for about four months. So the predecessors did
    not stop and hand over; a successor was stood up alongside them and they wound down
    afterwards. effective_date is therefore no earlier than 2025-12-09, and the real
    transition is a period rather than a date.

    The lesson generalises: any conclusion drawn from the API alone inherits its
    Final-agenda filter. This one survived three sessions before the HTML contradicted it.
    """
    successor = bmap.get(BODY_IDS["sustainability_commission"])
    if successor is None:
        rep.warnings.append(
            "Sustainability Commission (1385) absent from /bodies — lineage not written"
        )
        return
    # Dates from the calendar HTML, which is complete; the API's are filtered.
    for pred_key, last_met in (
        ("energy_commission", "2025-12-09"),
        ("environmental_commission", "2025-12-04"),
    ):
        pred = bmap.get(BODY_IDS[pred_key])
        if pred is None:
            rep.warnings.append(f"predecessor {pred_key} absent — lineage edge skipped")
            continue
        conn.execute(
            """INSERT INTO body_lineage
                 (successor_body_id, predecessor_body_id, relation, effective_date, source)
               VALUES (%s,%s,'merged_into','2025-12-09',%s)
               ON CONFLICT DO NOTHING""",
            (successor, pred,
             f"bodies.description; predecessor last met {last_met} (calendar HTML). "
             f"Successor first met 2025-08-12, so all three bodies OVERLAPPED ~4 months. "
             f"effective_date is a lower bound, not the transition date. NEEDS HUMAN CONFIRMATION."),
        )
        rep.lineage += 1


def ingest_persons(client: LegistarClient, conn, rep: Report) -> dict[int, int]:
    """Returns {legistar_person_id: persons.id}.

    is_public_figure = TRUE for everyone here: these are elected officials and
    appointed commissioners drawn from the official roster. Members of the public who
    speak at public comment are NOT ingested from this source and default to FALSE,
    which is what gates voiceprinting.
    """
    mapping: dict[int, int] = {}
    for p in client.persons():
        full = (p.get("PersonFullName") or "").strip()
        if not full:
            first = (p.get("PersonFirstName") or "").strip()
            last = (p.get("PersonLastName") or "").strip()
            full = f"{first} {last}".strip()
        if not full:
            rep.note_missing("PersonFullName")
            continue
        row = conn.execute(
            """INSERT INTO persons (legistar_person_id, full_name, sort_name, is_public_figure)
               VALUES (%s,%s,%s,TRUE)
               ON CONFLICT (legistar_person_id) WHERE legistar_person_id IS NOT NULL
                 DO UPDATE SET full_name = EXCLUDED.full_name
               RETURNING id""",
            (p["PersonId"], full, (p.get("PersonLastName") or None)),
        ).fetchone()
        mapping[p["PersonId"]] = row[0]
        rep.persons += 1
    return mapping


def ingest_events(
    client: LegistarClient, conn, bmap: dict[int, int],
    body_ids: list[int], since: str | None, rep: Report,
) -> dict[int, int]:
    """Returns {legistar_event_id: events.id}."""
    mapping: dict[int, int] = {}
    for bid in body_ids:
        evs = client.events(body_id=bid, since=since)
        if not evs:
            name = next((k for k, v in BODY_IDS.items() if v == bid), str(bid))
            rep.api_invisible_bodies.append(f"{name} (BodyId {bid})")
            continue
        for e in evs:
            our_body = bmap.get(e["EventBodyId"])
            if our_body is None:
                rep.warnings.append(f"event {e['EventId']} references unknown body {e['EventBodyId']}")
                continue
            if not e.get("EventVideoPath"):
                rep.note_missing("EventVideoPath")
            if not e.get("EventTime"):
                rep.note_missing("EventTime")
            date = e["EventDate"][:10]
            # MATCH AN EXISTING HTML-SOURCED ROW RATHER THAN INSERTING A SECOND ONE.
            # The two sources share no identifier: the API's EventGuid and the
            # calendar's MeetingDetail GUID are unrelated values (verified — 0 of 7
            # matched). So the join key is (body_id, event_date).
            #
            # That is safe HERE and the assumption is checked, not assumed: no Ann
            # Arbor body meets twice on one date in any source sampled. A jurisdiction
            # that holds a work session and a regular meeting on the same day would
            # break it, so the guard below fails loudly rather than silently merging
            # two different meetings into one row.
            existing = conn.execute(
                "SELECT id FROM events WHERE body_id=%s AND event_date=%s",
                (our_body, date),
            ).fetchall()
            if len(existing) > 1:
                rep.warnings.append(
                    f"AMBIGUOUS: {len(existing)} rows already exist for body {our_body} "
                    f"on {date}; API event {e['EventId']} not linked"
                )
                continue

            if existing:
                # Enrich the HTML row with the API identity and the fields only the
                # API carries. Do NOT overwrite document URLs — the HTML ones point at
                # View.ashx and are strictly better than EventAgendaFile.
                conn.execute(
                    """UPDATE events
                          SET legistar_event_id = %s,
                              legistar_guid     = COALESCE(legistar_guid, %s),
                              start_time        = COALESCE(start_time, %s),
                              location          = COALESCE(location, %s),
                              agenda_url        = COALESCE(agenda_url, %s),
                              ecomment_url      = COALESCE(ecomment_url, %s)
                        WHERE id = %s""",
                    (e["EventId"], e.get("EventGuid"), _parse_time(e.get("EventTime")),
                     e.get("EventLocation"), e.get("EventAgendaFile"),
                     e.get("EventEComment"), existing[0][0]),
                )
                mapping[e["EventId"]] = existing[0][0]
                rep.events_enriched += 1
                continue

            row = conn.execute(
                """INSERT INTO events
                     (body_id, legistar_event_id, legistar_guid, event_date, start_time,
                      location, meeting_kind, video_available, agenda_url, ecomment_url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (body_id, legistar_event_id) DO UPDATE
                     SET event_date = EXCLUDED.event_date,
                         agenda_url = EXCLUDED.agenda_url
                   RETURNING id""",
                (
                    our_body, e["EventId"], e.get("EventGuid"), date,
                    _parse_time(e.get("EventTime")),
                    e.get("EventLocation"),
                    _meeting_kind(e.get("EventBodyName"), e.get("EventComment")),
                    # EventVideoPath is empty on all 5,538 API events; video presence
                    # is only knowable from the calendar HTML, so NULL (unknown) here
                    # rather than FALSE (known absent).
                    None,
                    e.get("EventAgendaFile"),
                    # NOTE: EventInSiteURL is the MeetingDetail page, NOT the minutes.
                    # An earlier version stored it in minutes_html_url, which mislabelled
                    # a navigation link as a document. Minutes URLs come from HTML only.
                    e.get("EventEComment"),
                ),
            ).fetchone()
            mapping[e["EventId"]] = row[0]
            rep.events += 1
    return mapping


def _parse_time(t: str | None):
    if not t:
        return None
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            from datetime import datetime
            return datetime.strptime(t.strip(), fmt).time()
        except ValueError:
            continue
    return None


def _meeting_kind(body_name: str | None, comment: str | None) -> str:
    text = f"{body_name or ''} {comment or ''}".lower()
    if "work session" in text:
        return "work_session"
    if "planning session" in text:
        return "planning_session"
    if "special" in text:
        return "special"
    if "cancel" in text:
        return "cancelled"
    return "regular"


def ingest_items_and_votes(
    client: LegistarClient, conn, emap: dict[int, int], pmap: dict[int, int],
    rep: Report, with_votes: bool,
) -> None:
    for legistar_event_id, our_event in emap.items():
        for it in client.event_items(legistar_event_id):
            item_id = it.get("EventItemId")
            if item_id is None:
                rep.note_missing("EventItemId")
                continue
            row = conn.execute(
                """INSERT INTO event_items
                     (event_id, legistar_item_id, sequence, title, action_text,
                      passed_flag, on_consent_agenda, mover_person_id, seconder_person_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (legistar_item_id) WHERE legistar_item_id IS NOT NULL
                     DO UPDATE SET action_text = EXCLUDED.action_text,
                                   passed_flag = EXCLUDED.passed_flag
                   RETURNING id""",
                (
                    our_event, item_id, it.get("EventItemAgendaSequence"),
                    it.get("EventItemTitle"), it.get("EventItemActionName"),
                    _passed(it.get("EventItemPassedFlag")),
                    bool(it.get("EventItemConsent")),
                    pmap.get(it.get("EventItemMoverId")),
                    pmap.get(it.get("EventItemSeconderId")),
                ),
            ).fetchone()
            our_item = row[0]
            rep.event_items += 1

            if not with_votes or it.get("EventItemPassedFlag") is None:
                continue
            # values_only=True: 91% of vote rows carry a member roster with a NULL
            # value, all on consent items. Storing those would make a bundled consent
            # passage indistinguishable from a recorded roll call.
            all_rows = client.votes(item_id, values_only=False)
            valued = [v for v in all_rows if v.get("VoteValueName")]
            rep.vote_rows_discarded += len(all_rows) - len(valued)
            for v in valued:
                pid = pmap.get(v.get("VotePersonId"))
                if pid is None:
                    rep.warnings.append(
                        f"vote on item {item_id} references unknown person {v.get('VotePersonId')}"
                    )
                    continue
                conn.execute(
                    """INSERT INTO votes (event_item_id, person_id, vote_value)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (event_item_id, person_id) DO UPDATE
                         SET vote_value = EXCLUDED.vote_value""",
                    (our_item, pid, v["VoteValueName"].lower()),
                )
                rep.votes += 1


def _passed(flag):
    return None if flag is None else bool(flag)


def ingest_html_body(
    scraper, conn, bmap: dict[int, int], legistar_body_id: int,
    body_display_name: str, years: list[str], rep: Report,
) -> None:
    """Ingest a body the API cannot see, from the calendar HTML.

    For the Sustainability Commission this is not a supplement — it is the only
    source. It also supplies two fields the API never carries for ANY body: the
    YouTube URL (`EventVideoPath` is empty on all 5,538 API events) and the
    View.ashx document links keyed on the web-UI meeting id.

    Idempotency runs on `legistar_meeting_id`, because these rows have no API
    EventId and NULLs do not collide under UNIQUE (body_id, legistar_event_id).
    """
    our_body = bmap.get(legistar_body_id)
    if our_body is None:
        rep.warnings.append(f"body {legistar_body_id} not ingested; HTML rows skipped")
        return

    for year in years:
        for row in scraper.search(body_display_name, year):
            if row.meeting_id is None:
                rep.note_missing("MeetingDetail id")
                continue
            ev = conn.execute(
                """INSERT INTO events
                     (body_id, legistar_event_id, legistar_meeting_id, legistar_guid,
                      event_date, location, meeting_kind, video_available,
                      agenda_url, agenda_html_url, minutes_url, minutes_html_url)
                   VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (legistar_meeting_id) WHERE legistar_meeting_id IS NOT NULL
                     DO UPDATE SET agenda_html_url  = EXCLUDED.agenda_html_url,
                                   minutes_html_url = EXCLUDED.minutes_html_url,
                                   video_available  = EXCLUDED.video_available
                   RETURNING id""",
                (our_body, row.meeting_id, row.guid, row.meeting_date,
                 row.location, _meeting_kind(row.body_name, row.location),
                 row.has_video, row.agenda_url, row.agenda_html_url,
                 row.minutes_url, row.minutes_html_url),
            ).fetchone()
            rep.events += 1

            if row.youtube_id:
                # discovered_via IS SET HERE, at creation. It was previously left NULL
                # and backfilled by ingest_channel_videos — a separate tool that may
                # never run, which meant provenance was a promise rather than a record.
                # Whoever creates a row states how it came to exist.
                conn.execute(
                    """INSERT INTO media_assets
                         (event_id, host, external_id, url, has_index_points, discovered_via)
                       VALUES (%s,'youtube',%s,%s,FALSE,'legistar_calendar')
                       ON CONFLICT DO NOTHING""",
                    (ev[0], row.youtube_id, row.video_url),
                )
                rep.media_assets += 1
            elif row.minutes_url:
                # A past meeting with minutes but no video link. Absence of video is
                # NOT absence of contestation — some sessions are explicitly not
                # broadcast — so record it as a known-unknown rather than a silent gap.
                rep.warnings.append(
                    f"{row.body_name} {row.meeting_date}: minutes published, no video link"
                )


def report_coverage(rep: Report) -> str:
    lines = [
        "=== Legistar structural ingest ===",
        f"  bodies        {rep.bodies}",
        f"  persons       {rep.persons}",
        f"  events        {rep.events} new, {rep.events_enriched} enriched from API",
        f"  event_items   {rep.event_items}",
        f"  media_assets  {rep.media_assets}",
        f"  votes         {rep.votes}   (discarded {rep.vote_rows_discarded} null-valued rows)",
        f"  body_lineage  {rep.lineage}",
    ]
    if rep.missing_fields:
        lines.append("  missing fields (counted, not null-filled):")
        for k, n in sorted(rep.missing_fields.items(), key=lambda x: -x[1]):
            lines.append(f"     {k}: {n}")
    if rep.api_invisible_bodies:
        lines += [
            "",
            "  *** API-INVISIBLE BODIES — zero events returned ***",
            "  The API only exposes events with a Final/Final-revised agenda. These bodies",
            "  returned nothing, which is NOT evidence that they hold no meetings:",
        ]
        lines += [f"     {b}" for b in rep.api_invisible_bodies]
        lines.append("  -> these require the calendar HTML path before coverage is complete.")
    if rep.warnings:
        lines.append(f"  warnings ({len(rep.warnings)}):")
        lines += [f"     {w}" for w in rep.warnings[:12]]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest Ann Arbor Legistar structural layer")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD; incremental window")
    ap.add_argument("--bodies", default=None,
                    help="comma-separated Legistar BodyIds (default: A2Zero-relevant)")
    ap.add_argument("--with-items", action="store_true", help="also ingest event items")
    ap.add_argument("--with-votes", action="store_true", help="also ingest per-member votes")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--html-bodies", default=None,
                    help="comma-separated display names to scrape from the calendar HTML, "
                         "for bodies the API cannot see (e.g. 'Sustainability Commission')")
    ap.add_argument("--html-years", default="2025,2026",
                    help="years to scrape for --html-bodies")
    ap.add_argument("--offline", action="store_true", help="use only the raw cache")
    args = ap.parse_args()

    body_ids = ([int(x) for x in args.bodies.split(",")] if args.bodies
                else A2ZERO_RELEVANT_BODIES)
    client = LegistarClient(cache_dir=CACHE, offline=args.offline)
    rep = Report()

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        jid = _jurisdiction(conn)
        bmap = ingest_bodies(client, conn, jid, rep)
        ingest_body_lineage(conn, bmap, rep)
        pmap = ingest_persons(client, conn, rep)

        # ORDER MATTERS. The calendar HTML is the more complete source of MEETINGS —
        # for City Council 2026 it sees 28 where the API sees 17, and one of the
        # eleven it adds already happened. It is also the only source of video URLs
        # and of the View.ashx document links. The API's unique contribution is
        # event_items and votes. So: HTML lays down the rows, the API attaches its
        # EventId to them, and only then can items and votes be fetched.
        if args.html_bodies:
            scraper = CalendarScraper(cache_dir=HTML_CACHE, offline=args.offline)
            years = [y.strip() for y in args.html_years.split(",")]
            for display in [b.strip() for b in args.html_bodies.split(",")]:
                lbid = _body_id_for_display(display)
                if lbid is None:
                    rep.warnings.append(f"no known BodyId for display name {display!r}")
                    continue
                ingest_html_body(scraper, conn, bmap, lbid, display, years, rep)
                # COMMIT PER BODY. One transaction spanning the whole ingest meant a
                # ConnectionResetError near the end rolled back everything, including
                # work that had already succeeded. Each body is now durable on its own.
                conn.commit()

        emap = ingest_events(client, conn, bmap, body_ids, args.since, rep)
        conn.commit()
        if args.with_items:
            ingest_items_and_votes(client, conn, emap, pmap, rep, args.with_votes)
        conn.commit()

    print(report_coverage(rep))
    s = client.stats
    print(f"\n  api: {s.requests} requests, {s.cache_hits} cache hits, {s.rows} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
