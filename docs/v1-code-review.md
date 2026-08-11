# V2 Implementation Plan — Video → Claim Store

**Written:** 2026-07-24, from a code review of the v1 prototype.
**Reviewed:** `video_analysis/` at `Coding Projects/video_analysis/` — `run_pipeline.py`, `pipeline/v0`–`v8`,
`schema/video_output_schema.json`, `registries/`, `docs/prototype_run_1_postmortem.md`,
`docs/improvement_backlog.md`, `pipeline.log`, and all cached intermediates for `lWvRVUMyLP4`.
**Companion to:** `pipeline-v1-review.md` (which reviewed the JSON output). This reviews the code.

**Note on file location.** The task named `grapevine/docs/v2-plan.md`. The only `grapevine/`
directory on disk is `Shared Repo_Cloan/grapevine`, which is the A2Zero wiki/RAG repo and has no
`docs/`. Putting this there would orphan it from every document it cites, so it lives here
alongside `pipeline-v1-review.md`, which it directly answers. Move it if that was wrong.

---

## 0. Executive summary — what the code changes

Three findings, in descending order of importance. Only the third was anticipated.

**1. 45% of v1's argumentation is attached to the wrong utterance.** `make_segment_id()` rounds
start times into 10-second buckets. 1,156 turns produced only 631 unique IDs. V7 keys its results
in a dict by that ID, so collisions silently overwrite; V8 then looks up by the same ID and serves
one extraction to as many as 7 different turns. This is not in the postmortem or the improvement
backlog. **v1's analytical output is not migratable and should not be treated as data.**

**2. The "median 2-second utterance" is a bug, not a fact about speech.** V4 never merges
consecutive same-speaker segments. 1,198 diarization segments became 1,156 "turns" — a 3.5%
reduction. 66% of adjacent diarization pairs are the same speaker. Proper coalescing yields ~438
turns. This is **new evidence** that revises a premise in `HANDOFF.md` and `claim_store.sql`: the
73%-under-5s figure describes diarization fragments, not utterances. The tiering decision is still
correct (not relitigating it), but the fix ordering changes — **fix the merge before building the
tier gate**, because tiering a shredded stream tiers the wrong units.

**3. Q1 confirmed.** The response schema forces identical object shape on every segment, at three
independent enforcement points. Better prompting could not have fixed it: `v8_assemble.py` would
have raised `ValidationError`.

**Q2 answered: (a).** The agenda text and the minutes text were both fetched over HTTP and pasted
into the segmentation prompt. Proven, not inferred — two Legistar file numbers appear in the
section output and appear nowhere in the transcript. **Segmentation is substantially solved and
the roadmap's planned monotonic-DP aligner should be dropped.** Phase 4 shortens by an estimated
2–3 weeks. Three caveats in §2.4; one is a hardcoded URL that makes the current code work for
exactly one meeting per body.

**Cost.** v1 spent 37.9M video tokens on an extraction pass whose only video-dependent outputs
were `tone` and `demeanor` — the fields being scrapped. Removing video from extraction, not
tiering, is the dominant saving. Modeled v2: **$0.79–1.71 per 2-hour meeting** vs. $7–8 for a
clean v1 run and $14.50 actually spent. §5.

---

## Q1. Does the response schema force the same object shape on every segment?

**Yes. Confirmed, at three enforcement points, with the hard constraint being the validator rather
than the prompt.**

### Enforcement point 1 — the JSON Schema `required` arrays (the binding constraint)

`schema/video_output_schema.json:85-94`:

```json
"segment_breakdown": {
  "type": "array",
  "items": {
    "type": "object",
    "required": [
      "segment_id", "segment_type", "speaker_id", "constituency_group",
      "start_timestamp", "end_timestamp", "speaking_duration_seconds",
      "verbatim_text", "interjections", "audio_visual_context",
      "argumentation", "responds_to_segment_id", "citations"
    ],
```

And the nested objects, `schema/video_output_schema.json:116-134`:

```json
"audio_visual_context": {
  "type": "object",
  "required": ["tone", "demeanor", "inference_source"],
  ...
},
"argumentation": {
  "type": "object",
  "required": ["core_claim", "stance_on_topic", "evidence_provided", "framing_used"],
  "properties": {
    "core_claim": {"type": "string"},
    "stance_on_topic": {"type": "string", "enum": ["Support", "Oppose", "Neutral", "Unclear"]},
    "evidence_provided": {"type": "string"},
    "framing_used": {"type": "string"}
  }
},
```

There is no `if`/`then`, no `oneOf`, no `dependentSchemas` — nothing keyed on
`speaking_duration_seconds`, on `segment_type`, or on any tier field. Every segment in the array is
validated against the identical shape. `argumentation` and `audio_visual_context` are unconditionally
required, and their own sub-fields are unconditionally required and typed `"string"` (not
`["string", "null"]` — contrast the section-level analytical fields at `:47-52`, which *are*
nullable). A segment carrying `"argumentation": null` is invalid.

**This is why prompting could not have fixed it.** `pipeline/v8_assemble.py:124-127`:

```python
def validate_output(output: dict) -> None:
    """Validate final JSON against schema. Raises jsonschema.ValidationError if invalid."""
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(instance=output, schema=schema)
```

Called at `v8_assemble.py:196` before the write. A tiered output — one that omitted argumentation on
roll call — would have crashed the pipeline. The postmortem (`docs/prototype_run_1_postmortem.md:18`)
records this validator as a win, which it was for catching malformed enums. It was also the wall.

### Enforcement point 2 — the assembler synthesizes the shape when the model doesn't

`pipeline/v8_assemble.py:51-60`, the `else` branch of `build_segment()`:

```python
    else:
        arg_obj = {
            "core_claim": "",
            "stance_on_topic": "Unclear",
            "evidence_provided": "",
            "framing_used": "",
        }
        av_context = {"tone": "", "demeanor": "", "inference_source": "missing"}
        responds_to = None
        citations = []
```

Uniformity is guaranteed downstream of the model. Even a deliberately skipped turn receives the
full object skeleton. Whatever V7 does or doesn't return, the output shape is fixed.

### Enforcement point 3 — the prompt forbids nulls with sentinel strings

`pipeline/v7_argumentation.py:164-178` is a literal template, not a schema handed to the API:

```python
    Return a JSON array with one object per turn, in the same order:
    [
      {{
        "segment_id": "seg_xxxxxxxx",
        "core_claim": "The central argument in one sentence",
        "stance_on_topic": "Support | Oppose | Neutral | Unclear",
        "evidence_provided": "What evidence or reasoning did they cite? If none, say 'No specific evidence cited.'",
        "framing_used": "The narrative frame used — how they positioned the issue rhetorically",
        "tone": "2-3 words describing vocal tone (e.g., Urgent, Persuasive | Cautious, Warning)",
        "demeanor": "1-2 sentences on body language, eye contact, gestures as observed in video",
        "inference_source": "multimodal_gemini",
        "responds_to_segment_id": "seg_xxxxxxxx if speaker explicitly references a prior speaker's argument, else null",
        "citations": []
      }}
    ]
```

Read `evidence_provided` closely: **`If none, say 'No specific evidence cited.'`** The prompt does not
merely permit uniform depth, it mandates a non-null string where the honest answer is absence.
Measured in the output: 514 of 1,157 segments (44%) carry that sentinel. `core_claim` has no null
path at all. `responds_to_segment_id` is the *only* field given an explicit `else null` — and it is
the only field that comes out sparse (451/1,157, 39%). That contrast is the cleanest evidence in
the codebase that the model does what the template tells it.

`citations` appears as `[]` with no instruction anywhere about what belongs in it. 0/1,157
populated. The dead field was dead by omission.

### Was this a bug or the design?

The design. `docs/superpowers/plans/2026-05-06-video-discourse-pipeline.md:2094` specifies
per-turn extraction of `core_claim, stance_on_topic, evidence_provided, framing_used, tone,
demeanor, responds_to_segment_id, citations` for every turn from V4. Tiering was never designed
away — it was never designed in. Grepping the spec and plan for `tier`, `substantive`, or
`skip` returns nothing relevant.

### One additional finding: there are two schemas and they disagreed

The JSON Schema is never sent to the model. `GenerativeModel` is instantiated bare at
`v7_argumentation.py:139` — no `response_schema`, no `response_mime_type="application/json"`, and
the same is true at `v3:108`, `v5:114`, `v6:54`. Every call parses free text and strips markdown
fences by regex (`v7:187-190`). The postmortem records the predictable consequences
(`prototype_run_1_postmortem.md:71-84`): `stance_on_topic` came back `"Informative"`, and
`citations` came back as an array of URL strings. Both were papered over with coercion in V8
rather than constrained at the API.

**For v2: use the provider's structured-output mode.** A schema the model is actually bound by,
with a tier discriminator in it, replaces both the prompt template and half of V8's coercion logic.

### Verdict on the hypothesis

Confirmed, and the prescribed fix — two-pass classify-then-gated-extract — is right. But the
hypothesis is *incomplete*, and if v2 implements only the two-pass design it will produce cheaper
output that is still wrong. §1 is why.

---

## 1. The two findings the hypothesis missed

### 1.1 `segment_id` collides. 45% of v1's argumentation is misattributed.

`pipeline/utils.py:17-20`:

```python
def make_segment_id(video_id: str, speaker_id: str, start_sec: float) -> str:
    """Deterministic section ID from video_id, speaker, and start timestamp (rounded to 10s)."""
    key = f"{video_id}_{speaker_id}_{int(round(start_sec / 10) * 10)}"
    return "seg_" + hashlib.sha256(key.encode()).hexdigest()[:8]
```

The 10-second rounding was for **re-run stability** — the postmortem lists "Stable section_id and
segment_id on re-run" as a success criterion (`prototype_run_1_postmortem.md:174`). It achieves
that. It also makes the ID non-unique: any two turns by the same speaker starting inside the same
10-second bucket collide. Given finding 1.2 — 2-second fragments — collisions are not an edge case,
they are the norm.

Measured on `lWvRVUMyLP4`:

| | |
|---|---|
| V4 turns | 1,156 |
| Unique `segment_id`s | **631** |
| Colliding ID groups | 316 |
| Largest group | **7 turns sharing one ID** |
| Turns receiving another turn's extraction | **525 (45%)** |
| Entries in `v7_argumentation.json` | **630** |

The propagation path:

- `v7_argumentation.py:294-297` — results are stored in a dict keyed by the ID, so within a run
  the last write wins and earlier extractions are discarded:
  ```python
  for result in results:
      sid = result.get("segment_id")
      if sid:
          argumentation_by_segment[sid] = result
  ```
- `v8_assemble.py:88` — assembly looks up by the same key: `arg = argumentation_map.get(turn["segment_id"])`

So 630 extractions were fanned out across 1,157 segments. This also resolves a discrepancy nobody
chased: the postmortem claims "630 segments" with multimodal argumentation
(`prototype_run_1_postmortem.md:171`) while the shipped JSON has 1,156 populated. Both numbers are
correct. 630 were extracted; 1,156 were served.

A concrete case from the output — `seg_469efb03`, shared by four segments, all four carrying the
same `core_claim`:

```
core_claim (identical for all four): "The staff member calls on Chair Curtis to continue the roll call."
  [00:00:55  1s] 'Thank you. Council Member Cornell?'
  [00:00:56  1s] 'Thank you. Council Member Cornell? Here. Word by Ann Arbor.'
  [00:01:00  0s] 'Thank you. Chair Curtis?'
  [00:01:02  1s] 'Thank you. Chair Curtis?'
```

Related damage: 36 segments have `responds_to_segment_id` equal to their own `segment_id`, and 4
point at IDs that do not exist in the output. The dialogue graph that `pipeline-v1-review.md:105`
credits as "already built" is built on colliding keys.

**Fix (trivial, and do it first):** make the ID a function of the exact boundary, and stop making
identity depend on a float.

```python
def make_utterance_key(media_asset_id: str, start_ms: int, end_ms: int, sequence: int) -> str:
    return f"{media_asset_id}:{sequence:05d}:{start_ms}-{end_ms}"
```

Re-run stability is real but should be earned by determinism of the *upstream* pipeline (same
audio → same diarization → same sequence numbers), not by discarding precision in a primary key.
In the target schema this is moot: `utterances.id` is a `BIGSERIAL` and `(segment_id, sequence)`
is the natural key. Add a `UNIQUE (media_asset_id, sequence)` constraint so a collision is a
database error rather than a silent overwrite.

### 1.2 V4 does not merge turns. The 2-second median is an artifact.

`pipeline/v4_merge_turns.py:75-106` — the merge loop:

```python
    while i < len(enriched):
        seg = enriched[i]
        is_short = seg["duration"] < interjection_max_sec

        if is_short:
            # Short segment not yet absorbed — treat as standalone primary turn
            turns.append(_build_turn([seg], transcript_segments, video_id))
            i += 1
        else:
            # Long segment: start a primary turn for this speaker
            primary_speaker = seg["speaker_id"]
            turn_segs = [seg]
            j = i + 1

            # Look ahead: absorb exactly ONE sandwiched interjection + continuation.
            if (
                j < len(enriched)
                and enriched[j]["duration"] < interjection_max_sec
                and enriched[j]["speaker_id"] != primary_speaker
                and j + 1 < len(enriched)
                and enriched[j + 1]["speaker_id"] == primary_speaker
            ):
```

The only merge condition is the interjection sandwich: A-speaks, B-interjects-briefly, A-resumes.
**There is no clause that coalesces consecutive segments from the same speaker.** A commissioner
talking for three minutes with breathing pauses becomes N separate turns, because pyannote emits a
segment per speech burst.

Measured:

| | |
|---|---|
| V2 diarization segments | 1,198 |
| V4 turns | 1,156 |
| Reduction | **3.5%** |
| Adjacent diarization pairs with the same speaker | **794 (66%)** |

Simulating a proper same-speaker coalescing pass over the cached diarization:

| Gap tolerance | Turns | Median | ≤5s |
|---|---|---|---|
| v1 (none) | 1,156 | 2.0s | 849 (73%) |
| ≤0.5s | 848 | 2.6s | 556 (66%) |
| ≤1.0s | 576 | 2.1s | 372 (65%) |
| **≤2.0s** | **470** | **2.0s** | **307 (65%)** |
| ≤3.0s | 436 | 2.1s | 273 (63%) |

Real fragments from the 54-minute Sustainable Heating Franchise section — same speaker,
consecutive, each one a separate "turn" that received its own full argumentation object:

```
[1s] Missy_Stults_OSI   ''
[0s] Missy_Stults_OSI   'What does a franchise, what does it mean?'
[1s] Missy_Stults_OSI   "Give you a sense of where we were and a sense of where we're headed."
[3s] Missy_Stults_OSI   "So let's start with what a franchise is. And this also eliminates what it's not."
[1s] Missy_Stults_OSI   "And this also eliminates what it's not."
[3s] Missy_Stults_OSI   'Traditionally, these are pretty cut and clear.'
```

Two further defects fall out of this, both measured:

**Text duplication.** `collect_text()` (`v4_merge_turns.py:26-33`) gathers any transcript segment
*overlapping* `[start, end]`. Adjacent fragments overlap the same Whisper segments, so text is
emitted repeatedly. Note lines 4 and 5 above: the identical sentence appears inside one turn and
again as the next turn.

- 516 of 1,155 adjacent turn pairs (**44.6%**) have one turn's text fully contained in its neighbour's
- Sum of turn `verbatim_text` is **1.79×** the transcript's total character count

**Empty turns that got invented claims.** 71 turns (6.1%) have empty `verbatim_text` — diarization
detected voice activity where Whisper transcribed nothing. All 71 reached the output with a
populated `core_claim`:

```
0s | ""  →  "The speaker non-verbally acknowledges the previous speaker's response."
0s | ""  →  "He is present for the roll call, calling from Ann Arbor's second ward."
0s | ""  →  "The speaker is present for the meeting."
```

Note the second one. Given no text whatsoever, the model produced a specific factual assertion
about which ward a person represents. **Hallucination rate on empty input: 100% (71/71).** This is
the strongest single piece of empirical support for `verbatim TEXT NOT NULL` anywhere in the
project, and it comes from the code that predates the requirement. Keep it, and add the
complementary constraint: `CHECK (length(trim(verbatim)) > 0)`, because `NOT NULL` alone accepts
`''`, which is exactly what V8's `else` branch would have written.

### 1.3 What this does to the tiering premise — new evidence, stated explicitly

`HANDOFF.md:39-41` and `claim_store.sql:10-12` both assert that 73% of *utterances* in a real
meeting are under 5 seconds, and build the extraction economics on it. **The code shows that
figure measures diarization fragments.** Corrected: after coalescing at a 2-second gap, ~65% of
438 real turns are under 5 seconds, and the absolute count of short turns falls from 849 to ~307.

Three consequences:

1. **The tiering decision stands.** 65% is still most of the corpus, roll call is still 46
   fragments of nothing, and gating expensive work on a cheap classifier is still right. I am not
   relitigating it.
2. **The attributed cause was wrong, so the fix ordering was wrong.** Tiering a fragmented stream
   assigns tiers to fragments. Fix the merge first; then the tier gate operates on units that can
   actually hold a claim.
3. **The savings arithmetic changes.** The handoff implies tiering is where the economics live.
   The measured picture is different: fixing the merge alone is a 2.5× reduction in extraction
   units, tiering is a further ~1.8×, and removing video from the extraction pass is ~40×. Video
   removal dominates both by more than an order of magnitude. §5.

---

## Q2. How did v1 produce 12 agenda-aligned sections with no index points?

**Answer (a): the agenda text and the minutes text were fetched over HTTP and pasted into the
prompt. YouTube chapters were empty and played no role.**

### The code path

`run_pipeline.py:78` → `pipeline/v5_segment.py`.

In `main()`, `v5_segment.py:197-213`:

```python
    # Attempt to fetch official agenda
    agenda_text = None
    agenda_url = format_agenda_url(body_registry.get("agenda_url_pattern", ""), metadata)
    if agenda_url:
        print(f"[V5] Fetching agenda from {agenda_url}...")
        agenda_text = fetch_document_text(agenda_url)
        ...
    # Attempt to fetch meeting notes/minutes
    minutes_text = None
    minutes_url = format_agenda_url(body_registry.get("minutes_url_pattern", ""), metadata)
    if minutes_url:
        print(f"[V5] Fetching meeting notes from {minutes_url}...")
        minutes_text = fetch_document_text(minutes_url)
```

`fetch_document_text()` (`v5_segment.py:43-64`) does a plain `requests.get`, strips `<style>`
and `<script>` blocks (with `re.IGNORECASE`, specifically for Legistar's uppercase `<STYLE>` and its
base64 font payloads), strips remaining tags, unescapes entities, collapses whitespace, and
truncates to 12,000 characters.

Both documents then go verbatim into the prompt, `v5_segment.py:98-104`:

```python
    sources_block = ""
    if youtube_chapters:
        sources_block += f"\nYOUTUBE CHAPTERS (staff-annotated, often accurate post-meeting):\n{json.dumps(youtube_chapters, indent=2)}\n"
    if agenda_text:
        sources_block += f"\nOFFICIAL AGENDA (planned order — published before meeting, may not reflect live amendments):\n{agenda_text}\n"
    if minutes_text:
        sources_block += f"\nMEETING NOTES/MINUTES (actual record — published after meeting, captures amendments, votes, and outcomes):\n{minutes_text}\n"
```

alongside the full transcript, compacted to one line per turn at 150 characters each
(`v5_segment.py:106-109`) — ~28k tokens for a 2-hour meeting, comfortable in Gemini 2.5 Pro. The
instruction block (`v5_segment.py:122-133`) sets an explicit source hierarchy: minutes beat agenda
on disagreement, transcript is authoritative for timestamps, and "a section is a coherent topic
that persists for at least 2-3 minutes."

### Evidence it actually happened on this run

`pipeline.log:1671-1675`:

```
[V5] Fetching agenda from https://a2gov.legistar.com/View.ashx?M=AADA&ID=1367374&GUID=3392EBD4-...
[V5] Agenda fetched (3051 chars).
[V5] Fetching meeting notes from https://a2gov.legistar.com/View.ashx?M=MADA&ID=1367374&GUID=3392EBD4-...
[V5] Meeting notes fetched (3424 chars).
[V5] Triangulating sections with sources: ['agenda', 'minutes']...
```

Option (b) — inference from transcript cues — is ruled out. `processing/lWvRVUMyLP4/metadata.json:11`
is `"youtube_chapters": []`, so that path was inert, and the section output contains information
the transcript does not hold.

**The decisive proof** is `v5_sections.json:63`:

> "This section covers the presentation (26-0409) and resolution (26-0410) regarding the sidewalk
> gap filling implementation, which are listed separately on the agenda but were discussed together."

I grepped the full Whisper transcript for `26-0409`, `26-0410`, `0409`, `0410`, and the regex
`26-?04\d\d`. **Zero hits.** Those Legistar file numbers could only have come from the agenda
document. The model was reading the agenda, and it correctly detected that two separately-agendized
items were discussed as one — a genuine agenda-to-reality deviation.

**One honest caveat on the model's own source attribution.** `v5_sections.json:90` claims "The
meeting minutes confirm there were no callers for this second public comment period." The
transcript at 01:46 in fact contains *"I do not see anyone with their hand raised"* — so that fact
was transcript-derivable and the attribution to minutes may be confabulated. Same for `:99` ("The
Chair initially overlooked this agenda item"), which the transcript supports directly with *"I'm
sorry. I skipped ahead."* So `source_notes` is a useful signal but is not a reliable provenance
record. The file-number evidence is the one that carries weight.

### Is this reliable? Mostly — with three caveats, one of which is load-bearing

**Caveat 1 — the agenda URL is hardcoded to a single meeting. This is the one that matters.**

`registries/ann_arbor_sustainability_commission/body_registry.json:27-28`:

```json
  "agenda_url_pattern": "https://a2gov.legistar.com/View.ashx?M=AADA&ID=1367374&GUID=3392EBD4-918F-46C7-A9B9-B9B5C22F18B1",
  "minutes_url_pattern": "https://a2gov.legistar.com/View.ashx?M=MADA&ID=1367374&GUID=3392EBD4-918F-46C7-A9B9-B9B5C22F18B1",
```

It is called a *pattern* and stored at *body* scope, but it is an *event*-specific URL with a
literal `EventId` and `GUID`. `format_agenda_url()` (`v5_segment.py:67-83`) only substitutes
`{date}`, `{year}`, `{month}`, `{day}` — and per `legistar-a2gov-notes.md:73-84`, Legistar document
URLs are keyed on `EventId` + `GUID`, neither of which is derivable from a date. **As written, V5
fetches the March 10 documents for every video you point it at.** It worked once because the
registry was hand-pasted for that meeting.

This is a hardcode, not a mechanism — but it is a *cheap* fix, not a design problem. Phase 1
Legistar ingest already has to produce `EventId` and `GUID` per event (`legistar-a2gov-notes.md:73-84`
documents both as visible in the calendar HTML). The change is to look the URLs up per event
instead of per body: roughly ten lines, once `events` is populated. **It does mean Q2's answer is
"solved given Phase 1," not "solved today."**

**Caveat 2 — the 12,000-character truncation is a shared cap, and untested at Council scale.**
`v5_segment.py:61` returns `text[:12000]`. The commission agenda was 3,051 chars and minutes 3,424
— fine. A City Council agenda packet with 40+ items and attachment listings will exceed it, and
truncation is silent. This is the same class of bug as the 80k transcript cap that already burned
a run (`prototype_run_1_postmortem.md:26-34`), producing 6 sections covering the first 65 minutes
and a schema-valid output that would have shipped. Give each source its own budget, log the
character count, and fail loudly on truncation.

**Caveat 3 — n=1, and boundary accuracy has never been measured.** One meeting, one commission,
monthly cadence, a clean agenda, a chair who narrates transitions. The success criterion recorded
was "12 sections with summaries" (`prototype_run_1_postmortem.md:169`) — a count, not an accuracy
measure. Nobody has compared a boundary timestamp to ground truth. And the failure mode is
demonstrated: the earlier run produced 6 confident, well-formed, wrong sections.

### Consequence for Phase 4 — drop the aligner

`ARCHITECTURE-AND-ROADMAP.md:756` plans: *"Segmentation: index points if available; otherwise
monotonic alignment of agenda item text to transcript, plus cue phrases."*
`legistar-a2gov-notes.md:27` is blunter: *"Segmentation must be built (monotonic alignment of
agenda text to transcript + cue phrases). Was flagged as 'worth an hour to check, could save
weeks' — checked, and the weeks are not saved."*

**The code is new evidence against that conclusion. The weeks are largely saved after all** — not
by index points, but because an LLM given the agenda, the minutes, and the whole transcript does
the alignment in one $0.04 call, and does something a DP aligner structurally cannot: it detects
and *reports* deviations from the agenda (`v5_sections.json:63, 99`). A monotonic aligner assumes
agenda order holds. Ann Arbor's chair skipped an item and staff pulled it back later; two agendized
items were discussed as one. Those are the interesting events, and the aligner would have smoothed
them away.

**Replace "build the aligner" with:**

1. Wire `EventId`/`GUID` from the Phase 1 `events` table into per-event document fetch (~10 lines).
2. A deterministic assertion harness on V5 output — non-overlapping, monotonic, no gaps, coverage
   of full duration ±30s, section count within a factor of 2 of the agenda item count, every
   boundary timestamp present in the transcript. Cheap, and it catches the 6-section failure class.
3. Hand-score boundaries on 5 meetings across 2 bodies against the minutes. Report boundary error
   in seconds. **This is the missing measurement; do it before trusting segmentation at scale.**
4. Keep a per-source character budget with loud truncation warnings.

Estimate: **3–5 days instead of 2–3 weeks.** Phase 4 shortens accordingly. Retain the aligner as a
documented fallback for bodies with no published agenda, and note that per
`legistar-a2gov-notes.md:88-92` the accessible-HTML (`AADA`/`MADA`) variants only appear from
~March 2026 for Council — a retrospective window will hit PDF-only meetings, so the PDF fallback
path is required regardless.

---

## 2. Reuse triage

### 2.1 Keep as-is

| Component | Why |
|---|---|
| **`run_pipeline.py` + cache-per-step pattern** | The single best thing in v1. Every step writes a cached intermediate; `is_cached()` skips it. V7 crashed four times without losing upstream work (`prototype_run_1_postmortem.md:12`). Keep the pattern exactly; swap the terminal sink from a JSON file to Postgres. |
| **`v0_ingest.py` download + metadata** | `yt-dlp` handles both `watch?v=` and `live/` forms. Chapter parsing (`v0:174-179`) is correct and worth keeping even though Ann Arbor has none — other jurisdictions do. |
| **`v1_transcribe.py`** | Whisper `large-v2`, 1,116 segments, clean over 114 minutes. Word timestamps captured (`v1:64-67`). |
| **`v2_diarize.py`** | pyannote 3.1, 23 speakers. The `torch.load` / `use_auth_token` monkeypatches (`v2:20-41`) are ugly and load-bearing — keep them with the comments. |
| **`v5_segment.py:triangulate_sections()`** | Per Q2. The prompt's source hierarchy is well-reasoned and the "triangulation is always performed, external documents are starting structures not overrides" posture (`v5:11-13`) is better than the roadmap's "index points if available." |
| **`v6_extract_sections.py` as the Tier-C browse layer** | Section-level text-only extraction, 12 Pro calls, ~$0.19. Produces exactly the browse affordance `HANDOFF.md:103` describes. See §6.1 — this is v1 outperforming the target design. |
| **Registry pattern** (`registries/{body}/`) with `validated: true` gate | `v0:44-59` refuses to run against an unvalidated registry. That is the right HITL posture and it prefigures the target schema's `named_by NOT NULL` discipline. |

### 2.2 Modify

| Component | Change |
|---|---|
| **`utils.make_segment_id`** | Rewrite per §1.1. Highest priority in the whole plan. |
| **`v4_merge_turns.py`** | Rewrite the merge. Coalesce consecutive same-speaker segments with a ~2s gap tolerance; keep the interjection-sandwich logic on top of it. Replace `collect_text`'s overlap collection with word-level assignment (see WhisperX, below) so text is emitted exactly once. Drop turns with empty text into a `review_queue` row rather than a claim. |
| **`v1_transcribe.py`** | Two changes. (a) **Add custom vocabulary** — there is none. `whisper.transcribe()` is called at `v1:24-29` with no `initial_prompt`. `vocabulary.json` at the repo root is *not* an ASR vocabulary; it holds the `constituency_group` enum and nothing else. The roadmap calls custom vocabulary mandatory (`ARCHITECTURE-AND-ROADMAP.md:735-746`), and the observed date corruption ("spring of 2003" for 2023) is exactly what it prevents. (b) **Move to WhisperX** for forced alignment, so word-level timestamps can be joined to diarization spans — this is the proper fix for the fragment/duplication problem, not just a mitigation. |
| **`v3_resolve_speakers.py`** | Keep multimodal — `HANDOFF.md:86-89` explicitly sanctions multimodal for *identity*. One Pro call on a 20-minute clip, ~$0.41, 18/23 resolved. Apply the backlog's two fixes (`improvement_backlog.md:10-12`): sample timestamps across the whole meeting rather than only the first three appearances, and write merged embeddings back to the registry after human review of fragmented clusters. Gate voiceprint persistence on `persons.is_public_figure` per `claim_store.sql:94-97`. |
| **`v6_extract_sections.py`** | Keep the call structure; retarget the output. The six analytical fields become typed rows (`fiscal_references`, `barriers`, `coalitions`, `decisions`, `claim_warrants`) per `pipeline-v1-review.md:81-88`. Have it emit candidate rows with verbatim spans, not sentences. Its `entities_discussed` becomes the Subject/Issue suggestion feed (§4.3). |
| **`schema/video_output_schema.json`** | Becomes the API-level structured-output schema, with a tier discriminator and conditional requirements. See §3.3. |
| **`v8_assemble.py`** | Becomes a database writer, not a JSON assembler. Keep the validation discipline; drop the coercion (`v8:31-50`) once the model is bound by a real schema. Delete the `else` branch at `:51-60` — a missing extraction must produce no row, not an empty one. |
| **`v0_ingest.py` date handling** | `published_date` is the YouTube *upload* date (`v0:229-233`), which for this meeting is 2026-03-11 while the meeting was 2026-03-10. Chronology is the join key across every source (`grapevine-context.md:68`). Take `event_date` from Legistar; keep upload date as a separate field. |

### 2.3 Discard

| Component | Why |
|---|---|
| **`v7_argumentation.py` — the entire file** | Its only video-dependent outputs are `tone` and `demeanor`, which are scrapped. Everything else it produces is text-derivable at a fortieth of the cost. 37.9M video tokens bought two dead fields plus a misattributed dialogue graph. Delete it; do not port the clip-management machinery. |
| **`audio_visual_context`** | Per `HANDOFF.md:84-89`. 100% coverage, near-zero variance, and — now demonstrated — populated for 71 segments with no speech at all. |
| **`citations`** | 0/1,157. Dead by prompt omission (`v7:177`). Its intended job is covered properly by `claim_relations` and `claim_prior_references`. |
| **`v0_ingest.upload_to_gemini()` (`v0:190-205`)** | Uploads the **full 2-hour video** to the Files API and stores `gemini_file_uri` in metadata. Nothing consumes it — V3 and V7 each build their own clips. Dead upload of the largest artifact in the pipeline. |
| **`vocabulary.json`** | Misnamed, holds only the constituency enum. Move that enum into code or the schema; write a real ASR vocabulary file per jurisdiction. |
| **v1's `output/lWvRVUMyLP4.json` as a data source** | §4.1. |
| **`google.generativeai` SDK** | End-of-life; every run emits the deprecation warning (`improvement_backlog.md:28`). Migrate to `google.genai` as part of the v2 rewrite rather than as a separate chore — you are rewriting the call sites anyway. |

---

## 3. The two-pass extraction design

### 3.1 Ordering — the merge fix precedes the tier gate

```
0  INGEST      Legistar → events, event_items, matters, persons, memberships,
               media_assets (+ EventId/GUID for document fetch)
1  ACQUIRE     yt-dlp → audio + video; duration_seconds
2  ASR         WhisperX + per-jurisdiction custom vocabulary; word-level timestamps
3  DIARIZE     pyannote 3.1 → anonymous clusters
4  MERGE  ★    same-speaker coalescing (gap ≤2s) + interjection absorption,
               word-level text assignment, exact-boundary utterance keys
               → utterances   [1,156 → ~440]
5  SEGMENT     V5 triangulation over agenda + minutes + transcript → segments
6  CLASSIFY ★  PASS 1 — cheap, exhaustive, text-only
7  RESOLVE     speaker ID: roll-call enrollment, self-ID, chair address, roster,
               voiceprint (public figures only) → utterances.person_id
8  EXTRACT  ★  PASS 2 — expensive, gated, text-only
9  CANONICALIZE resolve to subjects / issues / arguments; low confidence → review_queue
10 REVIEW      triaged queue; measure minutes_spent
11 RENDER      pages as materialized views
```

★ = new or rewritten. Step 4 before step 6 is the correction from §1.3.

### 3.2 Pass 1 — the classifier

One text-only call per section, all of that section's turns in a single request. 12 calls per
meeting. Sees: section topic, agenda item title, and every turn's speaker, capacity, duration, and
full text. Sees no video.

Per **segment** (written to `segments`):

- `segment_kind` — the `claim_store.sql:339-342` enum (`roll_call` … `adjournment`)
- `extraction_tier` — `A` | `B` | `C`, and `tier_assigned_by`
- `summary` — 2–3 sentences. **Tier C stops here.** This is the browse affordance in full.

Per **utterance** (written to `utterances`):

- `is_substantive` — boolean
- `actor_capacity` — official | staff | public | organizational_rep | expert | utility_rep
- `responds_to_utterance_id` — nullable, and *stated as nullable*, which is the one field v1 got
  sparse output on
- `date_validation_flag` — any date in the text outside a plausible window for the meeting.
  Cheap here, and it catches the ASR year corruption the roadmap flags.

Deterministic pre-gates that need no model call at all, applied before Pass 1 writes a tier:

- empty or whitespace `text` → `is_substantive = false`, and a `review_queue` row. Never a claim.
  (Would have caught all 71 hallucinations.)
- `duration_ms < 2000` **and** `word_count < 8` → `is_substantive = false`
- `segment_kind = roll_call` → forced `extraction_tier = 'C'`

### 3.3 What gates on `extraction_tier`

| Tier | Definition | What runs |
|---|---|---|
| **A** | Decision nodes, contested items, anything a finding will cite | Full Pass 2: `claims`, `claim_warrants`, `claim_conditions`, `claim_relations`, `jurisdiction_citations`, `decisions`, `commitments`, `fiscal_references`, `barriers`, `coalitions` |
| **B** | Substantive but uncontested | `claims` + `claim_warrants` only. No conditions, no jurisdiction citations, no typed findings |
| **C** | Procedural, ceremonial, routine | `segments` row + `summary` + `utterances` rows. Nothing else. No model call in Pass 2 at all |

**Both gates must be ANDed.** `claim_store.sql:347` puts `extraction_tier` on `segments` and `:371`
puts `is_substantive` on `utterances`, and the DDL comment reads as if the section-level tier alone
drives depth. v1's data shows why that fails: the Tier-A Franchise section is 54 minutes and 280
coalesced turns, plenty of which are still "Yes." and "Can you hear me?". The Pass 2 selector is:

```sql
WHERE s.extraction_tier IN ('A','B') AND u.is_substantive
```

On this meeting: 438 coalesced turns → 378 in Tier A/B → **247 also substantive**. A 1,156 → 247
reduction in extraction units, **4.7×**, before any pricing change.

Two design rules for Pass 2, both learned from v1's failures:

- **Extract at section scope with turn-level span attribution — not turn-by-turn.** Batch a
  section's substantive turns into one call with the surrounding section transcript as context, and
  require every claim to name the `utterance_key` and verbatim span it came from. Turn-by-turn
  extraction is what produced "The speaker confirms his presence at the meeting in Ann Arbor" — a
  model asked for the central argument in a two-second fragment will manufacture one. Arguments
  span turns; `claims.extra_spans JSONB` (`claim_store.sql:432`) already anticipates this.
- **`verbatim` is the model's output, quoted from the input, and validated by exact substring
  match before insert.** Not `NOT NULL` as a hope — a hard check in the writer. Fail the claim, not
  the batch, and route failures to `review_queue`.

### 3.4 Where multimodal survives

Speaker identity only, per `HANDOFF.md:86-89`. One Pro call on a ~20-minute clip in step 7. Do not
reintroduce video into extraction; if demeanor ever matters, it matters on Tier A only, where a
human is already reviewing.

---

## 4. Migration path into `claim_store`

### 4.1 Do not migrate v1's analytical output. Re-run from the cached intermediates.

`pipeline-v1-review.md:135-162` gives a field-level migration map. It is correct as a *field
mapping* and should be kept as the v2 output contract. It should **not** be executed as a data
migration, because 45% of the argumentation is attributed to the wrong utterance (§1.1), 6% of
segments have no verbatim text at all yet carry fabricated claims (§1.2), and the units are
fragments rather than utterances. Migrating that produces a claim store whose provenance is wrong
in a way no downstream review can detect — the `verbatim` will be a real quote, just not the quote
the claim came from.

**The intermediates, however, are a genuine asset**, and they are all cached:

| Artifact | Size | Status |
|---|---|---|
| `metadata.json` | 3 KB | Good (fix the date) |
| `v1_transcript.json` | 2.0 MB | **Good and expensive to reproduce.** Word timestamps present |
| `v2_diarization.json` | 108 KB | **Good and expensive to reproduce** |
| `v3_speakers.json` | 6 KB | Good; hand-corrected |
| `v5_sections.json` | 4 KB | **Good** — the 12 sections |
| `v6_extracts.json` | 61 KB | Useful as candidate rows and Subject/Issue seeds |
| `v4_turns.json` | 610 KB | **Discard** — rebuild from v2 with the fixed merge |
| `v7_argumentation.json` | 491 KB | **Discard** |
| `output/lWvRVUMyLP4.json` | 1.7 MB | **Discard as data.** Retain as the eval negative control (§4.4) |

Re-running steps 4→8 on `lWvRVUMyLP4` needs no download, no Whisper, no pyannote, no re-segmentation.
Modeled cost: **~$1.30** (§5). That is cheaper than writing the migration script.

### 4.2 What loads directly

Machine-populated, no judgment, safe today:

```
metadata.city_or_municipality        → jurisdictions            (ocd-division/country:us/state:mi/place:ann_arbor)
metadata.governing_body              → bodies
metadata.body_description            → bodies.description
   └─ "created by City Council in 2025, combining the Energy and Environmental
      Commissions"                   → body_lineage  ×2  (relation='merged_into')
metadata.source_id  vid_yt_…         → media_assets.host='youtube', external_id='lWvRVUMyLP4'
metadata.youtube_chapters == []      → media_assets.has_index_points = FALSE
body_registry.current_roster[]       → persons + posts + memberships
v3_speakers.speaker_map{}            → persons + person_aliases + utterances.person_id
                                        speaker_id_method from v1's `source` field
                                        ('placard'→ocr, 'roll_call'→roll_call, 'visual'→manual)
v5_sections[].section_topic          → segments.section_topic
v5_sections[].start_sec / end_sec    → segments.start_ms / end_ms   (×1000)
v5_sections[].source_notes           → NO HOME IN THE SCHEMA — see §6.2
rebuilt turns                        → utterances (text, spans, duration, sequence)
```

`body_lineage` needs two rows, and both predecessor bodies must be ingested — Energy Commission and
Environmental Commission, both inactive (`legistar-a2gov-notes.md:44-46`), with Sustainability
Commission coverage starting 2025-08-12 (`pipeline-v1-review.md:200-203`). A 5-year retrospective
that queries by `body_id` alone silently loses the earlier A2Zero record.

### 4.3 What needs curation before any claim can be inserted

- **One `subjects` row minimum.** Hand-create "Ann Arbor Sustainable Heating Franchise" — it is the
  54-minute Tier A section and the natural pilot Subject. `created_by = 'human'`.
- **`issues`** — hand-name from `v6_extracts.entities_discussed[]`. That field is the cold-start
  answer (`HANDOFF.md:118-120`) and `definition_in_context` gives the human real material to name
  from. Suggestions only; `issues.named_by NOT NULL` is correct.
- **`arguments`** — empty at first. `claim_warrants.argument_id` is nullable
  (`claim_store.sql:458`), so claims land before canonicalization. Populate as the registry grows.
- **`orgs`** — the 40 `utility_representative` turns are almost certainly DTE. One row, plus
  `affiliations` with `source='self_id'`.

### 4.4 Sequenced steps

| # | Step | Output |
|---|---|---|
| 1 | Apply `claim_store.sql` to Postgres, with the §6 amendments | 42 tables |
| 2 | Phase 1 Legistar ingest for this one event (`EventId` 1367374) | `events`, `event_items`, `matters` `26-0409`/`26-0410` |
| 3 | Load structural rows from §4.2 | `jurisdictions` … `media_assets`, `body_lineage` |
| 4 | Rewrite V4; rebuild turns from the cached `v2_diarization.json` | ~440 `utterances` |
| 5 | Load `v5_sections.json` → `segments`; join to `event_items` by agenda title | 12 `segments`, ≥2 with `event_item_id` |
| 6 | Pass 1 classify | `segment_kind`, `extraction_tier`, `is_substantive` |
| 7 | Hand-curate 1 Subject + issues seeded from `v6_extracts` | `subjects`, `issues` |
| 8 | Pass 2 extract on Tier A/B ∧ substantive | ~247 units → claims, warrants, conditions, typed findings |
| 9 | **Diff against `pipeline-v1-review.md`'s hand annotation** | Precision/recall on conditions |
| 10 | Human review of every claim on the Franchise section; log `minutes_spent` | A-graded baseline, real HITL number |

**Step 9 is the point.** `ARCHITECTURE-AND-ROADMAP.md:717-726` is explicit that condition agreement
is the make-or-break measurement, and v1 has zero condition data — `argumentation` has
`core_claim`, `evidence_provided`, `framing_used` and nothing contingent
(`pipeline-v1-review.md:121-123`). **Phase 2's eval cannot be scored against v1's output at all.**
It needs this fresh pass. Worth stating plainly because it means Phase 2 and Phase 4 are coupled
more tightly than the roadmap's "Phase 4 only after Phases 2 and 3" sequencing implies: Phase 2's
central measurement requires the v2 extractor to exist.

Keep `output/lWvRVUMyLP4.json` as the **negative control**. It is a documented case of confident,
schema-valid, well-written, wrong output. Any eval harness that cannot distinguish it from the v2
pass is not measuring anything.

---

## 5. Cost model

**Pricing assumptions — verify before quoting these externally.** Gemini 2.5 Flash $0.30/M input,
$2.50/M output; 2.5 Pro $1.25/M input, $10/M output; video ≈263 tokens/second (this last one is
derived from v1's own arithmetic, `prototype_run_1_postmortem.md:49`, and independently reproduces
its "~158k tokens/call" figure for a 10-minute clip). Token counts below are computed from the
actual cached transcript and diarization, not estimated.

### v1, measured

`prototype_run_1_postmortem.md:115-136`. Total spend **$14.50**; a clean run estimated at **$7–8**.

| Step | Model | Calls | Cost |
|---|---|---|---|
| V3 speaker ID | Pro, 20-min video clip | 1 | ~$0.80 |
| V5 segmentation | Pro, text | 1 | ~$0.05 |
| V6 section extraction | Pro, text | 12 | ~$0.19 |
| V7 runs 1–2 (Pro, 40-min clips, crashed) | Pro, video | ~72 | ~$10.50–13.50 |
| V7 runs 3–5 (Flash, 10-min clips) | Flash, video | ~240 | ~$6.37 |

**Video tokens sent by V7: 37.9M.** Every `generate_content` call re-sent the whole 10-minute clip
to analyze 5 turns of text (`v7:186`), so each clip was billed roughly five times over. The
observed $6.37 is below the $12.58 a naive calculation gives, which suggests implicit context
caching absorbed some of the repetition — but the structural waste is the point, not the discount.

### v2, modeled on this meeting's actual data

| Step | Model | Calls | Tokens | Cost |
|---|---|---|---|---|
| Segmentation (V5, unchanged) | Pro, text | 1 | 19.6k in / 1.5k out | $0.040 |
| Speaker ID (V3, video — **kept**) | Pro, 20-min clip | 1 | 316k in / 2k out | $0.415 |
| **Pass 1 classify** | Flash, text | 12 | 33k in / 11k out | **$0.037** |
| **Pass 2 extract** (247 units, batched 8) | Pro, text | 31 | 82k in / 111k out | **$1.214** |
| | *(same, on Flash)* | | | *$0.302* |
| **Total** | | **45** | | **$1.71** |
| **Total, Flash extraction** | | | | **$0.79** |

**Delta vs. v1: 4.1× cheaper than a clean v1 run, 8.5× cheaper than what was actually spent.**
Flash extraction: 9× and 18×.

Where the saving comes from, decomposed — this ordering is the actionable finding:

| Change | Factor |
|---|---|
| **Remove video from extraction** | **~40×** on the extraction pass |
| Fix the V4 merge (1,156 → 438 units) | 2.6× |
| Tier gate (438 → 247 units) | 1.8× |
| Batch 8 turns/call instead of 5 | 1.6× |

**Video removal dominates by more than an order of magnitude, and it is a consequence of scrapping
affect — not of tiering.** The handoff attributes the economics to the tier field
(`HANDOFF.md:71`). Tiering is worth 1.8× here. Dropping affect extraction is worth 40×. Both are
right to do; the priority is inverted.

Note that speaker ID is now **24% of total cost** and the largest single line item. It is the one
place video is still justified, and it is also where accuracy matters most (misattribution
poisons every claim downstream), so this is money well spent. But it caps how cheap the pipeline
can get: ~$0.42/meeting is the floor.

At $1.71/meeting, the ~130–150 Council events over 5 years plus commissions
(`legistar-a2gov-notes.md:126-129`) is **$350–500** of inference for the full retrospective.
Inference is not the constraint on this project. **Human review is**, which is the whole argument
for the tier field — and it is worth being clear that tiering earns its place through the review
budget, not the API bill.

---

## 6. Where the target design is impractical, or worse than v1

Asked for directly. Six items, most consequential first.

### 6.1 `claims.subject_id NOT NULL` is a cold-start deadlock

`claim_store.sql:393`:

```sql
    subject_id          INT NOT NULL REFERENCES subjects(id),
```

`subjects` is hand-curated (`:266` `created_by TEXT NOT NULL`, and `HANDOFF.md:75` puts canonical
naming firmly in human hands — correctly). So: no claim can be inserted until a human has named
the Subject it belongs to. But the way you discover which Subjects a corpus contains is by
extracting from it. v1's `entities_discussed` was doing exactly that discovery work, and
`HANDOFF.md:118-120` proposes feeding `definition_in_context` into Issue naming for precisely this
reason.

The deadlock is not hypothetical — it blocks step 8 of §4.4 on day one, and at corpus scale it
means a human must pre-classify every meeting before any extraction can land.

**Fix, in order of preference:**
1. Make `subject_id` nullable and add `claims.curation_state` (`unassigned` | `proposed` |
   `confirmed`), with a partial unique index or trigger enforcing `NOT NULL` before a claim can
   enter a `page_manifest`. Provenance is enforced where it matters — at render — rather than at
   insert.
2. Or seed `subjects (id=0, name='unassigned', created_by='system')` and make assignment a
   review-queue action.

Option 1 is better because it makes the staging state legible and queryable. Either way, **this is
the one schema change that must happen before Phase 3 starts.**

### 6.2 `segments` has no home for agenda-deviation notes

V5 produces genuinely valuable procedural signal that the schema drops on the floor:

- *"listed separately on the agenda but were discussed together"* (`v5_sections.json:63`)
- *"The Chair initially overlooked this agenda item, but staff interjected to provide the update
  before adjournment"* (`:99`)

An item skipped and recovered, and two items merged, are exactly the kind of procedural friction
`ARCHITECTURE-AND-ROADMAP.md:668` tries to detect from Legistar action text — available here for
free, from video, and thrown away. Add:

```sql
ALTER TABLE segments ADD COLUMN agenda_deviation_note TEXT;
ALTER TABLE segments ADD COLUMN deviation_kind TEXT;  -- items_merged | item_skipped |
                                                      -- item_added | order_changed | item_postponed
```

Cheap, and it feeds the contestation index from a second corpus.

### 6.3 v1's section-scoped extraction is better than what the schema invites, and the schema should say so

`claims.utterance_id BIGINT REFERENCES utterances(id)` (`claim_store.sql:424`) reads as an
invitation to extract per utterance. **Don't.** v1 is the proof of what that produces: asked for
the central argument in a two-second fragment, the model manufactures one, and 100% of empty
fragments got fabricated claims.

More importantly, this is the roadmap's own argument turned on the extraction layer.
`ARCHITECTURE-AND-ROADMAP.md:22-24` explains that the authored wiki beats RAG because *"RAG hands
the model top-k fragments with no global view, while the wiki hands it pre-digested, coherent
prose."* Turn-by-turn extraction hands the model a fragment with no global view. Section-scoped
extraction hands it a coherent 30-minute block. **The same argument applies, and v1's V6 got this
right while the target design's data model quietly points the other way.**

v1's V6 — one text-only call over a whole section, ~$0.19 for the meeting — is a pattern to
preserve, not just a cheap fallback for Tier C. Add a DDL comment on `claims.utterance_id` stating
that it records *where the claim's span begins*, not the extraction unit, and that extraction runs
at section scope with `extra_spans` carrying multi-turn arguments.

### 6.4 The canonicalization review budget is unaccounted for

`ARCHITECTURE-AND-ROADMAP.md:495-498` targets full human review on 10–15% of claims. That budget
covers claim *verification*. It does not cover `issues.named_by` and `arguments.named_by`, both
`NOT NULL` and both explicitly human (`claim_store.sql:300, 322`), correctly so
(`HANDOFF.md:75`).

Scale check from this meeting: 247 substantive units → conservatively 150–300 claims → each with
1–4 warrants. Every novel warrant is a candidate `arguments` row needing a human name, and naming
requires seeing the other warrants to judge whether it is the same argument. That is not a
per-claim decision; it is a clustering judgment over a growing registry, and it does not
parallelize or sample the way claim verification does.

This is a real cost, it is the layer `HANDOFF.md:140` identifies as where the intellectual work
lives, and it is currently invisible in the plan. Instrument it: `review_queue.reason` already
supports `entity_merge`; add `argument_naming` and measure `minutes_spent` on it separately from
claim review in step 10 of §4.4. **Expect it to dominate**, and expect the registry to stabilize
(as `ARCHITECTURE-AND-ROADMAP.md:792` predicts for conditions) rather than grow linearly — but
measure rather than assume.

### 6.5 Smaller items

- **`media_assets.has_index_points`** — confirmed permanently `FALSE` for Ann Arbor
  (`metadata.json:11`, `legistar-a2gov-notes.md:27`). Keep the column for portability; delete any
  code branch that waits on it.
- **`claims.span_start` dual-meaning** (`claim_store.sql:426`, "ms for video, char offset for
  text") — works, but a query that mixes source types will silently compare milliseconds to
  character offsets. Either add a `span_unit` column or split into `span_start_ms` /
  `span_start_char`. Low priority, will eventually bite.
- **`verbatim TEXT NOT NULL` accepts `''`.** v1's V8 `else` branch (`v8:51-60`) is a live
  demonstration of code that would happily write empty strings into a `NOT NULL` column. Add
  `CHECK (length(trim(verbatim)) > 0)` to `claims`, `claim_warrants`, `claim_conditions`,
  `jurisdiction_citations`, `commitments`, `fiscal_references`, `barriers`.
- **`persons.display_masked GENERATED ALWAYS AS (NOT is_public_figure)`** (`:97`) — a stored
  negation of a column in the same row. Harmless, redundant; it belongs in the render layer. Ignore
  unless you are tidying.

### 6.6 Where v1 beats the target design — summarized

Three places, since the task asked directly:

1. **Section-scoped extraction with global context** (§6.3). v1's best idea and the one the target
   data model most obscures.
2. **Always-on source triangulation.** `v5_segment.py:11-13`: *"Triangulation is always performed.
   External documents are starting structures, not overrides."* The roadmap's *"index points if
   available; otherwise build alignment"* would have trusted index points blindly. Ann Arbor has
   none, so it is moot for case 1 — but the posture is better and should be carried into the
   design, because a Granicus jurisdiction will have index points that are stale or wrong on
   exactly the meetings where the agenda changed live.
3. **Deviation reporting** (§6.2). A monotonic aligner assumes agenda order holds. The LLM noticed
   twice that it didn't.

---

## 7. Recommended immediate order of work

1. **Fix `make_segment_id`** and add the uniqueness constraint. One hour. Everything downstream is
   unreliable until this lands.
2. **Rewrite the V4 merge**, with tests on the cached `v2_diarization.json`. Assert: no empty
   `verbatim_text` reaches an extraction unit; total turn text ≈ 1.0× transcript text (currently
   1.79×); ~440 turns, not 1,156.
3. **Verify the Legistar API** — `/eventitems/{id}/votes`, the one unresolved Phase 0 question
   (`legistar-a2gov-notes.md:162-187`). ~30 minutes, blocks Phase 1, and supplies the
   `EventId`/`GUID` that Q2's fix needs.
4. **Amend the DDL** per §6.1 and §6.5, then apply to Postgres.
5. **Wire per-event agenda/minutes fetch** into V5 (~10 lines once `events` exists), plus the
   assertion harness and per-source character budgets from Q2.
6. **Build Pass 1**, structured-output mode, on the rewritten turns. Cheap and fast to iterate.
7. **Build Pass 2**, section-scoped, text-only, with substring-validated `verbatim`.
8. **Run §4.4 steps 1–10 on `lWvRVUMyLP4`** from cached intermediates. ~$1.30 and no
   re-transcription. Score condition agreement against the existing hand annotation.
9. **Only then** touch a second meeting, and make it one with a contested Council vote plus a
   low-scoring control (`ARCHITECTURE-AND-ROADMAP.md:701-708`).

Add token and cost accounting to every model call in step 6. **There is none in v1** — grepping the
codebase for `usage_metadata`, `token_count`, `cost`, or `price` returns zero hits, which is why
the postmortem's cost table is hand-estimated after the fact. `review_queue.minutes_spent` exists
for the human side (`claim_store.sql:671`); give the machine side the same treatment.
