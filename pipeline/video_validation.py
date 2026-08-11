"""Verify that a host video actually belongs to the meeting it is linked from.

WHY THIS EXISTS — an observed, confirmed error in the source data.

Legistar's calendar attached video `IkZ4APPWNgY` to the Ann Arbor Sustainability
Commission row for 2026-05-12. YouTube reports that video as
"Ann Arbor Sustainability Commission Meeting 4/14/26", uploaded 2026-04-24 — it is the
APRIL 14 meeting. The 2026-04-14 calendar row, meanwhile, carries no video link at all.

Ingested naively, every claim extracted from that recording would be timestamped seven
weeks late. Chronology is the join key across every source in this project, so a
misdated recording is worse than a missing one: it is wrong in a way nothing downstream
can detect.

The calendar's meeting->video association is therefore treated as EVIDENCE, not as
ground truth, and checked against two independent signals the host provides:

  1. THE TITLE DATE. Ann Arbor titles these consistently enough to parse
     ("… Meeting 3/10/2026", "… Commission 6/9/26"). Where a date is present it is the
     stronger signal, because it states what the recording IS rather than when it was
     handled.
  2. THE UPLOAD DATE. Should fall on or shortly after the meeting. Useful as a
     plausibility bound and as the only check when the title carries no date.

Verdicts map to the `video_date_verification` vocabulary:
    matched            title date == meeting date
    title_mismatch     title date != meeting date   <- provenance-corrupting; blocks ASR
    upload_implausible no title date, and upload falls outside the plausible window
    no_title_date      no date in title, upload plausible — weak confirmation only
    unverified         metadata could not be fetched
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# A recording normally appears within a day or two. The April case took ten days, so a
# generous window avoids flagging slow uploads as errors — this bound is only used when
# the title gives us nothing better.
UPLOAD_WINDOW_DAYS_BEFORE = 1
UPLOAD_WINDOW_DAYS_AFTER = 21

# "3/10/2026", "6/9/26", "12/08/2025"
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")


@dataclass
class VideoCheck:
    external_id: str
    meeting_date: date
    verdict: str
    host_title: str | None = None
    host_upload_date: date | None = None
    title_stated_date: date | None = None
    detail: str = ""

    @property
    def blocks_extraction(self) -> bool:
        """A mismatched recording must not be transcribed against this meeting."""
        return self.verdict in ("title_mismatch", "upload_implausible")


def parse_title_date(title: str) -> date | None:
    """Pull a meeting date out of a host title. Two-digit years map to 2000s."""
    m = _DATE_RE.search(title or "")
    if not m:
        return None
    mo, d, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def fetch_metadata(external_id: str, cache_dir: Path | str | None = None,
                   offline: bool = False) -> dict | None:
    """yt-dlp metadata only — no media is downloaded. Cached by video id."""
    cp = None
    if cache_dir:
        cp = Path(cache_dir) / f"yt_{external_id}.json"
        cp.parent.mkdir(parents=True, exist_ok=True)
        if cp.exists():
            return json.loads(cp.read_text())
    if offline:
        return None
    try:
        out = subprocess.run(
            ["yt-dlp", "--skip-download", "--no-warnings", "--print",
             "%(id)s\t%(upload_date)s\t%(duration)s\t%(title)s",
             f"https://www.youtube.com/watch?v={external_id}"],
            capture_output=True, text=True, timeout=90, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    parts = out.split("\t")
    if len(parts) < 4:
        return None
    meta = {
        "id": parts[0],
        "upload_date": parts[1] or None,
        "duration": int(parts[2]) if parts[2].isdigit() else None,
        "title": parts[3],
    }
    if cp:
        cp.write_text(json.dumps(meta))
    return meta


def check(external_id: str, meeting_date: date | str,
          cache_dir: Path | str | None = None, offline: bool = False) -> VideoCheck:
    if isinstance(meeting_date, str):
        meeting_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()

    meta = fetch_metadata(external_id, cache_dir, offline)
    if meta is None:
        return VideoCheck(external_id, meeting_date, "unverified",
                          detail="metadata unavailable")

    upload = None
    if meta.get("upload_date"):
        try:
            upload = datetime.strptime(meta["upload_date"], "%Y%m%d").date()
        except ValueError:
            pass
    stated = parse_title_date(meta.get("title", ""))

    base = dict(external_id=external_id, meeting_date=meeting_date,
                host_title=meta.get("title"), host_upload_date=upload,
                title_stated_date=stated)

    # The title is the stronger signal: it states what the recording IS.
    if stated is not None:
        if stated == meeting_date:
            return VideoCheck(**base, verdict="matched",
                              detail=f"title date {stated} matches")
        return VideoCheck(
            **base, verdict="title_mismatch",
            detail=(f"title says {stated}, calendar says {meeting_date} "
                    f"({abs((stated - meeting_date).days)} days apart)"),
        )

    if upload is not None:
        lo = meeting_date - timedelta(days=UPLOAD_WINDOW_DAYS_BEFORE)
        hi = meeting_date + timedelta(days=UPLOAD_WINDOW_DAYS_AFTER)
        if lo <= upload <= hi:
            return VideoCheck(**base, verdict="no_title_date",
                              detail=f"no date in title; upload {upload} is plausible")
        return VideoCheck(**base, verdict="upload_implausible",
                          detail=f"no date in title; upload {upload} outside {lo}..{hi}")

    return VideoCheck(**base, verdict="unverified", detail="no title date, no upload date")
