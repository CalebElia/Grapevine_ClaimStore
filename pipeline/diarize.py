"""S2 — speaker diarization on pyannote 4.x.

WHAT CHANGED FROM v1

v1 ran pyannote 3.1 and produced 23 clusters for what human review established were
20 people: Brooks Curtis, Mallika Kothari, and Missy Stults were each split across two
clusters. That over-clustering is not cosmetic — a split speaker becomes two anonymous
identities, and every claim attributed to the second one loses its link to the person.

This runs 4.0.7 against the same audio so the two are directly comparable. Whether the
major version fixes the split on its own is a measurement, not an assumption.

TORCHCODEC. pyannote 4.x prefers torchcodec for decoding file paths, and torchcodec on
this machine cannot load: it supports FFmpeg 4-7 and ours is 8.1.2 (libavutil.60).
Rather than downgrade ffmpeg to satisfy a decoder we do not need, audio is decoded
separately and handed to the pipeline as an in-memory waveform — the path pyannote's
own warning recommends, and the one WhisperX already uses internally.

DEVICE. pyannote is torch-based, so unlike faster-whisper it can reach MPS.

Usage:
    python -m pipeline.diarize lWvRVUMyLP4 --audio path/to/audio.mp3
"""
from __future__ import annotations

import argparse
import json
import os
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

REPO = Path(__file__).parent.parent
MODEL = "pyannote/speaker-diarization-community-1"
SAMPLE_RATE = 16000


def diarize(audio_path: str | Path, video_id: str, hf_token: str,
            out_dir: Path | None = None,
            min_speakers: int | None = None,
            max_speakers: int | None = None) -> dict:
    import torch
    from pyannote.audio import Pipeline

    out_dir = out_dir or REPO / "processing" / video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[S2] loading {MODEL}")
    t0 = time.time()
    pipeline = Pipeline.from_pretrained(MODEL, token=hf_token)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        pipeline.to(torch.device(device))
    except Exception as exc:
        print(f"[S2] could not move pipeline to {device} ({exc}); staying on cpu")
        device = "cpu"
    print(f"[S2] running on {device}")

    # Decode with ffmpeg rather than torchcodec — see module docstring.
    import whisperx
    audio = whisperx.load_audio(str(audio_path))
    waveform = torch.from_numpy(audio[None, :])
    duration = audio.shape[0] / SAMPLE_RATE
    print(f"[S2] diarizing {duration/60:.0f} min")

    kwargs = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    result = pipeline({"waveform": waveform, "sample_rate": SAMPLE_RATE}, **kwargs)
    dt = time.time() - t0

    # pyannote 4.x BREAKING CHANGE: returns DiarizeOutput, not an Annotation. The 3.x
    # call `result.itertracks(...)` raises AttributeError — after the full 114 minutes
    # of compute have already been spent, which is a costly way to find out.
    #   .speaker_diarization            Annotation, may contain overlapping turns
    #   .exclusive_speaker_diarization  Annotation with overlaps removed
    #   .speaker_embeddings             ndarray, one centroid per cluster
    def _tracks(ann):
        return [{"start": round(t.start, 3), "end": round(t.end, 3), "speaker": spk}
                for t, _, spk in ann.itertracks(yield_label=True)]

    segments = _tracks(result.speaker_diarization)
    exclusive = _tracks(result.exclusive_speaker_diarization)
    speakers = sorted({s["speaker"] for s in segments})

    # THE REASON THIS UPGRADE WAS WORTH IT. 3.1 computed embeddings internally and
    # discarded them; 4.x hands them back. person_voiceprints has been waiting for a
    # source — one centroid per cluster is exactly what cross-meeting speaker matching
    # needs, and what makes accuracy compound as more meetings are processed.
    emb = getattr(result, "speaker_embeddings", None)
    embeddings = None
    if emb is not None:
        embeddings = {spk: emb[i].tolist() for i, spk in enumerate(speakers)}
        print(f"[S2] captured {len(embeddings)} speaker embeddings, dim={emb.shape[1]}")
    print(f"[S2] done in {dt/60:.1f} min — {len(segments)} segments, "
          f"{len(speakers)} speaker clusters")

    payload = {
        "video_id": video_id,
        "segments": segments,
        # Overlap-free variant. Better input for word->turn assignment, since a word
        # cannot honestly belong to two speakers at once.
        "exclusive_segments": exclusive,
        "speaker_embeddings": embeddings,
        "_provenance": {
            "diarization_model": MODEL,
            "pyannote_version": __import__("importlib.metadata", fromlist=["version"])
                                .version("pyannote.audio"),
            "device": device,
            "audio_seconds": round(duration, 1),
            "n_clusters": len(speakers),
            "n_segments_overlapping": len(segments),
            "n_segments_exclusive": len(exclusive),
            "embedding_dim": (emb.shape[1] if emb is not None else None),
            "diarized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # v1 for comparison: pyannote 3.1, 1198 segments, 23 clusters for 20 people.
            "v1_baseline": {"model": "pyannote/speaker-diarization-3.1",
                            "segments": 1198, "clusters": 23, "actual_people": 20},
        },
    }
    out = out_dir / "diarization.json"
    out.write_text(json.dumps(payload))
    print(f"[S2] wrote {out}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="S2 speaker diarization")
    ap.add_argument("video_id")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    args = ap.parse_args()

    from pipeline.config import require
    token = require("HF_TOKEN")
    diarize(args.audio, args.video_id, token,
            min_speakers=args.min_speakers, max_speakers=args.max_speakers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
