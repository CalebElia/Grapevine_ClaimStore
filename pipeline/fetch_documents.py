"""Fetch and text-extract meeting documents, so topic search runs on the RECORD.

WHY THIS EXISTS. The first corpus ranking searched `event_items` — Legistar's agenda item
TITLES. Those are headings, written before the meeting. Minutes are the narrative record,
agreed after it, and carry the discussion itself. Ranking meetings by what their agenda
was *called* is a weaker instrument than ranking by what was *recorded as said*.

BASIS IS TRACKED PER MEETING, because coverage is uneven and the unevenness is not random:

    City Council               237/270 minutes  (88%)
    Sustainability Commission   15/20  minutes  (75%)
    Energy Commission           10/46  minutes  (22%)
    Environmental Commission     9/40  minutes  (23%)

Ranking purely on minutes would therefore favour the bodies that FILE minutes, not the
bodies that DISCUSS the topic — a measurement artifact that would look exactly like a
finding. So: use minutes where they exist, fall back to the agenda where they do not, and
record which basis every number came from.

The Sustainability Commission is the reason this is urgent. Its events arrived by HTML
scraping rather than the Legistar API, so it has ZERO agenda items and is invisible to
title search — while being the body where this project currently lives.

Cache is immutable: a fetched document is never re-fetched, so a re-run costs nothing and
the corpus is reproducible.

Usage:
    python -m pipeline.fetch_documents --body "Sustainability Commission"
    python -m pipeline.fetch_documents --body "City Council" --limit 40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
CACHE = REPO / ".cache" / "documents"
DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "\
     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def cache_path(url: str, kind: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE / kind / f"{h}.json"


def extract_pdf(raw: bytes) -> str:
    import io
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


def extract_html(raw: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))


def fetch(url: str, fmt: str, kind: str) -> dict:
    """Fetch once, cache forever. Returns {text, chars, from_cache, error}."""
    p = cache_path(url, kind)
    if p.exists():
        d = json.loads(p.read_text())
        d["from_cache"] = True
        return d
    import httpx
    p.parent.mkdir(parents=True, exist_ok=True)
    rec: dict = {"url": url, "format": fmt, "kind": kind,
                 "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=90, follow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        # Trust the response, not the recorded format — Legistar serves PDFs from URLs
        # that look like HTML endpoints and vice versa.
        if "pdf" in ctype.lower() or r.content[:5] == b"%PDF-":
            rec["text"] = extract_pdf(r.content)
            rec["detected"] = "pdf"
        else:
            rec["text"] = extract_html(r.content)
            rec["detected"] = "html"
        rec["chars"] = len(rec["text"])
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        rec["text"], rec["chars"] = "", 0
    p.write_text(json.dumps(rec))
    rec["from_cache"] = False
    return rec


def rows_for(body: str, dsn: str = DSN, limit: int | None = None) -> list[dict]:
    import psycopg
    with psycopg.connect(dsn) as c:
        q = ("SELECT event_id, event_date, minutes_best_url, minutes_format, "
             "agenda_best_url, agenda_format FROM v_event_documents "
             "WHERE body_name ILIKE %s ORDER BY event_date DESC")
        rows = c.execute(q, (f"%{body}%",)).fetchall()
    out = []
    for eid, d, murl, mfmt, aurl, afmt in rows:
        # Minutes preferred — the agreed record of what was said. Agenda is the fallback,
        # and the basis is carried forward so no comparison silently mixes the two.
        if murl:
            out.append({"event_id": eid, "date": d, "url": murl,
                        "format": mfmt or "pdf", "basis": "minutes"})
        elif aurl:
            out.append({"event_id": eid, "date": d, "url": aurl,
                        "format": afmt or "pdf", "basis": "agenda"})
        else:
            out.append({"event_id": eid, "date": d, "url": None,
                        "format": None, "basis": "none"})
    return out[:limit] if limit else out


def run(body: str, limit: int | None = None, dsn: str = DSN) -> list[dict]:
    rows = rows_for(body, dsn, limit)
    have = [r for r in rows if r["url"]]
    print(f"[docs] {body}: {len(rows)} events, {len(have)} with a document "
          f"({sum(1 for r in have if r['basis']=='minutes')} minutes, "
          f"{sum(1 for r in have if r['basis']=='agenda')} agenda-only)")
    got, cached, failed = 0, 0, 0
    for i, r in enumerate(have, 1):
        d = fetch(r["url"], r["format"], r["basis"])
        r["text"] = d.get("text", "")
        r["chars"] = d.get("chars", 0)
        r["error"] = d.get("error")
        if d.get("error"):
            failed += 1
        elif d.get("from_cache"):
            cached += 1
        else:
            got += 1
            time.sleep(0.4)                       # be a good citizen against Legistar
        if i % 10 == 0 or i == len(have):
            print(f"  {i}/{len(have)}  fetched {got}, cached {cached}, failed {failed}",
                  flush=True)
    ok = [r for r in have if r["chars"] > 200]
    print(f"[docs] usable text for {len(ok)}/{len(have)} documents "
          f"(median {sorted(r['chars'] for r in ok)[len(ok)//2] if ok else 0:,} chars)")
    for r in have:
        if r.get("error"):
            print(f"  FAILED {r['date']} {r['basis']}: {r['error']}")
        elif r["chars"] <= 200:
            print(f"  THIN   {r['date']} {r['basis']}: only {r['chars']} chars extracted")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="fetch + extract meeting documents")
    ap.add_argument("--body", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    run(a.body, a.limit, a.dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
