# Grapevine Claim Store

The unified claim store for Grapevine. One store, many corpora: video, plans, annual reports,
minutes, webpages, and — next wave — regulatory dockets.

**Read `docs/PLAN.md` first.** It is the approved architecture and carries every decision, why it
was made, and what was rejected. This file is orientation only.

## Where this lives

```
~/Developer/Grapevine/Coding_Projects/grapevine-claim-store
```

**GitHub: `CalebElia/Grapevine_ClaimStore` (private).** `origin` points there. A second remote,
`legacy`, still points at `Grapevine_Video2ClaimStore`, which is where the first 111 commits
were made and which is left intact; nothing pushes to it.

**NOT under `~/Desktop`, and not by accident.** Desktop is iCloud-synced, and iCloud is a
writer rather than a snapshotter — it reaches into the directory and creates, restores and
duplicates files there. It had written conflict copies *inside* `.git/`: an `index 2` beside
the staging area git rewrites non-atomically on every add and commit, and a stale
`refs/remotes/origin/main 2`, which is what `git fsck` was reporting. It had also restored 196
weeks-old `name 2.ext` copies into the working tree mid-session, which pytest collected by
glob — the suite silently grew from 1280 tests to 2395, half of them running stale code and
passing.

`.gitignore` now catches `* 2.*`, `* [0-9].*` and `* copy*`, but that only stops them reaching
git and pytest; it does not stop a sync client racing a write. **Keep the code out of synced
directories.** The project-management documents stay under `~/Desktop/Grapevine/`, where sync
is the point.

Paths to siblings are relative (`../a2zero-wiki`), and test fixtures resolve from the repo
root, so the tree can move again without edits. When something breaks after a move it tends to
break *quietly* — the one file with an absolute path skipped six tests with "source PDFs not
present" and still reported a green run.

## What this replaces

Two efforts that turned out to be the same effort:

| Repo | Status | Relationship |
|---|---|---|
| `../a2zero-wiki` | **READ-ONLY. Do not modify.** | Source of seed registries (229 initiatives, 154 actors, 7 strategies) via a one-way export. Its `blackboard/quads.jsonl` was paused "pending schema redesign" — this repo is that redesign. |
| `../video_analysis` | **READ-ONLY. Do not modify.** | Source of cached ASR/diarization intermediates for `lWvRVUMyLP4`, and of the v1 code being rewritten here. Its `output/lWvRVUMyLP4.json` is the eval **negative control**. |

Neither sibling is a live dependency. Nothing here writes to either.

## Local database

**Native arm64 via conda-forge, deliberately NOT Homebrew.** The original trigger was that brew on
this machine was a migrated Intel install at `/usr/local` running under Rosetta, so everything it
built was x86_64 translated. **That is history** — the Intel brew was cleaned up on 2026-07-28 and
brew is now native arm64 at `/opt/homebrew` (`brew config`: `CPU: dodeca-core 64-bit arm_brava`,
`Rosetta 2: false`, macOS 26.5.2-arm64). The choice stands on its own merits regardless: conda's
subdir here is `osx-arm64`, it needs no sudo, and it removes cleanly with
`conda env remove -n grapevine-db`.

```bash
./scripts/db.sh setup     # one time — conda env with postgresql 18.4 + pgvector
./scripts/db.sh start     # port 5433, trust auth, unix socket in /tmp + loopback TCP
./scripts/db.sh reset     # drop + recreate + reapply schema
./scripts/db.sh psql      # interactive
```

`db.sh` connects over the `/tmp` socket, but `listen_addresses` is left at its Postgres default of
`localhost`, so the server also answers on `127.0.0.1:5433` and `[::1]:5433`, with `trust` in
`pg_hba.conf` for both. That TCP path is the one GUI clients use — Postico 2 is configured against
it. It is **loopback only**: nothing binds a routable interface, so this is not network exposure.
It does mean any local process can connect as `grapevine` without a password, which is the
intended trade for a scratch database on a single-user machine.

`tests/test_schema_runtime.py` builds a throwaway database and applies the full DDL on every
run, so the schema is verified continuously rather than once. It **skips** rather than fails
when no server is reachable.

## Toolchain architecture

Same reasoning as the database, same solution. `ffmpeg` used to be the x86_64 Homebrew build at
`/usr/local/bin/ffmpeg`; it is now native arm64 from conda-forge in a dedicated `grapevine-media`
env, exposed as `~/.local/bin/ffmpeg` (a symlink — `~/.local/bin` is first on PATH, so it wins
over both `/usr/local/bin` and any future `/opt/homebrew/bin` without editing a profile).

```bash
./scripts/check-arch.sh   # asserts the whole toolchain is native; exit 1 if not
```

**Why this has its own guard.** The regression is silent. An x86_64 `python3` or `ffmpeg`
appearing earlier on PATH still produces *correct* output — S1/S2 just lose the **MPS** backend
and fall back to CPU, turning minutes of transcription into hours with no error, no warning, and
no failed assertion. Nothing about the result looks wrong. `tests/test_toolchain_arch.py` runs
`check-arch.sh` on every `pytest tests/`, so the check fires without anyone remembering it.

Unlike `test_schema_runtime.py`, this one **never skips** on Apple Silicon — skipping is itself
the failure mode. It gates on `sysctl hw.optional.arm64` (the hardware) rather than
`platform.machine()` (the process), because a translated interpreter reports `x86_64` for the
latter and would skip precisely when it should fail.

One correction to a belief worth not re-deriving: **Rosetta does not cost you VideoToolbox.**
Verified on this machine — the x86_64 ffmpeg lists the `*_videotoolbox` encoders and really does
hardware-encode, because VideoToolbox is a system framework and Rosetta translates the calls.
What Rosetta actually costs is the CPU side: `x264`/`x265`/`libaom` ship hand-written arm64 NEON
assembly a translated build can never reach, and every filter, resample and audio-extract path
runs translated.

## Layout

```
schema/       claim_store.sql — the DDL. Start here.
              vocabularies.sql — seed terms for the semi-open ontology
migrations/   ordered, applied-once SQL
pipeline/     S0–S9 stages (see PLAN.md Part V)
registries/   per-jurisdiction config: ASR vocabulary, body rosters, Legistar ids
scripts/      db.sh (local postgres) · check-arch.sh (native-toolchain guard)
              status.sh (where the store stands) · review.sh (mention-review UI)
              dedup.sh (duplicate subjects and orgs; proposes, never merges)
tests/        pytest; fixtures/ holds copies of cached v1 intermediates
docs/         PLAN.md (approved plan) · v1-code-review.md (evidence for the rewrite)
              working-the-mention-queue.md (how to rule on queued mentions)
```

Every `scripts/*.sh` cd's to the repo root first. `python3 -m pipeline.X` resolves only from
there, and when it does not the failure is a `ModuleNotFoundError` on stderr that reads as
"the command did nothing".

## Two architectural rules

**1. Semi-open ontology.** Every controlled vocabulary lives in `vocabulary_terms`, not in a
`CHECK` constraint and not in a SQL comment. On an unrecognized term the trigger stores the closest
approved term, logs a `vocabulary_proposals` row, increments `occurrences`, and flags the entity.
**It never rejects the insert** — rejecting discards extraction work. Humans approve growth.

**2. The corpus grows.** `doc_type` is a vocabulary, not an enum. Dark matter →
`research_questions` → `source_targets` → new sources. The action-discovery process that consumes
those tables is a parallel effort; this repo owns the emit side only.

## Non-negotiables

- **`verbatim` is NOT NULL and non-empty** on every claim-bearing table. v1 produced 71 fabricated
  claims from empty input; this is the schema-level answer.
- **Claims are never deduplicated.** Each is one actor at one moment. Repeated *facts* resolve at
  `asserted_events`; repeated *reasons* at `arguments`. Merging assertions destroys propagation.
- **Corroboration is independence, not count.** `event_attestations.attestation_type` +
  `derives_from_attestation_id`. Six annual reports restating one sentence is one source.
- **Embeddings propose; symbols decide.** Cosine for candidate sets only. No threshold in a finding.
- **A cluster ID never becomes a canonical key.** `named_by` is human on subjects, issues,
  arguments, asserted_events.
- **Dark matter is a lead queue, never a finding.** Every gap cites `source_search_log` or is
  labelled unsearched.

## Time

Chronology is the join key across every source, so time is modelled twice:

- **Utterance/publication time** — when it was said or published
- **World time** — `claims.asserted_start` / `asserted_end` / `asserted_precision` /
  `asserted_calendar` / `asserted_date_text`: when the asserted thing happened

**Ann Arbor fiscal year: July 1 → June 30** (City Charter). `FY25` = 2024-07-01 → 2025-06-30.
An FY25 claim and a calendar-2025 claim overlap by only six months — **timeline queries compare
intervals, never `asserted_start` alone.**

## Working preferences

Blunt over sycophantic. Push back on soft answers. Name the assumption before asking the question.
Assume real fluency in policy and climate; interrogate AI/ML choices in more detail.
