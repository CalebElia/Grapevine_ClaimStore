# ASR + Diarization Benchmark

**Decision: ElevenLabs Scribe v2 for words and entities, Speechmatics for speaker
boundaries, joined by deterministic alignment.** §1–§8 recommended Scribe alone; **§9
supersedes that** after duration testing on a real 35-minute window. Read §9 first.

Date: 2026-08-07 · Harness: `pipeline/bench/` · Raw data: `processing/*/bench/`

---

## 1. Why we ran this

The v2 pipeline runs **WhisperX for words and pyannote for speakers, then joins them by
timestamp**. Human review of one meeting showed that join is the source of most of our
defects, not the transcription:

- 38% of pyannote segments were under 1.5s — below the floor where a speaker embedding
  carries usable information
- 141 words straddled a speaker-change boundary and were assigned by midpoint
- 67% of interjections carried no usable content (33 of 70 were literally empty)
- A residual cluster of 15 sub-second fragments totalling 5.7s was confidently named as
  a real person

None of that is fixable by a better ASR model. It is a consequence of running two
systems independently and reconciling them after the fact. Vendors that emit a **speaker
label on every word** have no join step and therefore cannot fail this way — that
architectural question, not raw accuracy, is what the benchmark was built to answer.

A second motive: at ~150 Council meetings × 2–3 hours, local WhisperX at ~1.5× realtime
is roughly **250 hours of compute**, or nine days of continuous machine time.

---

## 2. What we tested

Seven systems. Model identifiers are the API values actually sent, not dashboard display
names — see §6.

| System | Model | Speaker label per word? | Names speakers? |
|---|---|---|---|
| ElevenLabs Scribe | `scribe_v2` | yes | no |
| Deepgram | `nova-3` | yes | no |
| AssemblyAI | `universal-3-5-pro` | yes | no |
| Speechmatics | `enhanced` | yes | no |
| Rev.ai | `machine` | no | no |
| Google Gemini | `gemini-3.6-flash` | no | **yes** |
| **Incumbent** | `whisperx large-v2` + `pyannote community-1` | no | no |

OpenAI was excluded. The Azure AI Foundry endpoint lists 370 catalogue models but has no
callable deployments, and `gpt-4o-transcribe-diarize` — the only one that explicitly
diarizes — is nine months older than the newest transcription models there and requires
a different region and a separate Foundry project.

---

## 3. How we tested

### 3.1 Two meetings, four windows

| Meeting | Window | What it stresses |
|---|---|---|
| Sustainability Commission 3/10/26 | 0:00–8:00 | roll call + a fully verified 3-min passage |
| City Council 4/20/26 | 1:30–9:30 | roll call, 12 known voices |
| City Council 4/20/26 | 48:30–56:30 | *(mislabelled — see §7)* |
| City Council 4/20/26 | 64:30–72:30 | *(mislabelled — see §7)* |
| City Council 4/20/26 | 128:00–136:00 | deliberation / crosstalk |

The crosstalk window was chosen by **hand-off-marker density in text** — counting
phrases like "councilmember", "seconded by", "all in favor" — never by speaker-change
density. Diarization is the thing under test; selecting windows by a vendor's own
segmentation would let it set its own exam. That window scored 38 markers against 17 for
roll call, more than double any non-overlapping alternative.

### 3.2 Two scoring modes

**Scored against ground truth** (Sustainability only). Ground truth carries an explicit
evidence tier per element, because they are not equally trustworthy:

- `verified` — a human wrote the exact words (Ken Garber's 476-word public comment)
- `confirmed` — a human affirmed a machine proposal (20 cluster→name assignments)
- `reference` — machine output, human-corrected but not audited (turn boundaries, word
  timings). **Not gold**, and it flatters the incumbent, which produced it.

**Cross-vendor disagreement** (Council). No ground truth exists for a 2.5-hour Council
meeting and producing it would cost hours. Instead: run independent systems on the same
audio; where they agree they are probably right, where one stands alone it is probably
wrong. This is the same reasoning that produced the vocabulary entry for "Missy Stults" —
Whisper said *Stoltz*, YouTube captions said *staltz*, two systems guessing at one sound
and disagreeing means a proper noun in neither lexicon.

### 3.3 Metrics

| Family | Measures |
|---|---|
| Word | WER (filler-insensitive) on verified passages; **entity accuracy** scored against counts from verified text |
| Speaker | DER with optimal cluster mapping; cluster purity and coverage; **speakers found vs. actually present** |
| Turn | fragment rate, empty rate, **welded rate** (turns containing more than one true speaker) |
| Time | word-offset vs. reference alignment |
| Practical | audio coverage, wall-clock, cost |
| Composite | **estimated review burden** in minutes — the objective |

Turn metrics run **after coalescing every vendor to the same unit** (consecutive
same-speaker turns merged at a 2s gap). Without that, the metric measures each vendor's
definition of "turn" rather than its quality — see §6.

---

## 4. Outcomes

### 4.1 Scored run — Sustainability, 0:00–8:00, 14 speakers present

| System | WER | Entity | DER | Speakers | Weld | Review | Word-level speakers |
|---|---|---|---|---|---|---|---|
| **ElevenLabs** | 0.020 | 0.714 | 0.181 | **14/14** | **0.000** | **16.1 min** | yes |
| Speechmatics | 0.031 | 0.750 | 0.182 | 14/14 | 0.044 | 20.1 min | yes |
| Gemini | **0.007** | **1.000** | 0.199 | 16/14 | 0.196 | 24.9 min | no |
| Rev.ai | 0.053 | 0.833 | 0.163 | 6/14 | 0.069 | 30.4 min | no |
| *Incumbent* | 0.007 | 0.812 | n/a¹ | 16/14 | 0.174 | 35.2 min | no |
| Deepgram | 0.015 | 0.857 | 0.267 | **3/14** | 0.077 | 36.7 min | yes |
| AssemblyAI | 0.009 | 0.875 | 0.236 | **3/14** | 0.200 | 45.8 min | yes |

¹ Self-referential — the reference timeline was built from the incumbent's own output.

**Every vendor beats the incumbent on review burden**, several by 2×.

### 4.2 Council roll call — 12 known voices

| System | Speakers found | Hint given |
|---|---|---|
| **ElevenLabs** | **12** | none |
| AssemblyAI | 12 | `speakers_expected=12` |
| Deepgram | **2** | none available |

AssemblyAI's result is **not evidence of good diarization**. On the crosstalk window,
running it with and without the hint produced 11 clusters vs. 4 — with **0.993 word
agreement** between the two runs. The parameter is a *constraint*, not a hint: give it a
number and it produces that many clusters regardless of how many people spoke. It is
only safe where the count is externally known.

ElevenLabs recovered 12 with no hint at all. Deepgram found 2 — and 3 of 14 on the
Sustainability window. It has no equivalent parameter.

### 4.3 Crosstalk — 128:00–136:00, count unknown

Unhinted speaker counts: Deepgram 5, AssemblyAI 4, ElevenLabs 4. All plausible.
**Deepgram's failure is specific to rapid short utterances, not to deliberation.** Since
roll call is the free labelled enrollment set at the top of every meeting, that is still
disqualifying.

Odd-one-out rate, independent systems only:

| System | Rate |
|---|---|
| ElevenLabs | **1.29%** |
| Deepgram | 2.41% |
| AssemblyAI | 3.26% |

### 4.4 Vocabulary discovery — the strongest secondary result

Words that independent systems spell differently are, by definition, in none of their
lexicons. Cross-checked against Legistar:

| Systems produced | Correct (Legistar) |
|---|---|
| `dish` / `disch` | **Lisa Disch** — council member |
| `redina` / `rudina` | **Travis Radina** — council member |
| `malik` / `malek` | **Jon Mallek** |
| `juskevich` / `jaskiewicz` | **Adam Jaskiewicz** — registered commenter |
| `courtland` / `cortland`, `bursey` / `burci` | **Cortland Bersee** |
| `landy` / `lande` | **Lynne Lande** |
| `therapaws` / `therapause` | **Therapaws of Michigan** — agenda item 17 |

**In two cases the majority was wrong.** Consensus chose `dish` over `disch`; no system
produced `Radina` at all. Majority vote on a proper noun is unreliable — but the
disagreement flagged both perfectly.

The operating rule: **disagreement detects which terms need vocabulary entries; Legistar
resolves what they should be.** Every name above is already in the agenda or the
`persons` table before any audio is processed.

---

## 5. Recommendations

### 5.1 Adopt ElevenLabs Scribe v2 for S1/S2

- Only system to recover a known speaker count **without being told** (12/12, 14/14)
- Lowest odd-one-out rate among independent systems on crosstalk
- Zero welded turns and best cluster purity (0.968) on the scored window
- Speaker label on every word — no ASR/diarization join, so the entire class of
  midpoint-assignment defects disappears

**Deepgram: rejected.** 2/12 and 3/14 on roll call across two meetings, no hint
parameter.

**AssemblyAI: viable, awkward.** Needs a count you usually don't have; worst odd-one-out
once the duplicate run was removed.

**Gemini: retain as a naming layer, not a transcript.** Still the only system that
produces names (56% correct) and the only one with perfect entity accuracy — but 0.760
purity and no real timestamps disqualify it as the record. Prior work showed its named
turns can be aligned onto our timestamped clusters **deterministically**, at 100%
precision on roster members, with no LLM call in the loop.

### 5.2 Per-meeting vocabulary, generated from Legistar

Scribe v2's one real weakness is entity accuracy — **0.714, worst of the speaker-per-word
systems**, rendering `A20` four times where the correct term is `A2Zero`.

This is fixable and the fix is already proven. Every vendor accepts custom vocabulary as
a **keyterm list**, which is structurally safer than Whisper's `initial_prompt`: a term
list is not continuable prose, so it cannot leak into the transcript the way our prompt
did (destroying 62 words at 23:19 of the Sustainability meeting).

What exists: `registries/ann_arbor/asr_vocabulary.json`, 21 hand-curated terms.
**What does not exist yet:** a step that generates per-meeting terms from that meeting's
own Legistar agenda — member names, registered commenters, proclamation subjects. The
Council test showed the hand-built list misses everything Council-specific.

### 5.3 A turn-boundary reconciliation step is still required

**Boundary agreement is poor across every pair on every segment** — 0.056 to 0.652 —
even where word agreement exceeds 0.90. On the deliberation window, Deepgram and
AssemblyAI agree on 91.5% of words and **5.6%** of turn boundaries. Two runs of the *same*
system with different speaker hints agreed on only 55%.

No vendor makes this go away. Scribe v2 is the best of them, not a solution to it. The
existing S3 coalescing (2s gap, same speaker) plus the human review loop in
`pipeline/export_review.py` / `import_review.py` remain necessary.

### 5.4 Revised pipeline shape

```
S1/S2   ElevenLabs Scribe v2 — words + speaker-per-word + timestamps
        with a per-meeting keyterm list built from the Legistar agenda
S2b     Gemini naming pass, aligned deterministically onto Scribe's clusters
S3      coalesce turns; boundary reconciliation
S4      registry resolution — roster/persons/aliases as the spelling authority
S4b/S4c existing human review export + import (unchanged)
```

---

## 6. Harness defects found and fixed

Recorded because several inverted the ranking, and any future run should know these were
live.

| Defect | Effect | Fix |
|---|---|---|
| DER conflated *miss* with *unmapped cluster* | ~50% miss rate for six commercial diarizers at once | miss = system heard silence; unmapped cluster = confusion |
| Reference timeline overlapped itself | covered 138% of an 8-min window | flatten to atomic intervals, most-specific segment wins, clip to window |
| Entity accuracy gameable by silence | Rev.ai scored 1.000 and ranked first by never producing `A2Zero` | score against counts from verified text |
| Turn granularity not normalized | Deepgram returned 53 turns for one 3-min monologue; others returned 2 | coalesce all vendors before turn metrics |
| Review burden charged one name per cluster | **rewarded under-clustering** — 3 clusters for 14 people ranked first | charge for real speaker count; merged speakers cost a weld each |
| Case-variant in mangle list | `Arca` matched `ARCA`, halving the score | exclude variants differing only by case |
| Two runs of one vendor treated as independent | they voted together; AssemblyAI's odd-one-out read 0.26% instead of 3.26% | independence check before consensus |
| Locator matched common surnames | Harris/Brown/Watson/Lutz matched incidental speech; two Council windows mislabelled | search distinctive surnames and agenda phrases only |

Vendor API contracts learned the hard way: dashboard display names are not API
identifiers (`Nova-3` → `nova-3`, `Universal-3.5 Pro` → `universal-3-5-pro`); AssemblyAI
deprecated `speech_model` for `speech_models` and rejects `word_boost` on universal-3+
in favour of `keyterms_prompt`; Rev.ai requires `options` as a multipart part with an
explicit JSON content-type **and rejects any vocabulary phrase containing a digit** —
which structurally excludes `A2Zero`.

---

## 7. Unresolved

**Two Council windows are mislabelled and their results are not interpretable.** The
locator matched common surnames. The window labelled "public_comment" (48:30–56:30) is
mostly the budget presentation — public comment actually starts at 50:05 — and the one
labelled "deliberation" (64:30–72:30) is public comment mid-stream. Only roll call and
the separately-chosen crosstalk window are sound.

**Turn-boundary quality is unmeasured against truth.** We have agreement between vendors
but no gold segmentation on any meeting, so we know they disagree without knowing who is
right.

**Timestamp accuracy has no gold standard.** The reference is our own WhisperX forced
alignment, and WhisperX is a candidate. It is reported but never decisive. Making it real
means force-aligning human-verified *text* against audio.

**Roll-call DER measures agreement with a reference we know is defective.** On the
verified passage five of six systems score 0.005–0.019; on roll call everyone scores
~0.45. That region's boundaries came from output a human corrected in eight places.

**WER rests on one clean single-speaker passage.** No measurement under crosstalk, which
is where transcripts actually break.

**Cost and wall-clock are unmeasured.** `PRICE_PER_MIN` is entirely `None`; timings exist
only for 8-minute clips. The 113-minute and 155-minute behaviour of the async job APIs —
which matters at 375 hours of corpus — is untested.

**n=2 meetings, one city, one recording setup.** Both Ann Arbor, both CTN. Nothing here
transfers automatically to another jurisdiction's audio.

**Scribe v2's entity accuracy is the weakest of the speaker-per-word systems** (0.714).
The recommendation depends on §5.2 actually being built. If per-meeting vocabulary does
not materialise, Speechmatics at 0.750 and 14/14 speakers is the fallback.

---

## 8. Reproducing

```bash
python3 -m pipeline.bench.run --list          # which API keys are present
python3 -m pipeline.bench.run --check         # 3-second validation per vendor
python3 -m pipeline.bench.run --vendors all   # scored run
python3 -m pipeline.bench.council_run         # cross-vendor disagreement
python3 -m pipeline.bench.run --score-only    # re-score cached output, no API calls
```

Raw vendor output caches per vendor, so re-scoring after a metric change costs nothing
and never re-bills. Do **not** `source .env` — several values contain spaces and `source`
splits on them.

---

## 9. Addendum — duration testing invalidates §5.1 as written

Added 2026-08-07 after running the recommended stack on a real 35-minute window.

**§1–§8 measured every system on 8-minute windows.** Production meetings are 113–155
minutes. That is a scope error in the benchmark design, and correcting it changes the
recommendation.

### 9.1 Scribe v2 under-clusters as audio lengthens

Same meeting, same keyterms, same encoding:

| Audio | Clusters |
|---|---|
| 8 minutes | 14–15 (correct) |
| 35 minutes | **8** — merging distinct people |

At 35 minutes one cluster carried Chair Curtis, Levin *and* Overpeck; another carried
Berkowitz *and* Cornell. Nedrich's, Mazloomian's and Smyth's roll-call answers were
absorbed into the clerk's turn entirely.

Two candidate causes were ruled out by A/B on the identical 8-minute clip:

- **keyterms** — 15 clusters with, 14 without
- **mp3 encoding** — 64k → 14, 128k → 14, wav → 15

Duration is the remaining variable. Likely mechanism: with 35 minutes dominated by one
speaker (16.5 of them), a global clustering step allocates clusters to dominant voices
and folds two-second roll-call answers into them — precisely the utterances that carry
our speaker-enrollment signal.

### 9.2 Speechmatics does not degrade, but cannot spell

Same 35-minute window:

| | Scribe v2 | Speechmatics |
|---|---|---|
| Roster names correct | **14/15** | 8/15 |
| Roll-call answerers separated | 8 clusters, 3 merged into one | **12 distinct**, 1 merge |
| Clusters with ~0s of speech | 0 | **7** |
| Throughput | **87× realtime** | 17× realtime |

Speechmatics produced `Katari`, `Mazloomi Ann`, `Nadrich`, `Smith`, `Hans`,
`Colvin Garcia`; Scribe produced all six correctly. **Unverified:** the same 81 terms
were passed as `additional_vocab` without `sounds_like` pronunciation hints, so the
entity gap may be configuration rather than capability.

### 9.3 Revised recommendation

Neither system dominates; they fail in opposite directions. Use both, joined by the same
deterministic word-stream alignment already validated for the naming pass (94% word
agreement between vendors on the benchmark window, 100% precision on roster members):

```
words + entities + timestamps   ElevenLabs Scribe v2  (14/15 names, 87x realtime)
speaker boundaries at length    Speechmatics          (12/12 roll call at 35 min)
speaker NAMES                   Gemini, aligned deterministically
canonical spelling              persons / roster registry
```

Every join is inspectable arithmetic, not a model call.

**Fallback if two ASR passes is unacceptable:** chunk Scribe into ~8-minute windows where
its clustering is sound, and rejoin across chunks by resolved person name rather than by
cluster id. The gap: a speaker unnamed in two chunks cannot be linked, and Scribe returns
no embeddings — which is a reason to retain pyannote as an embedding extractor even after
retiring it as the diarizer.

### 9.4 Also established

- **ElevenLabs `keyterms` works, and is easy to get silently wrong.** It is a repeated
  multipart field per term. A JSON array fails as "invalid characters" (the brackets); a
  comma-joined string fails as "at most 4 spaces"; `keywords`, `prompt` and
  `biased_keywords` all return HTTP 200 **and are ignored**. Verified A/B, twice each:
  without keyterms 0 of 2 `A2Zero` correct; with keyterms 2 of 2.
- **Scribe is non-deterministic** — identical requests return different text. Any A/B on
  it needs repeat runs, or run-to-run variance reads as a parameter effect.
- Vendor limits now enforced in `pipeline/vocab.py`: ≤50 chars, ≤5 words, no `<>{}[]\`.
  One bad agenda line fails the whole request.
- Keyterms carry a **20% cost surcharge**; Scribe bills ~81 credits per audio minute.
