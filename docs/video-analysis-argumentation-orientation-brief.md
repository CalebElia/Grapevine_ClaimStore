# Video Analysis — Argumentation Analysis — Orientation Brief

Three parts: **how we got to the current pipeline**, **where to start analysing**, and **what
we are building first**.

The goal this serves: understand how the argument over the **Bryant neighborhood
decarbonization project** was made, by whom, and how it changed — across City Council, the
Sustainability Commission, and the Energy and Environmental Commissions it replaced.

Date: 2026-08-10 · Supporting detail: `docs/asr-benchmark.md`, `docs/PLAN.md`

> Part 3 was rebuilt on **minutes text** rather than agenda item titles, after the original
> ranking was correctly challenged. What changed, and what did not, is recorded in place.

---

# Part 1 — What we tried, and why the pipeline looks like this

## The problem we started from

A v1 prototype extracted one 113-minute commission meeting into structured claims. Reviewing
its output found three blocking defects:

- **45% of turns carried another turn's extraction** — turn IDs were built by rounding start
  times into 10-second buckets, so 1,156 turns collapsed into 631 unique IDs.
- **71 turns with empty text received fabricated claims.**
- **5 of 18 speaker identities were invented.** A 20-minute clip was sent to a multimodal model
  and asked who was on screen; five speakers who first talk *after* minute 20 were confidently
  named from footage they never appear in, at `confidence: 1.0`. One first speaks at 86:48.

That last one shapes everything since. The failure mode we design against is not "the model is
wrong" — it is **"the model is confidently, plausibly, checkably wrong, and nothing errors."**

## What we tested

Seven transcription/diarization systems, two Ann Arbor meetings, five windows.

| System | Model | Speaker label per word? | Names speakers? |
|---|---|---|---|
| ElevenLabs Scribe | `scribe_v2` | yes | no |
| Deepgram | `nova-3` | yes | no |
| AssemblyAI | `universal-3-5-pro` | yes | no |
| Speechmatics | `enhanced` | yes | no |
| Rev.ai | `machine` | no | no |
| Google Gemini | `gemini-3.6-flash` | no | **yes** |
| incumbent | `whisperx` + `pyannote` | no | no |

**The architectural question, not the accuracy question, is what the benchmark was for.** Our
incumbent ran WhisperX for words and pyannote for speakers and then *joined them by timestamp*.
That join produced almost every defect: 38% of pyannote segments fell below the ~1.5s floor where
a speaker embedding carries information, 141 words straddled a speaker boundary and were assigned
by midpoint, and 67% of "interjections" carried no usable text. A vendor that labels every **word**
with a speaker has no join step and cannot fail that way.

Scoring used ground truth with explicit evidence tiers — `verified` (a human wrote the exact
words), `confirmed` (a human affirmed a machine proposal), `reference` (machine output,
human-corrected, **not gold**). Metrics: WER, entity accuracy, DER, cluster purity, welded-turn
rate, and a composite **estimated review burden**, since the objective is human minutes, not
accuracy.

## What we chose, and why

**ElevenLabs Scribe v2 — words, timestamps, word-level speaker.**
Recovered 14/14 speakers on one window and 12/12 on a Council roll call *with no hint*. Zero
welded turns, best cluster purity (0.968). 87× realtime against 1.5× for local WhisperX — which
turns a nine-day retrospective into hours.

**Google Gemini Flash — names and turn boundaries.**
The only system that produces names. More importantly it fixes boundaries *acoustics cannot*:
roll call is a **linguistic** pattern — the clerk calls a name, the next voice is that person.
Scribe filed three roll-call answers under the clerk; Gemini corrected all three, and split two
welded turns a human had found by ear.

**The join between them is arithmetic, not a model call.** Both systems transcribed the same
audio, so their word streams agree ~87–94%. Align the streams; each matched word casts one vote
linking a Scribe cluster to a Gemini name. Reproducible and inspectable, which a second LLM pass
would not be.

**Registries decide spelling.** Gemini writes *Kathari*, *Malik*, *Mazlumian*, *Nadrich*. Each
resolves against the roster, constrained by spoken title — without that constraint "Council Member
Malik" matches Mallika Kothari, a Youth Member, on string similarity alone. Verified failure,
twice.

### Why not the others

| | Why it lost |
|---|---|
| Deepgram | Found **2 speakers in a 12-person roll call**, and 3 of 14 on another meeting. No speaker-count parameter to fix it. |
| AssemblyAI | Its `speakers_expected` is a **constraint, not a hint** — give it 11 and it produces 11 clusters regardless (0.993 word agreement between hinted and unhinted runs). Only safe where the count is externally known. |
| Speechmatics | Holds speaker separation at length but **cannot spell**: 8/15 roster names vs Scribe's 14/15 (`Katari`, `Mazloomi Ann`, `Nadrich`, `Smith`, `Hans`). |
| Rev.ai | Rejects any custom-vocabulary phrase containing a digit — which **structurally excludes `A2Zero`**, the single most load-bearing term in this corpus. |

## Two non-obvious things that cost us time

**Chunking is mandatory, and not for upload size.** Scribe's speaker recall degrades with *both*
audio length and speaker-time imbalance, and they compound:

| recall = clusters found ÷ people present | balanced | lopsided |
|---|---|---|
| 8 min | 0.97 | 0.88 |
| 35 min | 0.82 | **0.53** |

The worst cell — long, with one dominant presenter — is what most meetings look like. We now
process in 8-minute chunks; recall on the failing window went **0.53 → 1.00**. Cluster IDs are
namespaced per chunk (`c03_speaker_1`) and *never* compared across chunks — **the join key is the
person's name**, which the Gemini pass supplies.

**Custom vocabulary was accepted and silently ignored in three encodings.** ElevenLabs `keyterms`
must be repeated multipart fields; a JSON array fails as "invalid characters" (the brackets), a
comma-joined string as "at most 4 spaces", and `keywords` / `prompt` / `biased_keywords` all
return HTTP 200 **and do nothing**. Verified A/B twice: without keyterms 0 of 2 `A2Zero` correct,
with them 2 of 2. Note also that **Scribe is non-deterministic** — identical requests return
different text, so any A/B needs repeat runs.

## The vocabulary loop — and one rule that matters

Running independent systems on the same audio and comparing them surfaces terms none of them
knows. Cross-checked against Legistar:

| systems produced | correct |
|---|---|
| `dish` / `disch` | **Lisa Disch** |
| `redina` / `rudina` | **Travis Radina** |
| `juskevich` / `jaskiewicz` | **Adam Jaskiewicz** |
| `courtland` / `cortland` | **Cortland Bersee** |

**In two of those the majority was wrong** — consensus chose `dish` over `disch`, and no system
produced `Radina` at all. So: **disagreement tells you which terms need an entry; the registry
tells you what they should be. Never let models vote on a name.** Every correct spelling was
already in Legistar before any audio was processed.

---

# Part 2 — Why we verify sections, not meetings

## The measurement that decided it

A human reviewed 35 minutes of the new pipeline's output, clocked:

| | old pipeline | new pipeline |
|---|---|---|
| review time | ~30 min | **27 min** |
| annotations needed | 47 | **23** |
| word accuracy | ~99% | ~99% |
| speech carrying a name | — | 92% |
| **turn-level attribution error** | — | **26%** |

Half the edits — but **the time barely moved**, because the reviewer watched the video either way.
Three attempts at a "safe to skip" confidence flag all failed; the best caught 54% of errors while
still requiring 65% of the audio to be watched. At a 26% base rate there is no dense pocket to
point a reviewer at.

## The asymmetry that gives us a way forward

The errors are **not evenly distributed across uses**:

- words are ~99% right
- attribution is wrong 26% of the time

Retrieval is a *text* operation. Attribution is what *claims* depend on. Therefore:

> **An unverified transcript is safe to search and unsafe to quote.**

So we index everything automatically, use a subject query to find the spans an argument actually
rests on, hand-check only those, and **gate publication** — a claim may be staged from unverified
ground but can never enter a rendered page from it. That reuses an existing trigger,
`require_curated_claim()`, which already blocks publication when a claim has no Subject.

Review effort then scales with analytical interest, not with hours of audio.

---

# Part 3 — Where to start

## What is in the database now

364 events, 17,781 agenda items, 10,171 votes, 261 videos, 1,771 persons.

| Body | Events | Agenda items | Videos | Range |
|---|---|---|---|---|
| City Council | 262 | 16,410 | 181 | 2020-01 → 2026-12 |
| Energy Commission | 46 | 696 | 35 | 2020-01 → 2025-12 |
| Environmental Commission | 39 | 675 | 31 | 2020-07 → 2025-12 |
| Sustainability Commission | 17 | **0** | 14 | 2025-08 → 2026-12 |

Energy and Environmental were **merged into** Sustainability in 2025 — and they overlapped four
months, so a 5-year retrospective needs all three or it silently drops the early record.

## Topic coverage for the Bryant project

An earlier version of this ranking searched **agenda item titles**. That was the weaker
instrument, and it has been redone against the **minutes** — the record agreed after the meeting.
All 330 available documents for the four bodies have now been fetched and text-extracted
(`pipeline/fetch_documents.py`, immutable cache, 2 failures out of 332 — both dead agenda PDFs).

Searching for *Bryant, geothermal, SEU, DTE, weatherization, Community Action Network,
electrification, heat pump, decarbonization, A2Zero, franchise, neighborhood-scale*:

| Body | Events | With text | Minutes / agenda | On topic | Share |
|---|---|---|---|---|---|
| **City Council** | 270 | 241 | 237 / 4 | **122** | 51% |
| **Sustainability Commission** | 20 | 15 | 15 / 0 | 14 | **93%** |
| Energy Commission | 46 | 38 | 10 / 28 | 18 | 47% |
| Environmental Commission | 40 | 36 | 9 / 27 | 4 | **11%** |

**Deprioritise Environmental Commission.** 11% coverage, 4 meetings, and its single richest
meeting hits only 2 terms. Confirmed on the better basis — this is not where the argument happens.

**Read the basis column before comparing bodies.** Energy and Environmental have minutes for only
about a quarter of their meetings, so most of their rows are scored on agendas. Agendas are far
shorter, which *inflates* the per-1,000-word density — the Energy Commission's top density scores
are all agenda rows. **Breadth (how many distinct terms appear) is comparable across bases;
density is not.**

### Does the basis change matter? Measured, for Council

`pipeline/basis_compare.py` scored all 229 Council meetings both ways:

| | agenda titles | minutes |
|---|---|---|
| meetings on topic | 92 | **117** (+27%) |
| total mentions | 282 | 482 |
| found **only** by this basis | **0** | **25** |

Spearman rank correlation **0.837** — the ordering is broadly stable, but minutes are a strict
superset. Structurally so: Legistar minutes reproduce every agenda title verbatim and *add* public
comment, communications and dispositions. The 25 extra meetings are ones where the subject arose
in public comment rather than in a scheduled item. The best of them, **2025-10-06** (Bryant,
geothermal, SEU), the title-based ranking missed entirely.

## The finding that matters more than the ranking

**Minutes do not contain the argument.** For the one meeting where we have both:

| Sustainability Commission 3/10/2026 | minutes | transcript |
|---|---|---|
| words | 498 | **18,045** |
| Bryant | 0 | 16 |
| geothermal | 0 | 31 |
| heat pump | 0 | 15 |
| DTE | 1 | 68 |
| franchise | 3 | 50 |
| **total topic mentions** | **5** | **203** |

These are *action minutes* — call to order, roll call, motions, votes. Council's are longer
(3,000–7,000 words) but structurally the same thing: agenda titles plus enactment numbers plus
dispositions. A 39-minute discussion of Bryant geothermal appears in the minutes as one line:
"City staff and DTE presented an update on the Sustainable Heating Franchise."

**So minutes and agendas are an index, not a source.** They tell you which video to transcribe.
They cannot answer a question about argumentation, and no ranking built on them should be
mistaken for one. This is the same conclusion Part 2 reached from the other direction.

## What minutes ARE good for: verified attendance

The roll call is the valuable content. `pipeline/rollcall.py` extracts it for **15 of 15**
Sustainability meetings, and every extraction is checked against the count the clerk wrote down —
a block whose parsed names disagree with its declared count is returned as unreliable rather than
as data.

That guard is not decorative. Legistar renders the same roll call in at least three layouts, and
the first implementation reported **15 present / 0 absent** for a meeting with 13 present and 3
absent — it swallowed the absent list. Marking an absent person present would license attributing
speech to someone who was not in the room.

Validated against the human-confirmed 3/10 registry:

- **13 of 13** roll-called-present commissioners were independently confirmed as speakers
- **zero** confirmed speakers appear on the absent list
- 9 further confirmed speakers are not on the roll call at all — staff (Missy Stults, Erin
  Donnelly) and public commenters, who are never roll-called

Perfect precision, partial recall (59%). Usable as a *gate*: a proposed name on the absent list is
strong evidence of misattribution, while absence from the roll call proves nothing.

## Recommended starting set

Ranked on minutes, video required, breadth first. `br` = distinct topic terms.

| Date | Body | br | Mentions | Video | Topics |
|---|---|---|---|---|---|
| 2026-06-01 | Council | **5** | 10 | `EMV-5kqtW9s` | A2Zero, decarbonization, Bryant, DTE, weatherization |
| 2025-08-07 | Council | 4 | **19** | `W2FohOlWKBk` | Bryant, decarbonization, weatherization, DTE |
| 2023-10-16 | Council | 4 | 12 | `TKbLDWzYM3w` | geothermal, Bryant, DTE, decarbonization |
| 2026-05-18 | Council | 4 | 9 | `W3Nc6-zVcdU` | SEU, DTE, Bryant, A2Zero |
| 2025-04-07 | Council | 4 | 8 | `kd9h-CwJMCI` | SEU, franchise, geothermal, A2Zero |
| 2025-02-03 | Council | 4 | 5 | `UBcMj3ojgug` | DTE, Bryant, decarbonization, A2Zero |
| 2024-10-07 | Council | 4 | 4 | `h4zH_Qm16SM` | Bryant, geothermal, DTE, franchise |
| 2024-03-04 | Council | 4 | 4 | `-HUnjluKjgQ` | geothermal, electrification, decarbonization, A2Zero |
| 2023-11-20 | Council | 3 | 11 | `H_pW_CU-D4E` | electrification, geothermal, Bryant |
| 2020-11-10 | Energy | 4 | 5 | `5TWf8wUd4XQ` | electrification, DTE, A2Zero, franchise |

Spans 2020 → 2026, which matters because the argument evolves: geothermal feasibility (2023–24) →
franchise negotiation (2024–25) → SEU and implementation (2025–26). The 2020 Energy Commission
meeting is the earliest framing and the only pre-2023 entry that survives on breadth.

**Three high-scoring meetings have no linked video** — 2022-09-06, 2023-09-26, and **2025-10-06**
(the best of the minutes-only finds). Worth checking whether the recording exists and simply is
not linked.

Plus **one already processed**: Sustainability Commission 3/10/2026 (`lWvRVUMyLP4`) — full
transcript, human-reviewed speaker registry, 39 minutes of Missy Stults on the DTE franchise and
Bryant geothermal. It is the reference implementation and the eval baseline.

## Three things to fix or work around first

**1. Sustainability Commission still has no agenda items.** Its events came in via HTML scraping,
not the Legistar API. The document fetch closes this for *search* — all 15 minutes are now cached
and extracted — but `event_items` remains empty, so any query that joins on agenda items silently
skips this body. The minutes text carries the item titles and Legistar file numbers
(`26-0407`, `26-0410`), so they can be parsed out.

**2. Two media-linkage defects — FIXED, `migrations/004_media_linkage.sql`.** Recorded here
because the *class* of defect is not fixed, only these two instances.

- `IkZ4APPWNgY` was attached to **2026-05-12** by Legistar's own calendar. Host metadata:
  7,426s, uploaded **2026-04-24**, titled *"…Meeting 4/14/26"*. It is the April 14 meeting,
  and it was *also* correctly attached to 2026-04-14 by our own weaker title-matching pass.
  **The city's assertion was the wrong one.** Ingested naively, every claim from that meeting
  is misdated by four weeks — and chronology is the join key across every source here.
- The real 5/12 recording, `bc7m_xHhLSo`, existed and had **never been ingested**. Root cause:
  the discovery pass matched titles in `M/D/YY` form, and CTN published this one as
  *"- May 12, 2026"*. Any future discovery must accept both forms.
- `2026-01-13` had **four** videos on one event. Not a multi-part recording — three are
  aborted streams of **59s, 42s and 1s** against the real 6,398s meeting. The selection query
  used for the first draft of the starting set picked the **42-second clip**.

The structural fix is `v_meeting_recordings`: one row per event, the longest recording,
with an `others` count exposed rather than hidden. **Read that view, never `media_assets`
directly, when you want "the video for this meeting."** No minimum-duration threshold — a
magic number would fail the first time a commission adjourns in six minutes.

**3. `media_assets.duration_seconds` was empty for all 261 videos — now backfilled**
(`pipeline/backfill_media.py`). Duration is what a review budget is built from. The corpus is
**724 hours across 256 fetchable videos**.

The same pass re-derives `title_stated_date` for every video, turning the hand audit above into
a mechanical one. Result across all 261: **zero title mismatches** — the 2026-05-12 defect was
the only one of its kind. Two things it did surface:

- **A third title format.** CTN has renamed its convention at least twice — `M/D/YY`,
  `- Month D, YYYY`, and the 2020-era `M-D-YY`. 34 videos were unverifiable until the third was
  added. Each format was discovered only *after* it hid something, so treat the list as
  incomplete and re-run `--reparse` (no network) whenever a new one appears.
- **Five 2020 Council videos are private or removed** — `6OlScqATpT8`, `f65MmJssZkQ`,
  `wZ-ImjyDb2I`, `ATGKcQx5Kjg`, `NW27cEF62lA`. Those meetings have minutes but no recoverable
  recording.

> **Read `date_verification` narrowly.** It means "the host's title agrees with this row's
> meeting date" — *not* that the video can be fetched. Those five removed videos have stored
> titles that parse and agree, so they read `matched`. The availability signal is
> **`duration_seconds IS NULL`**. Plan transcription work off duration, never off
> `date_verification`.

### One methodological warning, learned the hard way

**An acronym that is also a common word cannot be matched by shape.** `\bCAN\b` for Community
Action Network produced **570 false hits** across the Council corpus and ranked as the top term —
every one of them the ordinary verb inside the ALL-CAPS Zoom boilerplate that repeats on every
page ("PHONE CALLERS CAN PRESS \*9"). Case-sensitivity did not save it. It now matches the
spelled-out form only. Two meetings in the earlier recommended set were carried largely by that
false match. Audit the contexts of any short-token pattern before trusting a count.

---

# Part 4 — What we are building first

## The decision

**Build the prototype on the Sustainability Commission's back catalogue, end to end, before
touching Council.** Eleven meetings, all with video, all with minutes:

| | |
|---|---|
| meetings with video | 11 (2025-09 → 2026-07) |
| total runtime | **15.7 hours** (median 84 min) |
| already transcribed | 1 — 3/10/2026, human-reviewed |
| roll call verified | **11 of 11** |
| Scribe wall-clock for the rest | ~11 minutes at 87× realtime |

## Why this corpus, and not the higher-scoring Council meetings

**The real prize is testing whether the registry compounds.** We measured a 26% turn-level
attribution error. Verification makes that *safe*; it does not make it *smaller*. The only
mechanism that shrinks it is accumulating speaker names, keyterms and voiceprints across
meetings — and we have asserted that compounds without ever demonstrating it.

Eleven meetings of the **same ~16 commissioners, in the same room, in the same format** is the
cleanest possible test, and we start it holding 22 human-confirmed identities plus
checksum-verified attendance for every meeting. If error does not fall from meeting 1 to
meeting 11 here, it will not fall anywhere, and we will have learned that cheaply.

Council offers none of that: 181 videos, a rotating cast of public commenters, and no
validated roll call.

Secondary reasons: it is small enough to finish; the format is stable enough that a broken
stage is obviously broken rather than subtly wrong; and 3/10/2026 already gives us a
human-reviewed eval baseline to measure every change against.

## What this corpus is NOT

**It is not sufficient for the full proof of concept, and we should not let a clean result
here be read as "we have the Bryant argument."**

The Sustainability Commission covers **eleven months, and only the implementation phase**. The
contested phases happened before this body existed — geothermal feasibility (2023–24) and
franchise negotiation (2024–25) took place in Council and in the Energy and Environmental
Commissions that were merged into it in 2025. What we will capture here is the body *reporting
on* Bryant, largely after the arguments were settled elsewhere.

So the full POC needs Council video, and probably Energy Commission video, both of which are
already ranked in Part 3. This tranche buys a **working system**, not a complete corpus.

## The pipeline for this tranche

```
  1  transcribe + diarize      ElevenLabs Scribe v2, 8-min chunks
  2  name speakers             Gemini pass + deterministic alignment + registry
                               presence-gated on verified roll call
  3  segment on the AGENDA     minutes give the item sequence; the meeting follows it
  4  find analysis-relevant    search the TRANSCRIPT text for the subject
     segments
  5  verify only those spans   scoped review workbook, existing round-trip
  6  exploratory analysis      on verified spans only — decide what outputs are worth
                               producing once we can see the raw material
```

**Step 3 is the design decision worth stating explicitly: segment on the agenda, not
semantically.** The meeting proceeds through its agenda in order, and the minutes record that
order with Legistar file numbers. That is a *symbolic* scaffold — consistent with the standing
rule that **embeddings propose and symbols decide**. Semantic topic segmentation is harder,
unbuilt, and unvalidated; reach for it only where the agenda anchor fails.

**Step 4 must run on the transcript, not on the agenda or minutes.** This is the operating
principle that falls out of Part 3:

> **Agendas and minutes give you signal, not substance.** They tell you *which* meeting and
> *which* item — they cannot tell you what was argued. The 3/10 minutes record 5 topic mentions
> where the transcript holds 203, and record **zero** mentions of Bryant, geothermal, or heat
> pumps in a meeting that spent 39 minutes on them.

So minutes route us to the right video and the right agenda item; only the transcript can
answer a question about argumentation. Any analysis built on minutes alone is measuring the
clerk, not the commission.

## One sequencing rule

**Probe before batching.** We do not actually know that Bryant is discussed across these
eleven meetings. The evidence is mixed: a "Building Decarbonization" work group reports in
nearly every meeting and is renamed "Residential Neighborhood Decarbonization" by 2026-07 — a
continuous thread. But it is one of four work groups, alongside Returnable Containers,
Sidewalk Gap Filling and Water Quality; and our one rich data point, 3/10, had **DTE
presenting**, which may make it the outlier rather than the norm.

Transcribe two or three, measure subject density in the transcripts, then commit to the rest.
At 87× realtime that probe costs minutes, and it is the difference between discovering the
corpus is thin now versus after eleven full runs.

---

## Where the code is

```
pipeline/vocab.py            per-meeting keyterm list (registry + roster + agenda)
pipeline/transcribe_v2.py    Scribe v2, 8-min chunks   (--chunked)
pipeline/naming.py           Gemini + deterministic alignment + registry resolution
pipeline/turns_v2.py         turns from word-level speakers, boundary flags
pipeline/export_review_v2.py review workbook, tiered by confidence
pipeline/import_review.py    corrections back in; voiceprint enrollment
pipeline/bench/              the vendor benchmark harness (re-runnable)
pipeline/fetch_documents.py  minutes/agenda fetch + text extract, immutable cache
pipeline/topic_scan.py       rank meetings by topic, from the record (breadth + density)
pipeline/basis_compare.py    does minutes-basis ranking differ from agenda-title basis?
pipeline/rollcall.py         checksum-validated attendance from minutes
pipeline/backfill_media.py   host metadata + mechanical meeting<->video linkage audit
migrations/                  ordered, applied-once SQL (004 = media linkage)
registries/ann_arbor/        asr_vocabulary.json · speaker_registry.json · body rosters
docs/asr-benchmark.md        full evidence for Part 1
```

164 tests passing. `pytest tests/` builds a throwaway Postgres and applies the full DDL every run.
