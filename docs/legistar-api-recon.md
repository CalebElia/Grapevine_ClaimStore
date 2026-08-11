# Legistar Web API — Ann Arbor (`a2gov`) — VERIFIED

**Date:** 2026-07-28
**Method:** Direct HTTP against `https://webapi.legistar.com/v1/a2gov/`. ~90 requests.
**Status:** The API is reachable. Every item on the manual checklist in
`docs/v1-code-review.md` / `legistar-a2gov-notes.md` §7 is now answered.

Supersedes the "NOT VERIFIED" section of the prior recon, which could not reach the API.

---

## 1. Access

| Question | Answer |
|---|---|
| Base URL | `https://webapi.legistar.com/v1/a2gov/` |
| Client string | **`a2gov`** — confirmed. `annarbor` and `annarbormi` both return HTTP 500 `LegistarConnectionString setting is not set up in InSite for client: …` |
| Token required | **No.** Anonymous GET returns HTTP 200. |
| Record cap | **Confirmed at 1000.** An unfiltered `/events` query returns exactly 1000 rows and silently truncates. **Paginate with `$top`/`$skip` on every collection call** — an early count of "events since 2021" hit the cap and produced misleading per-body last-seen dates until filtered per body. |
| OData support | `$filter`, `$orderby`, `$top` all work. Dates as `datetime'2026-01-01'`. |

---

## 2. THE BLOCKING QUESTION — per-member votes

**Answer: YES. Per-member vote values are published, including dissent.**
`ARCHITECTURE-AND-ROADMAP.md` signals 1 (non-unanimous vote) and 2 (vote composition
shift) **survive. The contestation index does not need reweighting.**

Proof case — `/eventitems/221826/votes`, City Council 2026-05-18, `EventItemPassedFlag = 0`:

```
An Ordinance to Amend Chapter 55 (Zoning), Rezoning of 1.98 Acres from PUD…
action: Approved on First Reading    consent: 0    RESULT: FAILED

  Christopher Taylor    Nay        Chris Watson          Nay
  Erica Briggs          Nay        Dharma Akmon          Nay
  Lisa Disch            Yea        Jennifer Cornell      Nay
  Jen Eyer              Nay        Jon Mallek            Nay
  Travis Radina         Nay
  Cynthia Harrison      Nay
  Ayesha Ghazi Edwin    Nay
```

A failed ordinance with all eleven members named individually. That is exactly the
signal the index is built on.

### The nuance that would have misled a shallow check

Across 45 items sampled over 5 Council meetings:

| | |
|---|---|
| items returning vote **rows** | 45 |
| items with any non-null **value** | **14 (31%)** |
| total vote rows | 495 |
| rows with `VoteValueName = null` | **451 (91%)** |
| Yea / Nay / Absent | 22 / 10 / 12 |

**All 31 items with rows but no values were `EventItemConsent = 1`** — consent agenda,
bundled and passed without a roll call. Values appear on roll-call items.

So the endpoint returns HTTP 200 with a full member roster and *null values* on consent
items. A checker that tests "does `/votes` return rows?" concludes votes are available;
a checker that tests "are values populated?" on a consent item concludes they are not.
**Test `VoteValueName IS NOT NULL`, not row count.**

---

## 3. Body IDs — confirmed, and they are NOT the web-UI IDs

The prior recon recorded City Council as web UI ID **4166**. The API `BodyId` is **138**.
Two distinct ID spaces; do not mix them.

| BodyId | Active | Body | Events ≥2021 |
|---|---|---|---|
| **138** | 1 | City Council | **184** |
| **1385** | 1 | Sustainability Commission | **0 — see §4** |
| **220** | 0 | Energy Commission | 32 |
| **222** | 0 | Environmental Commission | 27 |
| 153 | 1 | City Planning Commission | 125 |
| 365 | 1 | Transportation Commission | 66 |
| 267 | 1 | Housing Commission | — |
| 223 | 1 | Greenbelt Advisory Commission | — |
| 191 | 0 | Cool Cities Task Force | — |

The ID ordering corroborates the merge independently of any description text: Energy (220)
and Environmental (222) are low and inactive; Sustainability (1385) is high and active.
→ two `body_lineage` rows.

**Corpus is larger than assumed.** City Council alone is 184 events since 2021 against a
roadmap estimate of 130–150 and an original working assumption of ~50 videos. Adding
Planning (125) and Transportation (66) puts the A2Zero-relevant set above 400 events.

---

## 4. ⚠ RESOLVED — the API only exposes events with a FINAL agenda

**The rule: `/events` returns an event only if its agenda status is `Final` or
`Final-revised`. Draft agendas do not exist to the API.**

Evidence — all 5,538 events, properly paginated:

| `EventAgendaStatusName` | count |
|---|---|
| Final | 4,669 |
| Final-revised | 869 |
| **Draft** | **0** |

Minutes status is *not* filtered the same way — `Draft` (1,304), `Final` (4,192),
`Final-revised` (40), `Not Viewable by the Public` (2) all appear. So the gate is on the
**agenda** specifically.

The Sustainability Commission's 2026-03-10 meeting — the one v1 processed —
`MeetingDetail.aspx?ID=1367374` reports:

> Meeting Name: Sustainability Commission · **Agenda status: Draft** · Meeting date/time:
> 3/10/2026 6:00 PM · **Minutes status: Draft**

That meeting is four months past and its agenda is still Draft. **This commission does not
finalize agendas, so none of its meetings will ever appear in the API.** Meanwhile
`Calendar.aspx` lists them normally (confirmed: 2026-10-13, 11-10, 12-08).

Contrast the predecessor bodies, which *are* in the API — Energy Commission 32 events,
Environmental Commission 27. Those clerks finalized. **This is a clerking-convention
dependency, not a data-model one** — exactly roadmap trap #10, "note which signals depend on
local clerking conventions."

### Consequences — the ingest needs both paths, and must reconcile them

1. **API path** for bodies that finalize agendas: City Council, Energy, Environmental,
   Planning, Transportation.
2. **HTML calendar path** for bodies that do not: Sustainability Commission — i.e. **the
   pilot body**. Not a fallback; required.
3. **A coverage reconciliation step is mandatory.** Counting events per body from the API and
   calling it complete would silently drop the entire pilot corpus while reporting success.
   Compare API counts against the calendar HTML per body and fail loudly on divergence.
4. S5's per-event `EventId`/`GUID` lookup must come from the calendar HTML for such bodies.

Corpus is also far larger than any prior estimate: **5,538 events, 51 bodies,
1990-10-15 → 2026-08-12.**

### Superseded note: the pilot body is invisible to the API

**`EventBodyId eq 1385` returns zero events.** Not a pagination artifact — a filtered
query returning an empty list.

Cross-checks:
- No event in the API has `Sustainab` anywhere in its body name.
- Only 3 events exist API-wide for 2026-03-09..12, none of them the Sustainability
  Commission meeting v1 processed on **2026-03-10**.
- Yet that meeting's agenda and minutes fetch fine from
  `a2gov.legistar.com/View.ashx?M=AADA&ID=1367374&GUID=…`, and v1 used them successfully.

So the meeting exists in Legistar's **web UI** and not in the **Web API**. Note also that
the `View.ashx` document id (1367374) is in a completely different range from API
`EventId`s (14156) — a third ID space.

**Consequences:**
1. Phase 1 ingest **cannot be API-only.** The HTML calendar scrape is not a fallback, it
   is required for commission coverage.
2. Energy (32 events) and Environmental (27) *are* in the API, so the predecessor bodies
   can be ingested normally — the gap is specific to the successor.
3. This is the `legistar-a2gov-notes.md` "body reorganization" trap in a sharper form: the
   body exists in `/bodies` with an active flag and has no events attached.
4. **Recheck before building S5's per-event URL lookup** — if commission events are absent,
   `EventId`/`GUID` for those meetings must come from the calendar HTML.

---

## 5. Field-level answers

| Field | Finding |
|---|---|
| `EventVideoPath` | **Empty on every event checked.** `EventVideoStatus = 'Public'` is set, but no URL. **Video links must come from the calendar HTML** (YouTube, per the prior recon). Unchanged conclusion, now confirmed from the API side. |
| Event end times | **Not present.** Only `EventTime` (start, e.g. `7:00 PM`). The anomalous-duration signal must use YouTube duration via `yt-dlp --dump-json`. |
| `EventInSiteURL` | Populated: `MeetingDetail.aspx?LEGID=14156&GID=55&G=<guid>`. **This is where S5 gets the `EventId` + `GUID` pair** for `View.ashx?M=AADA/MADA`. |
| `EventAgendaFile` | Populated — direct PDF URL on `a2gov.legistar1.com`. |
| `EventAgendaStatusName` / `EventMinutesStatusName` | Populated: `Final` / `Draft`. Use to avoid ingesting draft minutes as ground truth. |
| `EventItems` in `/events` | Always `[]`. Requires the separate `/events/{id}/eventitems` call. |
| `EventItemConsent` | Populated (0/1). **Load-bearing** — it is what distinguishes a null vote value from a missing one, and `pulled_from_consent` depends on it. |
| `EventItemMoverId` / `SeconderId` | Populated. → `event_items.mover_person_id`. |
| `EventItemTally` | **Never populated** (0 of ~90 items). Compute tallies from `/votes`. |
| `MatterHistoryId` | Equals the `EventItemId` for the same action; `/matters/{id}/histories` and `/events/{id}/eventitems` agree. Either path reaches `/votes`. |
| `MatterHistoryTally` | Null. Same as above. |

### `EventItemActionName` vocabulary (observed, 5 meetings)

```
Adjourn                     Approved as presented       Held and Closed
Adopted on Second Reading   Approved on First Reading   Received and Filed
Amended                     Approved with changes       Referred
Approved                    Approved as Amended
```

Postponement/referral signal: `Referred`, `Held and Closed`. Amendment signal: `Amended`,
`Approved as Amended`, `Approved with changes`. **Enumerate across the full window before
freezing** — the roadmap warns action text drifts as clerks change, and this is 5 meetings.

---

## 6. What this changes in the plan

| Plan item | Status |
|---|---|
| Verify `/eventitems/{id}/votes` (blocking) | ✅ **Done. Votes with values exist. Index unchanged.** |
| S5 per-event agenda/minutes URLs | ✅ Unblocked for Council via `EventInSiteURL`. ⚠ Blocked for commissions — see §4. |
| Contestation signals 1 & 2 | ✅ Survive |
| Signal 7 (anomalous duration) | ⚠ Confirmed: needs YouTube duration, not event times |
| `media_assets.url` | ⚠ Confirmed: HTML scrape only |
| Corpus size estimate | ⚠ Revise upward — 400+ relevant events, not ~50 |
| API-only ingest | ❌ **Not viable.** HTML path required. |

## 7. Still open

- [ ] Paginate a full event pull per body and reconcile against a calendar Excel export.
- [ ] Where do Sustainability Commission meetings live? (separate InSite instance? different
      body record?) — determines the commission ingest path.
- [ ] Enumerate `EventItemActionName` across the whole 5-year window, not 5 meetings.
- [ ] Are matter versions / text retrievable, or is amendment history only in histories?
- [ ] Attachment enumeration and whether correspondence appears there.
