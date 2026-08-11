# Grapevine — Video to Claim Store

Turning recorded public meetings into a **claim store**: who asserted what, when, on what
evidence — with provenance strong enough that a claim can be traced back to the second of audio
it came from.

The working subject is the **Bryant neighborhood decarbonization project** in Ann Arbor, argued
across City Council, the Sustainability Commission, and the Energy and Environmental Commissions
it replaced in 2025.

> **Start with [`docs/video-analysis-argumentation-orientation-brief.md`](docs/video-analysis-argumentation-orientation-brief.md).**
> It explains what we tried, what we measured, what we chose, and what we are building first.

---

## The problem this is built against

A v1 prototype extracted one 113-minute meeting into structured claims. Review found that 45% of
turns carried another turn's extraction, 71 empty turns received fabricated claims, and **5 of 18
speaker identities were invented** — named at `confidence: 1.0` from footage the speakers never
appeared in.

So the failure mode designed against here is not "the model is wrong." It is:

> **the model is confidently, plausibly, checkably wrong, and nothing errors.**

Almost every structural decision in this repo — the vocabulary triggers, the publication gate, the
checksum on roll-call parsing, the refusal to let models vote on a name — follows from that one
sentence.

## Where it stands

| | |
|---|---|
| transcription + diarization | ElevenLabs Scribe v2, 8-minute chunks — chosen over 6 alternatives ([benchmark](docs/asr-benchmark.md)) |
| speaker naming | Gemini pass + deterministic word-stream alignment + registry resolution |
| word accuracy | ~99% |
| **turn-level attribution error** | **26%** — the number that shapes the architecture |
| corpus | 364 events · 261 videos (724 h) · 330 minutes/agenda documents · 1,771 persons |
| tests | 164 passing |

Because words are right and attribution is not:

> **An unverified transcript is safe to search and unsafe to quote.**

Everything is indexed automatically; verification is spent only on the spans an argument actually
rests on; and publication is gated so a claim can never reach a rendered page from unverified
ground.

## Reading order

| | | |
|---|---|---|
| 1 | [`CLAUDE.md`](CLAUDE.md) | orientation, the two architectural rules, the non-negotiables |
| 2 | [`docs/video-analysis-argumentation-orientation-brief.md`](docs/video-analysis-argumentation-orientation-brief.md) | **the brief** — why the pipeline looks like this, and what we build first |
| 3 | [`docs/PLAN.md`](docs/PLAN.md) | the approved architecture, including what was rejected and why |
| 4 | [`schema/claim_store.sql`](schema/claim_store.sql) | the DDL — the comments carry the reasoning, not just the types |
| 5 | `pipeline/transcribe_v2.py` → `naming.py` → `turns_v2.py` | the transcription path |
| 6 | `pipeline/export_review_v2.py` + `import_review.py` | the human review round-trip |
| 7 | [`tests/`](tests/) | 164 tests; the schema suite rebuilds Postgres from the DDL every run |
| 8 | [`docs/asr-benchmark.md`](docs/asr-benchmark.md) | full vendor evidence behind the stack choice |

## Running it

```bash
./scripts/db.sh setup     # conda env: postgresql 18.4 + pgvector (no sudo, removes cleanly)
./scripts/db.sh start     # port 5433
./scripts/db.sh reset     # drop + recreate + reapply schema
cp .env.example .env      # then fill in your own keys — .env is gitignored, never commit it
pytest tests/             # 164 tests; schema tests skip if no server is reachable
```

`./scripts/check-arch.sh` asserts the toolchain is native arm64. It has its own guard because the
regression is silent: an x86_64 `python3` or `ffmpeg` still produces *correct* output, just
without MPS or hardware acceleration, turning minutes into hours with nothing raised.

## Two rules that explain most of the code

**Semi-open ontology.** Every controlled vocabulary lives in `vocabulary_terms`, never in a
`CHECK` constraint. On an unrecognized term the trigger stores the closest approved term, logs a
proposal, and flags the entity — **it never rejects the insert**, because rejecting discards
extraction work. Humans approve growth.

**Embeddings propose; symbols decide.** Cosine similarity builds candidate sets. Registries,
rosters and vote records decide. No threshold appears in a finding, and a cluster ID never becomes
a canonical key.

## Data and provenance

All source material is public record: Ann Arbor Legistar (agendas, minutes, votes, rosters) and
CTN's public YouTube recordings of open meetings. Speaker registries name meeting participants —
commissioners, city staff, and members of the public who spoke on the record — as those meetings
are published by the city.

Voice embeddings are stored only for `is_public_figure = TRUE` and the restriction is enforced in
the database by `reject_private_voiceprint()`, not by convention.

## Related repositories

- **[`a2zero-wiki`](https://github.com/CalebElia/a2zero-wiki)** — the knowledge-graph pipeline that
  renders the Obsidian wiki from Ann Arbor's carbon-neutrality planning documents. Its
  `blackboard/quads.jsonl` was paused *pending schema redesign*; **this repository is that
  redesign**, and the video corpus is one source feeding it. Read its `CLAUDE.md` and `SCHEMA.md`
  for the wider vision.
- `video_analysis` — the v1 prototype being rewritten here. Retained as the eval negative control.

Neither is a live dependency. Nothing here writes to either.
