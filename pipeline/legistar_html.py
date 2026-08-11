"""Legistar calendar HTML scraper — the path for bodies the Web API cannot see.

WHY THIS EXISTS

The Legistar Web API returns an event only if its agenda status is Final or
Final-revised. Across all 5,538 Ann Arbor events there are zero drafts. The
Sustainability Commission — the pilot body, with monthly meetings — never finalizes
its agendas, so `/events?$filter=EventBodyId eq 1385` returns an empty list and no
error. An API-only ingest would report success and silently hold no pilot corpus at
all. See docs/legistar-api-recon.md §4.

This module is therefore NOT a fallback. For such bodies it is the only source.

It also supplies two things the API never carries for ANY body:
  * the YouTube video URL (`EventVideoPath` is empty on every event in the API)
  * the `View.ashx` document links keyed on the web-UI meeting id + GUID

NOTE ON ID SPACES — this deployment has three, and mixing them is silent corruption:
  * API `BodyId`         138  = City Council   (web UI calls the same body 4166)
  * API `EventId`      14156  = a meeting
  * web-UI meeting id 1367374 = the SAME meeting, used by MeetingDetail/View.ashx
`MeetingRow.meeting_id` is always the third kind.

HOW IT WORKS

Calendar.aspx is ASP.NET WebForms with Telerik combo boxes and a ~370 KB VIEWSTATE.
The landing page shows UPCOMING meetings only; the real query runs when the
"Search Calendar" button posts back with a year and body filter. Both filters are
posted as display text plus a matching `_ClientState` JSON blob — Telerik reads the
latter, so posting only the text silently returns unfiltered results.

Raw HTML is cached verbatim with URL and fetch timestamp, matching the API client's
immutable-raw-layer contract: re-parsing must never require re-fetching.
"""
from __future__ import annotations

import hashlib
import html as html_mod
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CALENDAR_URL = "https://a2gov.legistar.com/Calendar.aspx"
BASE_URL = "https://a2gov.legistar.com/"
MIN_INTERVAL_S = 0.5          # heavier pages than the API; be gentler
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# View.ashx M= codes. The ADA variants are HTML rather than PDF and parse far more
# reliably, which matters because minutes are Tier 1 ground truth. Availability is
# inconsistent — accessible variants appear only from ~March 2026 for some bodies —
# so the PDF fallback is required, not optional.
DOC_CODES = {
    "A": "agenda_url",
    "AADA": "agenda_html_url",
    "M": "minutes_url",
    "MADA": "minutes_html_url",
}


@dataclass
class MeetingRow:
    body_name: str
    meeting_date: str                     # ISO YYYY-MM-DD
    meeting_time: str | None
    location: str | None
    meeting_id: int | None                # web-UI id, NOT the API EventId
    guid: str | None
    agenda_url: str | None = None
    agenda_html_url: str | None = None
    minutes_url: str | None = None
    minutes_html_url: str | None = None
    video_url: str | None = None
    youtube_id: str | None = None
    ecomment_url: str | None = None

    @property
    def has_video(self) -> bool:
        return self.video_url is not None


@dataclass
class ScrapeStats:
    requests: int = 0
    cache_hits: int = 0
    rows_parsed: int = 0
    rows_without_id: int = 0
    warnings: list[str] = field(default_factory=list)


def _combo_state(value: str) -> str:
    """Telerik RadComboBox ClientState. Posting the display text alone is not enough —
    the control reads this blob, and without it the filter is silently ignored."""
    return json.dumps({
        "logEntries": [], "value": value, "text": value,
        "enabled": True, "checkedIndices": [], "checkedItemsTextOverflows": False,
    })


class CalendarScraper:
    def __init__(self, cache_dir: Path | str | None = None, offline: bool = False):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.stats = ScrapeStats()
        self._last_request = 0.0

    # ---------------------------------------------------------------- transport
    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"cal_{hashlib.sha1(key.encode()).hexdigest()}.json"

    def _throttle(self) -> None:
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _request(self, key: str, data: bytes | None = None) -> str:
        cp = self._cache_path(key)
        if cp and cp.exists():
            self.stats.cache_hits += 1
            return json.loads(cp.read_text())["body"]
        if self.offline:
            raise RuntimeError(f"offline and not cached: {key}")

        # RETRY WITH BACKOFF. These are ~1 MB POSTs against an ASP.NET page and the
        # server drops connections under sustained scraping — a widened ingest died
        # partway through with ConnectionResetError. Retrying is not optional here.
        body = None
        last_err: Exception | None = None
        for attempt in range(4):
            self._throttle()
            req = urllib.request.Request(
                CALENDAR_URL, data=data,
                headers={"User-Agent": USER_AGENT,
                         **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})},
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    body = resp.read().decode("utf-8", "ignore")
                break
            except Exception as exc:                      # noqa: BLE001 — retry anything transport-level
                last_err = exc
                self.stats.warnings.append(f"{key}: attempt {attempt + 1} failed ({exc})")
                time.sleep((2 ** attempt) * 2)            # 2s, 4s, 8s
            finally:
                self._last_request = time.monotonic()
        if body is None:
            raise RuntimeError(f"calendar request failed after 4 attempts: {key}") from last_err
        self.stats.requests += 1
        if cp:
            cp.write_text(json.dumps({
                "url": CALENDAR_URL, "key": key,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "body": body,
            }))
        return body

    @staticmethod
    def _hidden(page: str, name: str) -> str:
        m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', page)
        return html_mod.unescape(m.group(1)) if m else ""

    # ------------------------------------------------------------------ public
    def body_names(self) -> list[str]:
        """Display names accepted by the body filter, straight from the dropdown."""
        page = self._request("landing")
        m = re.search(r'id="ctl00_ContentPlaceHolder1_lstBodies_DropDown".*?</div>', page, re.S)
        if not m:
            return []
        return [
            re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", "", li))).strip()
            for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(0), re.S)
        ]

    def search(self, body_name: str = "All Boards and Commissions",
               year: str = "This Year") -> list[MeetingRow]:
        """All meetings for one body and year, past and future.

        The landing page lists only upcoming meetings; this posts the Search Calendar
        button, which is what actually applies the filters.
        """
        landing = self._request("landing")
        form = {
            "__EVENTTARGET": "", "__EVENTARGUMENT": "",
            "__VIEWSTATE": self._hidden(landing, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": self._hidden(landing, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": self._hidden(landing, "__EVENTVALIDATION"),
            "ctl00$ContentPlaceHolder1$lstYears": year,
            "ctl00_ContentPlaceHolder1_lstYears_ClientState": _combo_state(year),
            "ctl00$ContentPlaceHolder1$lstBodies": body_name,
            "ctl00_ContentPlaceHolder1_lstBodies_ClientState": _combo_state(body_name),
            "ctl00$ContentPlaceHolder1$txtDateFilter": "",
            "ctl00$ContentPlaceHolder1$txtDateFilter$dateInput": "",
            "ctl00$ContentPlaceHolder1$btnSearch": "Search Calendar",
        }
        page = self._request(f"search::{body_name}::{year}",
                             urllib.parse.urlencode(form).encode())
        return self._parse(page, body_name)

    # ------------------------------------------------------------------ parsing
    def _parse(self, page: str, expect_body: str) -> list[MeetingRow]:
        rows: list[MeetingRow] = []
        for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", raw, re.S)
            if len(cells) < 6:
                continue
            body = self._text(cells[0])
            date = self._text(cells[1])
            if not re.match(r"\d{1,2}/\d{1,2}/\d{4}$", date):
                continue
            if expect_body != "All Boards and Commissions" and expect_body.lower() not in body.lower():
                continue

            mid, guid = None, None
            m = re.search(r"MeetingDetail\.aspx\?ID=(\d+)&(?:amp;)?GUID=([0-9A-Fa-f-]+)", raw)
            if m:
                mid, guid = int(m.group(1)), m.group(2)
            else:
                self.stats.rows_without_id += 1
                self.stats.warnings.append(f"{body} {date}: no MeetingDetail link")

            mo, d, y = date.split("/")
            row = MeetingRow(
                body_name=body,
                meeting_date=f"{y}-{int(mo):02d}-{int(d):02d}",
                meeting_time=self._text(cells[3]) if len(cells) > 3 else None,
                location=self._text(cells[4])[:400] if len(cells) > 4 else None,
                meeting_id=mid, guid=guid,
            )
            for code, attr in DOC_CODES.items():
                dm = re.search(rf'href="(View\.ashx\?M={code}&(?:amp;)?ID=\d+[^"]*)"', raw)
                if dm:
                    setattr(row, attr, BASE_URL + html_mod.unescape(dm.group(1)))
            # THREE YouTube URL forms appear in this calendar, and matching only the
            # first silently under-counts video coverage:
            #   https://www.youtube.com/watch?v=<id>&Mode2=Video
            #   https://www.youtube.com/live/<id>?si=…          (streamed meetings)
            #   https://youtu.be/<id>?si=…&Mode2=Video          (short form)
            # The short form was missed on the first pass and cost real rows.
            vm = re.search(r'href="(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^"]+)"', raw)
            if vm:
                row.video_url = html_mod.unescape(vm.group(1))
                ym = re.search(
                    r"(?:watch\?v=|live/|youtu\.be/)([A-Za-z0-9_-]{11})", row.video_url
                )
                row.youtube_id = ym.group(1) if ym else None
                if row.youtube_id is None:
                    self.stats.warnings.append(
                        f"{body} {date}: unparsed video URL {row.video_url}"
                    )
            rows.append(row)
            self.stats.rows_parsed += 1
        return rows

    @staticmethod
    def _text(cell: str) -> str:
        return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
