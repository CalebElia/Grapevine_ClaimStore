"""Recover meeting videos from the CTN Ann Arbor YouTube channel.

WHY — Legistar is not a reliable index of what was recorded.

Three separate failures observed, all confirmed:
  1. Many past meetings simply have no video link on the calendar at all
     (33 of 59 City Council meetings in the ingested window).
  2. Some links that do exist resolve to a CTN landing page with nothing playable.
  3. At least one link points at the WRONG meeting — video IkZ4APPWNgY is filed
     under 2026-05-12 but is titled "…Meeting 4/14/26".

Meanwhile the city's own channel, @CTNAnnArbor, holds the recordings. So the channel
is both a RECOVERY source for meetings Legistar never linked and an INDEPENDENT
CHECK on the links it did publish.

TWO TABS, AND MISSING THE SECOND COSTS MOST OF THE CORPUS.
YouTube files past livestreams separately from uploads. Council and commission
meetings are streamed live, so they land under /streams, not /videos. Of the 25
videos Legistar had already given us, only 5 appear in /videos — the other 20 are
streams. Enumerating /videos alone would look like it worked and would miss 80%.

EVIDENCE STRENGTH — recorded, not flattened.
A Legistar link is the city ASSERTING an association. A channel match is US INFERRING
one from a title string. Those are different claims and `media_assets.discovered_via`
keeps them apart, so a later reviewer can tell which videos rest on a parsed title.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CHANNEL = "https://www.youtube.com/@CTNAnnArbor"
TABS = ("videos", "streams")     # BOTH. See module docstring.

# Legistar BodyId -> patterns that identify that body in a video title.
# Ordered longest-first within each body so "City Planning Commission" cannot be
# swallowed by a looser "City Council" style match.
BODY_PATTERNS: dict[int, list[re.Pattern]] = {
    1385: [re.compile(r"\bsustainability\s+commission\b", re.I)],
    220:  [re.compile(r"\benergy\s+commission\b", re.I)],
    222:  [re.compile(r"\benvironmental\s+commission\b", re.I)],
    153:  [re.compile(r"\b(?:city\s+)?planning\s+commission\b", re.I)],
    365:  [re.compile(r"\btransportation\s+commission\b", re.I)],
    138:  [re.compile(r"\bcity\s+council\b", re.I)],
}

# Both separators occur in real titles: "3/10/2026" and "2-22-24".
_DATE_RE = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b")

# Titles that carry a body name and a date but are NOT the meeting recording.
_NOT_A_MEETING = re.compile(
    r"\b(working session|work session|caucus|budget work|retreat|"
    r"public hearing only|part \d|continued)\b", re.I
)


@dataclass
class ChannelVideo:
    video_id: str
    title: str
    tab: str
    body_id: int | None = None
    stated_date: date | None = None
    is_meeting_like: bool = False


def list_channel(tab: str, cache_dir: Path | str | None = None,
                 offline: bool = False) -> list[tuple[str, str]]:
    """(video_id, title) for one channel tab. Cached; yt-dlp does no download."""
    cp = Path(cache_dir) / f"ctn_{tab}.json" if cache_dir else None
    if cp and cp.exists():
        return [tuple(x) for x in json.loads(cp.read_text())]
    if offline:
        return []
    # NOTE the separator. yt-dlp's --print template does NOT interpret backslash
    # escapes, so "\t" would arrive as two literal characters and split() on a real
    # tab would silently yield zero rows. A pipe avoids the whole question.
    out = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--no-warnings", "--print", "%(id)s|%(title)s",
         f"{CHANNEL}/{tab}"],
        capture_output=True, text=True, timeout=1800,
    ).stdout
    rows = [tuple(l.split("|", 1)) for l in out.splitlines() if "|" in l]
    if cp:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(rows))
    return rows


def parse_title(title: str) -> tuple[int | None, date | None, bool]:
    """-> (body_id, stated_date, looks_like_a_regular_meeting)."""
    body_id = None
    for bid, pats in BODY_PATTERNS.items():
        if any(p.search(title) for p in pats):
            body_id = bid
            break
    d = None
    m = _DATE_RE.search(title)
    if m:
        mo, dd, y = (int(g) for g in m.groups())
        if y < 100:
            y += 2000
        try:
            d = date(y, mo, dd)
        except ValueError:
            d = None
    return body_id, d, bool(body_id and d and not _NOT_A_MEETING.search(title))


def harvest(cache_dir: Path | str | None = None, offline: bool = False) -> list[ChannelVideo]:
    seen: set[str] = set()
    out: list[ChannelVideo] = []
    for tab in TABS:
        for vid, title in list_channel(tab, cache_dir, offline):
            if vid in seen:
                continue
            seen.add(vid)
            bid, d, meeting = parse_title(title)
            out.append(ChannelVideo(vid, title, tab, bid, d, meeting))
    return out
