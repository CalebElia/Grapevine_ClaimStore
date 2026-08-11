# The reference run — Sustainability Commission, 10 March 2026

One meeting, processed end to end and reviewed by a human against the video. This is the
**eval baseline**: when a pipeline change claims an improvement, it is measured here.

Video: [`lWvRVUMyLP4`](https://www.youtube.com/watch?v=lWvRVUMyLP4) · 114 minutes · 270 turns ·
18,045 words

The meeting was reviewed **twice** — once under the previous pipeline (4 Aug) and once under the
current one (8 Aug) — so the artefacts here come from two passes and their counts differ on
purpose. The fuller second pass produced the 22 confirmed identities now in
[`registries/ann_arbor/speaker_registry.json`](../registries/ann_arbor/speaker_registry.json).

Everything here is derived from a public meeting recorded and published by CTN, with agenda,
minutes and roster from Ann Arbor Legistar.

## Files

| file | what it is |
|---|---|
| `named.json` | **machine output.** Turns with timings, speaker names, name tier, attribution risk, cluster ids and boundary flags. No human corrections applied — this is what the pipeline produced on its own. |
| `sustainability-commission-meeting-3-10-2026-v2.xlsx` | **the review workbook**, with a real reviewer's annotations still in it. Three sheets: `Start here` (legend), `Transcript` (329 rows), `Speakers` (25 rows). |
| `corrections.json` | structured corrections from the **4 Aug** round-trip, keyed by `utterance_key`, with the source workbook's SHA-256 recorded so a correction can always be traced to the file it came from. |
| `speakers.reviewed.json` | the 15 identities confirmed in that same 4 Aug pass. Written as `*.reviewed.json` and never back into `speakers.json`, so re-running a stage can never clobber human work. |

Audio is not included (282 MB). Re-fetch it from the video id when you need it.

## Read the workbook first, and read the Speakers tab's notes

The most useful thing here is not the machine output — it is the record of a human arguing
with it. Actual reviewer notes:

> *"Correct first name, missing last name. Pulled from video name tag."*
> *"Don't know where the proposed name comes from, it's definitely Carlene"*
> *"This may be a catch all artifact for messed up turns."*

That third note found a real defect: a cluster the pipeline had confidently named was in fact
absorbing turns from several speakers.

## What this run measured

| | |
|---|---|
| word accuracy | ~99% |
| speech carrying a name | 92% |
| **turn-level attribution error** | **26%** |
| review time | 27 minutes (9 on speakers, 18 on transcript) |
| annotations needed | 23 (down from 47 under the previous pipeline) |

**Quality doubled; time barely moved** — because the reviewer watched the video either way.
That result is why the architecture verifies *spans an argument rests on* rather than whole
meetings. The reasoning is in
[`docs/video-analysis-argumentation-orientation-brief.md`](../docs/video-analysis-argumentation-orientation-brief.md),
Part 2.

## Two things to know before you trust these files

**`named.json` has no `speaker` field.** Names live in `speaker_name`, alongside `name_tier`
(`roster` / `persons` / `proposed` / `raw`) and `attribution_risk`. Those are *two different
confidences* and conflating them is a mistake we have already paid for: `name_tier` says
whether the NAME is real, `attribution_risk` says whether THIS TURN belongs to that person. A
turn can carry a perfectly real name and still be the wrong speaker — which is precisely what
most of the 26% looks like.

**The tiers are calibrated on the wrong axis.** On the Speakers tab, green (high tier) had a 0%
error rate. On the Transcript tab, green had 15%. Same reason as above. Sixteen of 21 name
corrections landed on turns of four words or fewer — "So moved.", "Second." — where there is
almost no acoustic or linguistic evidence to go on.

## Reproducing it

```bash
python -m pipeline.vocab          lWvRVUMyLP4          # keyterms from registry + roster + agenda
python -m pipeline.transcribe_v2  lWvRVUMyLP4 --audio <file.mp3> --chunked
python -m pipeline.naming         lWvRVUMyLP4
python -m pipeline.turns_v2       lWvRVUMyLP4
python -m pipeline.export_review_v2 lWvRVUMyLP4
```

Two gaps in that chain, both known and neither hidden:

- **Nothing fetches the audio.** `transcribe_v2.py` takes `--audio <file>`; the mp3 for this run
  was pulled by hand. `ctn_channel.py` and `video_validation.py` only read metadata.
- **`import_review.py` reads the older v1 workbook format** and will raise on the v2 workbook
  above. The corrections in `corrections.json` came from the v1 review; the v2 review's results
  were carried into `registries/ann_arbor/speaker_registry.json` manually.

Also note that **Scribe v2 is non-deterministic** — identical requests return slightly different
text, so a re-run will not reproduce `named.json` byte for byte. Compare on metrics, not on diff.
