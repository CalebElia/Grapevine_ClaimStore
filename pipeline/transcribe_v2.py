"""S1+S2 — transcription AND diarization in one call, via ElevenLabs Scribe v2.

WHAT THIS REPLACES AND WHY. v2's S1/S2 ran WhisperX for words and pyannote for speakers,
then joined them by timestamp. That join was the source of most of our defects, not the
transcription — WhisperX scored 99.3% word accuracy on the passage a human verified:

  * 38% of pyannote segments fell under 1.5s, below the floor where a speaker embedding
    carries usable information
  * 141 words straddled a speaker-change boundary and were assigned by MIDPOINT
  * 67% of interjections carried nothing usable (33 of 70 were literally empty)
  * a residual bin of 15 sub-second fragments was confidently named as a real person

Scribe returns a SPEAKER LABEL ON EVERY WORD. There is no join, so that entire class of
error cannot occur. See docs/asr-benchmark.md for the measurements behind the choice:
14/14 speakers recovered unprompted on this meeting, 12/12 on Council roll call, zero
welded turns, best cluster purity of the seven systems tested.

THE ONE KNOWN WEAKNESS is entity accuracy — 0.714, the worst of the speaker-per-word
systems, rendering 'A20' where the term is 'A2Zero'. That is what pipeline/vocab.py is
for, and why keyterms are mandatory here rather than optional.

Usage:
    python -m pipeline.transcribe_v2 lWvRVUMyLP4 --audio processing/lWvRVUMyLP4/audio.m4a
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
MODEL = "scribe_v2"
MAX_UPLOAD_MB = 90          # keep well inside request limits for a 2-3 hour meeting

# CHUNKING IS NOT AN UPLOAD-SIZE WORKAROUND. Scribe's speaker recall degrades with both
# audio length and speaker-time imbalance, and the two compound. Measured on one meeting,
# recall = clusters found / people actually present:
#
#                     balanced     lopsided
#      8 minutes        0.97         0.88
#     35 minutes        0.82         0.53
#
# The worst cell — long, with one dominant presenter — is what most of the corpus looks
# like. Chunking at 8 minutes hands the naming pass 0.88-0.97 recall instead of 0.53, so
# Gemini corrects a few merges rather than carrying the whole job.
#
# Stitching is safe because the JOIN KEY IS THE PERSON'S NAME, not the cluster id. Cluster
# ids are namespaced per chunk and deliberately never compared across chunks.
CHUNK_S = 480.0
OVERLAP_S = 3.0             # so a word cut at a boundary survives in one chunk intact


def prepare_audio(src: Path, out: Path, start: float = 0.0,
                  duration: float | None = None) -> Path:
    """64 kbps mono mp3. A 113-minute meeting as 16 kHz wav is ~217 MB; the same audio as
    mp3 is ~54 MB. Scribe is not sensitive to the difference at this bitrate and the
    upload is what actually fails on long meetings."""
    if out.exists():
        return out
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if start:
        cmd += ["-ss", str(start)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)]
    subprocess.run(cmd, check=True)
    mb = out.stat().st_size / 1e6
    print(f"[S1v2] prepared {out.name} ({mb:.0f} MB)")
    if mb > MAX_UPLOAD_MB:
        print(f"[S1v2] WARNING {mb:.0f} MB exceeds the {MAX_UPLOAD_MB} MB comfort limit — "
              f"if the request fails, split the meeting and merge the word streams.")
    return out


def _post(mp3: Path, terms: list[str], key: str) -> dict:
    import httpx
    parts = [("file", (mp3.name, mp3.read_bytes(), "audio/mpeg"))]
    parts += [("keyterms", (None, t)) for t in terms]
    with httpx.Client(timeout=3600) as c:
        r = c.post("https://api.elevenlabs.io/v1/speech-to-text",
                   headers={"xi-api-key": key}, files=parts,
                   data={"model_id": MODEL, "diarize": "true",
                         "timestamps_granularity": "word"})
    if r.status_code >= 400:
        raise RuntimeError(f"elevenlabs {r.status_code}: {r.text[:400]}")
    return r.json()


def transcribe_chunked(audio: Path, video_id: str, event_id: int | None = None,
                       body_slug: str = "ann_arbor_sustainability_commission",
                       out_dir: Path | None = None, chunk_s: float = CHUNK_S) -> dict:
    """Full meeting in CHUNK_S windows, word streams concatenated."""
    from pipeline import vocab as V
    from pipeline.config import require
    out_dir = out_dir or REPO / "processing" / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    vb = V.build(event_id, body_slug=body_slug)
    terms, key = vb["terms"], require("ELEVENLABS_API_KEY")

    total = float(subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(audio)], capture_output=True, text=True).stdout.strip())
    n = int(total // chunk_s) + (1 if total % chunk_s else 0)
    print(f"[S1v2] {total/60:.0f} min -> {n} chunks of {chunk_s/60:.0f} min "
          f"({len(terms)} keyterms)")

    words, clusters, t0 = [], [], time.time()
    for i in range(n):
        start = i * chunk_s
        dur = min(chunk_s + OVERLAP_S, total - start)
        if dur <= 0.5:
            break
        mp3 = prepare_audio(audio, out_dir / f"chunk_{i:03d}.mp3", start, dur)
        d = _post(mp3, terms, key)
        got = [w for w in d.get("words", []) if w.get("type") in (None, "word")]
        # Namespace the cluster id: `speaker_1` in chunk 0 and chunk 5 are different
        # people as far as we are concerned, and pretending otherwise is the bug.
        cs = sorted({f"c{i:02d}_{w.get('speaker_id')}" for w in got if w.get("speaker_id")})
        clusters += cs
        kept = 0
        for w in got:
            ab = start + (w.get("start") or 0)
            if words and ab < words[-1]["end"] - 0.01:      # drop the overlap re-run
                continue
            words.append({"text": w.get("text", ""), "start": round(ab, 3),
                          "end": round(start + (w.get("end") or 0), 3),
                          "speaker": f"c{i:02d}_{w.get('speaker_id') or 'unknown'}"})
            kept += 1
        print(f"  chunk {i+1}/{n}  {start/60:5.1f}-{(start+dur)/60:5.1f} min  "
              f"{kept:5d} words  {len(cs):2d} clusters")

    wall = time.time() - t0
    print(f"[S1v2] done in {wall/60:.1f} min — {len(words)} words, "
          f"{len(clusters)} per-chunk clusters across {n} chunks")
    payload = {"video_id": video_id, "words": words, "speakers": clusters,
               "_provenance": {
                   "asr_model": f"elevenlabs/{MODEL}", "chunked": True,
                   "chunk_seconds": chunk_s, "overlap_seconds": OVERLAP_S,
                   "chunks": n, "keyterms": terms,
                   "keyterm_sources": {k: len(v) for k, v in vb["sources"].items()},
                   "cluster_ids_are_per_chunk":
                       "namespaced c<NN>_ — never compare across chunks; the naming "
                       "pass rejoins by person, not by cluster",
                   "audio_seconds": round(total, 1), "wall_seconds": round(wall, 1),
                   "event_id": event_id,
                   "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               }}
    out = out_dir / "scribe.json"
    out.write_text(json.dumps(payload))
    print(f"[S1v2] wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return payload


def transcribe(audio: Path, video_id: str, event_id: int | None = None,
               body_slug: str = "ann_arbor_sustainability_commission",
               out_dir: Path | None = None, start: float = 0.0,
               duration: float | None = None) -> dict:
    import httpx
    from pipeline import vocab as V
    from pipeline.config import require

    out_dir = out_dir or REPO / "processing" / video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    vb = V.build(event_id, body_slug=body_slug)
    terms = vb["terms"]
    print(f"[S1v2] {len(terms)} keyterms from {', '.join(vb['sources'])}")

    # Scribe bills ~81 credits per minute of audio. Windowing exists so a sample run
    # does not consume a whole quota — the full 113-minute meeting costs 9,120.
    tag = "" if not duration else f"_{int(start)}-{int(start+duration)}"
    mp3 = prepare_audio(audio, out_dir / f"audio_64k{tag}.mp3", start, duration)
    key = require("ELEVENLABS_API_KEY")
    t0 = time.time()
    print(f"[S1v2] uploading to {MODEL} ...")
    # keyterms is a LIST — one repeated multipart field per term, NOT a JSON blob and NOT
    # a comma-joined string. Both of those were accepted-then-rejected in ways that
    # misdescribe the problem: JSON fails as "some keyword contains invalid characters"
    # (the brackets), comma-joined fails as "each keyword can contain at most 4 spaces".
    # Verified A/B on a 2-minute clip containing two 'A2Zero' mentions, twice each:
    # without keyterms 0 correct both runs; with keyterms 2 of 2 correct both runs.
    parts = [("file", (mp3.name, mp3.read_bytes(), "audio/mpeg"))]
    parts += [("keyterms", (None, t)) for t in terms]
    with httpx.Client(timeout=3600) as c:
        r = c.post("https://api.elevenlabs.io/v1/speech-to-text",
                   headers={"xi-api-key": key}, files=parts,
                   data={"model_id": MODEL, "diarize": "true",
                         "timestamps_granularity": "word"})
    if r.status_code >= 400:
        raise RuntimeError(f"elevenlabs {r.status_code}: {r.text[:500]}")
    d = r.json()
    wall = time.time() - t0

    words = [w for w in d.get("words", []) if w.get("type") in (None, "word")]
    speakers = sorted({str(w.get("speaker_id")) for w in words if w.get("speaker_id")})
    dur = max((w.get("end") or 0) for w in words) if words else 0
    print(f"[S1v2] done in {wall/60:.1f} min — {len(words)} words, "
          f"{len(speakers)} speaker clusters, {dur/60:.0f} min of audio")

    payload = {
        "video_id": video_id,
        "words": [{"text": w.get("text", ""), "start": w.get("start"),
                   "end": w.get("end"), "speaker": str(w.get("speaker_id") or "unknown")}
                  for w in words],
        "speakers": speakers,
        "_provenance": {
            "asr_model": f"elevenlabs/{MODEL}",
            "diarization": "joint — speaker label on every word, no ASR/diarization join",
            "keyterms": terms,
            "keyterm_sources": {k: len(v) for k, v in vb["sources"].items()},
            "event_id": event_id,
            "audio_seconds": round(dur, 1),
            "window": {"start": start, "duration": duration},
            "wall_seconds": round(wall, 1),
            "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "replaces": "whisperx large-v2 + pyannote community-1 (see docs/asr-benchmark.md)",
        },
    }
    out = out_dir / f"scribe{tag}.json"
    out.write_text(json.dumps(payload))
    print(f"[S1v2] wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="S1+S2 via ElevenLabs Scribe v2")
    ap.add_argument("video_id")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--event", type=int)
    ap.add_argument("--body", default="ann_arbor_sustainability_commission")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float,
                    help="seconds; Scribe bills ~81 credits per minute of audio")
    ap.add_argument("--chunked", action="store_true",
                    help="full meeting in 8-min chunks (recommended — see CHUNK_S)")
    a = ap.parse_args()
    import pipeline.config as cfg
    cfg.load_dotenv()
    if a.chunked:
        transcribe_chunked(Path(a.audio), a.video_id, a.event, a.body)
    else:
        transcribe(Path(a.audio), a.video_id, a.event, a.body,
                   start=a.start, duration=a.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
