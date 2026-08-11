"""Legistar Web API client for Ann Arbor (`a2gov`).

Recon findings this client is built around — see docs/legistar-api-recon.md:

1. NO TOKEN NEEDED. Anonymous GET returns 200. Client string is `a2gov`; `annarbor`
   and `annarbormi` both return HTTP 500.

2. THE 1000-RECORD CAP IS SILENT. An unpaginated `/events` returns exactly 1000 rows
   and truncates with no error, no header, no warning. During recon this produced
   plausible-looking but wrong per-body "last event" dates until it was caught. Every
   collection read here goes through `paginate()`, which keeps requesting until a
   short page arrives. Never call `get()` on a collection you expect to be large.

3. THE API ONLY EXPOSES EVENTS WITH A FINAL AGENDA. Across all 5,538 events,
   EventAgendaStatusName is only ever 'Final' or 'Final-revised' — zero drafts. The
   Sustainability Commission never finalizes agendas, so none of its meetings appear,
   including the one the v1 pipeline processed. `agenda_status_coverage()` exists to
   make that measurable rather than discovered later.

4. PER-MEMBER VOTE VALUES ARE PUBLISHED, but 91% of vote ROWS have a null
   VoteValueName — all of those on consent-agenda items. Test values, not row counts.
   `votes(..., values_only=True)` is the default for that reason.

Raw responses are cached verbatim with URL and fetch timestamp, per the roadmap's
immutable-raw-layer principle: the derived layer must be reproducible by re-parse
without re-fetching.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

BASE = "https://webapi.legistar.com/v1"
DEFAULT_CLIENT = "a2gov"

# Confirmed empirically: a collection request returns at most 1000 rows.
PAGE_SIZE = 1000

# Politeness. The API is public and unauthenticated; there is no published rate limit,
# so this is deliberately conservative rather than tuned.
MIN_INTERVAL_S = 0.15

# Ann Arbor body ids, confirmed 2026-07-28. NOTE these are API BodyIds and are NOT the
# web-UI ids — City Council is 138 here and 4166 in the InSite HTML. Three distinct id
# spaces exist in this deployment (API BodyId, web-UI body id, View.ashx document id).
BODY_IDS = {
    "city_council": 138,
    "sustainability_commission": 1385,   # active, but ZERO events in the API — see note 3
    "energy_commission": 220,            # inactive predecessor
    "environmental_commission": 222,     # inactive predecessor
    "planning_commission": 153,
    "transportation_commission": 365,
    "housing_commission": 267,
    "greenbelt_advisory_commission": 223,
}

A2ZERO_RELEVANT_BODIES = [
    BODY_IDS["city_council"],
    BODY_IDS["sustainability_commission"],
    BODY_IDS["energy_commission"],
    BODY_IDS["environmental_commission"],
    BODY_IDS["planning_commission"],
    BODY_IDS["transportation_commission"],
]


class LegistarError(RuntimeError):
    pass


@dataclass
class FetchStats:
    requests: int = 0
    cache_hits: int = 0
    rows: int = 0
    truncation_guards: int = 0
    errors: list[str] = field(default_factory=list)


class LegistarClient:
    """Read-only client with an immutable raw cache.

    cache_dir layout:
        <cache_dir>/<sha1-of-url>.json   {"url":…, "fetched_at":…, "body":…}

    Content-addressed by URL so a re-parse never needs the network, and so two callers
    requesting the same page share one stored response.
    """

    def __init__(
        self,
        client: str = DEFAULT_CLIENT,
        cache_dir: Path | str | None = None,
        offline: bool = False,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.stats = FetchStats()
        self._last_request = 0.0

    # ---------------------------------------------------------------- internals
    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{BASE}/{self.client}/{path.lstrip('/')}"
        if params:
            # OData keys start with '$'; quote values but keep the key literal.
            # Single quotes and parens are OData syntax and must survive quoting —
            # e.g. $filter=EventDate ge datetime'2021-01-01'
            safe = "',()"
            qs = "&".join(
                f"{k}={urllib.parse.quote(str(v), safe=safe)}"
                for k, v in params.items()
                if v is not None
            )
            url = f"{url}?{qs}"
        return url

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()}.json"

    def _fetch(self, url: str) -> Any:
        cp = self._cache_path(url)
        if cp and cp.exists():
            self.stats.cache_hits += 1
            return json.loads(cp.read_text())["body"]
        if self.offline:
            raise LegistarError(f"offline and not cached: {url}")

        wait = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        # Retry transport failures. A long ingest issues thousands of requests and the
        # server resets connections under sustained load; one reset should not discard
        # the run. HTTP errors (4xx/5xx) are NOT retried — those are answers, not faults.
        raw = None
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = json.loads(exc.read().decode()).get("ExceptionMessage", "")[:200]
                except Exception:
                    pass
                self._last_request = time.monotonic()
                raise LegistarError(f"HTTP {exc.code} for {url} — {detail}") from exc
            except Exception as exc:                       # noqa: BLE001
                last_err = exc
                self.stats.errors.append(f"{url}: attempt {attempt + 1} — {exc}")
                time.sleep((2 ** attempt) * 2)
            finally:
                self._last_request = time.monotonic()
        if raw is None:
            raise LegistarError(f"failed after 4 attempts: {url}") from last_err

        self.stats.requests += 1
        body = json.loads(raw)
        if cp:
            # Immutable raw layer: URL + fetch timestamp travel with the payload.
            cp.write_text(json.dumps(
                {"url": url,
                 "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "body": body},
                indent=None,
            ))
        return body

    # ------------------------------------------------------------------- public
    def get(self, path: str, **params) -> Any:
        """Single request. Use only where the result is known to be small."""
        return self._fetch(self._url(path, params))

    def paginate(self, path: str, order_by: str | None = None, **params) -> Iterator[dict]:
        """Yield every row, defeating the silent 1000-row cap.

        An explicit $orderby is strongly preferred: without a stable sort, $skip
        paging over a changing result set can drop or repeat rows.
        """
        skip = 0
        seen = 0
        while True:
            page = self._fetch(self._url(path, {
                **params,
                "$orderby": order_by,
                "$top": PAGE_SIZE,
                "$skip": skip,
            }))
            if not isinstance(page, list):
                raise LegistarError(f"expected a list from {path}, got {type(page).__name__}")
            yield from page
            seen += len(page)
            self.stats.rows += len(page)
            if len(page) < PAGE_SIZE:
                return
            self.stats.truncation_guards += 1
            skip += PAGE_SIZE
            if skip > 500_000:                      # runaway guard
                raise LegistarError(f"pagination exceeded 500k rows on {path}")

    # ----------------------------------------------------------- domain reads
    def bodies(self) -> list[dict]:
        return list(self.paginate("bodies", order_by="BodyId"))

    def persons(self) -> list[dict]:
        return list(self.paginate("persons", order_by="PersonId"))

    def events(self, body_id: int | None = None, since: str | None = None) -> list[dict]:
        """Events, optionally scoped to a body and/or a start date (YYYY-MM-DD).

        REMINDER: only events with a Final/Final-revised agenda are returned at all.
        A body that does not finalize agendas yields zero rows and no error.
        """
        clauses = []
        if body_id is not None:
            clauses.append(f"EventBodyId eq {body_id}")
        if since:
            clauses.append(f"EventDate ge datetime'{since}'")
        flt = " and ".join(clauses) if clauses else None
        return list(self.paginate("events", order_by="EventId", **{"$filter": flt}))

    def event_items(self, event_id: int) -> list[dict]:
        """Agenda items for one event. `EventItems` is always [] on the events
        endpoint itself, so this separate call is required."""
        return self.get(f"events/{event_id}/eventitems",
                        **{"AgendaNote": 1, "MinutesNote": 1, "Attachments": 0})

    def votes(self, event_item_id: int, values_only: bool = True) -> list[dict]:
        """Per-member votes for one event item.

        values_only=True (default) drops rows whose VoteValueName is null. 91% of rows
        are null, all on consent-agenda items, and they carry a member roster with no
        vote information. Keeping them would make a consent item look like a recorded
        roll call.
        """
        rows = self.get(f"eventitems/{event_item_id}/votes")
        if not isinstance(rows, list):
            return []
        if values_only:
            rows = [r for r in rows if r.get("VoteValueName") is not None]
        return rows

    def matter_histories(self, matter_id: int) -> list[dict]:
        return self.get(f"matters/{matter_id}/histories")

    # ------------------------------------------------------------- diagnostics
    def agenda_status_coverage(self, body_ids: list[int]) -> dict[int, dict]:
        """Per body: how many events the API exposes, and their agenda statuses.

        This is the reconciliation hook. A body returning zero rows is NOT evidence
        that it holds no meetings — the Sustainability Commission holds monthly
        meetings and returns zero here. Compare against the calendar HTML before
        treating any per-body count as coverage.
        """
        out: dict[int, dict] = {}
        for bid in body_ids:
            evs = self.events(body_id=bid)
            statuses: dict[str, int] = {}
            for e in evs:
                k = str(e.get("EventAgendaStatusName"))
                statuses[k] = statuses.get(k, 0) + 1
            out[bid] = {
                "events": len(evs),
                "agenda_statuses": statuses,
                "earliest": min((e["EventDate"][:10] for e in evs), default=None),
                "latest": max((e["EventDate"][:10] for e in evs), default=None),
                "api_invisible_suspected": len(evs) == 0,
            }
        return out
