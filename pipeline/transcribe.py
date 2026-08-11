"""S1 — transcription with a per-jurisdiction ASR vocabulary, plus forced alignment.

WHAT CHANGED FROM v1, AND WHY IT MATTERS

v1 called Whisper with no `initial_prompt` at all. The cost was measured, not
theoretical: "A2Zero" — the name of the plan this entire project is about — came out
as "A20" in 11 of 11 occurrences and correctly zero times. Entity matching on the
program's own name would have returned nothing, silently.

`initial_prompt` conditions the decoder toward supplied spellings. Benchmarked on a
5-minute clip containing two known failures: without it, A2Zero 0 / A20 1; with it,
A2Zero 2 / A20 0.

FORCED ALIGNMENT. v1 used Whisper's native word timestamps, which are a by-product of
attention weights and drift. WhisperX realigns against a phoneme model, which matters
because S3 assigns every word to exactly one speaker turn by its midpoint — bad
timestamps put words in the wrong mouth — and because click-to-play provenance
ultimately rests on them.

DEVICE. CTranslate2 (faster-whisper's backend) exposes no Metal backend — verified on
this machine: compute types are cpu-only and CUDA device count is 0. So transcription
runs on CPU with int8 quantisation, ~1.5x realtime. The alignment pass uses torch and
CAN reach MPS. That split is deliberate, not an oversight.

Usage:
    python -m pipeline.transcribe lWvRVUMyLP4 --audio path/to/audio.mp3
"""
from __future__ import annotations

import argparse
import json
import re
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

REPO = Path(__file__).parent.parent
MODEL = "large-v2"          # matches v1, so differences are attributable to the prompt
DEVICE = "cpu"              # CTranslate2 has no Metal backend
COMPUTE = "int8"
ALIGN_DEVICE = "mps"        # torch-based; this one can use the GPU


def detect_prompt_leak(segments: list[dict], prompt: str,
                       min_run: int = 5, min_coverage: float = 0.5) -> list[dict]:
    """Find segments where the decoder emitted the PROMPT instead of the audio.

    `initial_prompt` is prepended as if it were prior transcript, so under acoustic
    uncertainty Whisper can simply continue it. Measured on lWvRVUMyLP4: 7 seconds of
    speech were replaced by 'Speakers include Missy Stults of the Office of
    Sustainability and Innovations and AmeriCorps members of the Ann Arbor Climate Corps
    members of the Ann Arbor Climate Corps.' — the prompt, with a clause repeated.

    NO STATISTICAL HEURISTIC FINDS THIS. That segment reads 231.9 wpm, dead centre for
    the speaker; it is grammatical, on-topic, and about the right length. It was caught
    by a human listening. The only reliable signal is that the text came FROM THE PROMPT.

    TWO CONDITIONS, because one is not enough. Matching a single phrase is meaningless —
    the prompt necessarily contains the terms people actually say, and Missy Stults
    really does introduce herself as director of "the Office of Sustainability and
    Innovations". Flagging that would train everyone to ignore the warning. What
    separates a leak is that the prompt words are most of the SEGMENT:

      1. a run of >= min_run consecutive words shared with the prompt, and
      2. >= min_coverage of the segment's words falling inside such runs.

    Measured on all 1284 segments of lWvRVUMyLP4 against the offending prompt: the leak
    scores 1.00 and is the ONLY hit. The nearest genuine mentions score 0.15 ("...I'm
    Missy Stults, the Director of our Office of Sustainability and Innovations..."), 0.26
    and 0.45 ("That's regulated by the MPSC, the Michigan Public Service Commission.").
    That last one is close to the threshold — a short sentence that is mostly a proper
    noun will always be — so treat a hit as "listen to this segment", not as proof.
    """
    def norm(s: str) -> list[str]:
        return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()

    pw = norm(prompt)
    grams = {tuple(pw[i:i + min_run]) for i in range(len(pw) - min_run + 1)}
    hits = []
    for s in segments:
        w = norm(s.get("text", ""))
        covered, runs = set(), []
        for i in range(len(w) - min_run + 1):
            g = tuple(w[i:i + min_run])
            if g in grams:
                covered.update(range(i, i + min_run))
                runs.append(" ".join(g))
        if not runs:
            continue
        coverage = len(covered) / len(w)
        if coverage >= min_coverage:
            hits.append({"start": round(s.get("start", 0), 1),
                         "end": round(s.get("end", 0), 1),
                         "text": (s.get("text") or "").strip(),
                         "matched": runs[0],
                         "coverage": round(coverage, 2)})
    return hits


def load_vocabulary(jurisdiction: str = "ann_arbor") -> dict:
    path = REPO / "registries" / jurisdiction / "asr_vocabulary.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No ASR vocabulary for {jurisdiction!r} at {path}. Custom vocabulary is "
            f"mandatory per jurisdiction — running without one is what produced 'A20'."
        )
    return json.loads(path.read_text())


def transcribe(audio_path: str | Path, video_id: str,
               jurisdiction: str = "ann_arbor", out_dir: Path | None = None) -> dict:
    import torch
    import whisperx

    vocab = load_vocabulary(jurisdiction)
    prompt = vocab["_prompt_template"]
    out_dir = out_dir or REPO / "processing" / video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"[S1] loading {MODEL} on {DEVICE}/{COMPUTE}")
    model = whisperx.load_model(MODEL, DEVICE, compute_type=COMPUTE,
                                language="en",   # skip detection; it costs ~30s and we know
                                asr_options={"initial_prompt": prompt})
    audio = whisperx.load_audio(str(audio_path))
    duration = len(audio) / 16000
    print(f"[S1] transcribing {duration/60:.0f} min with vocabulary "
          f"({len(prompt.split())} words of prompt)")
    result = model.transcribe(audio, batch_size=8)
    t_asr = time.time() - t0
    print(f"[S1] transcription done in {t_asr/60:.1f} min ({duration/t_asr:.1f}x realtime)")
    del model

    # Forced alignment — the reason for WhisperX over plain Whisper.
    align_device = ALIGN_DEVICE if torch.backends.mps.is_available() else "cpu"
    print(f"[S1] aligning on {align_device}")
    t1 = time.time()
    try:
        align_model, meta = whisperx.load_align_model(language_code="en", device=align_device)
        result = whisperx.align(result["segments"], align_model, meta, audio,
                                align_device, return_char_alignments=False)
        aligned = True
        del align_model
    except Exception as exc:
        # Alignment failing is recoverable — Whisper's native timestamps still exist and
        # S3 can work from them, just less precisely. Losing the whole run is not.
        print(f"[S1] WARNING alignment failed ({type(exc).__name__}: {exc}); "
              f"keeping unaligned segments")
        aligned = False
    print(f"[S1] alignment done in {(time.time()-t1)/60:.1f} min")

    # Prompt-leak check runs on EVERY transcription, before anything downstream can
    # consume the text. A leak is silent by construction — see detect_prompt_leak.
    leaks = detect_prompt_leak(result["segments"], prompt)
    if leaks:
        print(f"[S1] *** PROMPT LEAK: {len(leaks)} segment(s) contain prompt text ***")
        for h in leaks:
            print(f"[S1]     {h['start']:.0f}s-{h['end']:.0f}s matched {h['matched']!r}")
            print(f"[S1]     emitted: {h['text'][:110]!r}")
        print("[S1]     These segments are FABRICATED — the audio they cover was not "
              "transcribed. Shorten the prompt to a bare term list and re-run.")

    payload = {
        "video_id": video_id,
        "segments": result["segments"],
        "prompt_leaks": leaks,
        # Provenance travels with the artifact. media_assets.asr_model records the same,
        # so a transcript can always be traced to the model AND vocabulary that made it.
        "_provenance": {
            "asr_model": f"whisperx/{MODEL}",
            "device": DEVICE, "compute_type": COMPUTE,
            "word_alignment": "wav2vec2-forced" if aligned else "whisper-native",
            "vocabulary_jurisdiction": jurisdiction,
            "vocabulary_updated": vocab.get("updated"),
            "initial_prompt": prompt,
            "audio_seconds": round(duration, 1),
            "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
    out = out_dir / "transcript.json"
    out.write_text(json.dumps(payload))
    print(f"[S1] wrote {out} ({out.stat().st_size/1e6:.1f} MB, "
          f"{len(payload['segments'])} segments)")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="S1 transcription with custom vocabulary")
    ap.add_argument("video_id")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--jurisdiction", default="ann_arbor")
    args = ap.parse_args()
    transcribe(args.audio, args.video_id, args.jurisdiction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
