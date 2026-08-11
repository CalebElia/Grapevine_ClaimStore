"""Benchmark runner. One clip, many vendors, one comparable table.

    python -m pipeline.bench.run --list                 # which keys are present
    python -m pipeline.bench.run --clip                 # cut the 0:00-8:00 test clip
    python -m pipeline.bench.run --vendors local        # score the incumbent
    python -m pipeline.bench.run --vendors all          # everything with a key
    python -m pipeline.bench.run --score-only           # re-score cached runs, no calls

Raw vendor output is cached under processing/<id>/bench/raw/, so re-scoring after a
metric change costs nothing and never re-bills an API. Scoring and calling are separate
on purpose: metrics WILL change as we learn what matters, and re-running $-per-minute
transcription to try a new formula is how a benchmark stops being run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from pipeline.bench import adapters as A
from pipeline.bench import ground_truth as GT
from pipeline.bench import metrics as M
from pipeline.bench.types import BenchTranscript, Turn, Word

REPO = Path(__file__).parent.parent.parent

# Load .env before anything reads a key — otherwise `--list` reports every key missing
# on a machine where they are all correctly configured, which is worse than useless.
# config.load_dotenv() is called lazily inside get()/require(), so importing the module
# is not enough; it has to be invoked.
import pipeline.config as _cfg  # noqa: E402

_cfg.load_dotenv()


def clip_path(video_id: str) -> Path:
    return REPO / "processing" / video_id / "bench" / "clip_0-480.wav"


def make_clip(video_id: str, start: float, end: float) -> Path:
    """Cut the benchmark window. 16kHz mono wav — the common denominator every API
    accepts, and what pyannote/WhisperX already consume, so no vendor gets a codec
    advantage the others don't have."""
    out = clip_path(video_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = REPO / "processing" / video_id / "audio.m4a"
    if not src.exists():
        print(f"[bench] downloading audio for {video_id}")
        subprocess.run(["yt-dlp", "-f", "bestaudio", "-o", str(src),
                        f"https://www.youtube.com/watch?v={video_id}"], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ss", str(start),
                    "-to", str(end), "-ac", "1", "-ar", "16000", str(out)], check=True)
    print(f"[bench] {out} ({out.stat().st_size/1e6:.1f} MB, {end-start:.0f}s)")
    return out


def raw_path(video_id: str, vendor: str) -> Path:
    return REPO / "processing" / video_id / "bench" / "raw" / f"{vendor}.json"


def to_transcript(d: dict) -> BenchTranscript:
    turns = [Turn(t["speaker"], t["start"], t["end"], t["text"],
                  [Word(**w) for w in t.get("words", [])],
                  t.get("speaker_name")) for t in d["turns"]]
    return BenchTranscript(d["vendor"], d["model"], turns, d["audio_seconds"],
                           d["wall_seconds"], d["has_word_timestamps"],
                           d["has_word_speakers"], d["names_speakers"],
                           d.get("cost_usd"), None, d.get("notes", []))


def score(tx: BenchTranscript, gt: dict) -> dict:
    ref = [M.RefSegment(s["start"], s["end"], s["speaker"]) for s in gt["speaker_segments"]]
    full = " ".join(t.text for t in sorted(tx.turns, key=lambda x: x.start))

    # word accuracy is scored ONLY on verified passages — the one place we know the
    # exact words. Scoring against machine output would measure agreement, not accuracy.
    word = {"wer": None, "ref_words": 0}
    for p in gt["verified_passages"]:
        got = " ".join(t.text for t in tx.turns
                       if t.end > p["start"] and t.start < p["end"])
        word = M.wer(p["text"], got)
        word["passage"] = f"{p['start']:.0f}-{p['end']:.0f}s"
        break

    # Expected counts come from the human-verified passages — the only place we know
    # how many times a term was really said. Without this, silence outscores accuracy.
    import re as _re
    verified_text = " ".join(p["text"] for p in gt["verified_passages"]).lower()
    expected = {k: len(_re.findall(_re.escape(k.lower()), verified_text))
                for k in gt["entities"]}
    expected = {k: v for k, v in expected.items() if v}
    ent = M.entity_accuracy(full, gt["entities"], expected)
    der = M.diarization_error(tx, ref)
    health = M.cluster_health(tx, ref)
    nm = M.naming(tx, set(gt["speaker_names"]))
    # Normalise segmentation granularity before judging turn legibility — otherwise the
    # metric measures each vendor's definition of "turn" rather than its quality.
    tx_c = M.coalesce(tx)
    turn = M.turn_quality(tx_c, ref)
    turn["turns_raw"] = len(tx.turns)
    ts = M.timestamp_accuracy(tx, [(w, t) for w, t in gt["timestamp_reference_words"]])
    cov = M.coverage(tx)
    burden = M.review_burden(word, health, turn, nm, ent)
    price = A.PRICE_PER_MIN.get(tx.vendor)

    # SELF-REFERENCE GUARD. The reference timeline and the word timings were both
    # produced by the local pipeline, so scoring `local` against them measures agreement
    # with itself, not accuracy. A DER of 0.000 is not a result. Flagging it in the
    # payload rather than only in a docstring, because docstrings do not survive into a
    # results table someone reads three weeks later.
    warn = []
    if tx.vendor == "local":
        warn.append("DER, purity, coverage and timestamp metrics are SELF-REFERENTIAL "
                    "for this vendor — the reference was built from its own output. "
                    "Only WER, entities, turn quality and cost are comparable.")
        der = {**der, "der": None, "self_referential": True}
        ts = {**ts, "median_offset_s": None, "self_referential": True}
    return {"vendor": tx.vendor, "model": tx.model,
            "word": word, "entities": ent, "diarization": der,
            "clusters": health, "naming": nm, "turns": turn,
            "timestamps": ts, "coverage": cov, "review_burden": burden,
            "warnings": warn,
            "practical": {
                "wall_seconds": round(tx.wall_seconds, 1),
                "realtime_factor": round(tx.audio_seconds / tx.wall_seconds, 1)
                                   if tx.wall_seconds else None,
                "cost_usd": (round(price * tx.audio_seconds / 60, 4)
                             if price is not None else None),
                "speaker_per_word": tx.has_word_speakers,
                "word_timestamps": tx.has_word_timestamps,
                "notes": tx.notes + [n for n in tx_c.notes if n not in tx.notes]}}


def table(rows: list[dict]) -> str:
    def g(r, *ks, dp=3):
        v = r
        for k in ks:
            v = (v or {}).get(k)
        if v is None:
            return "  —"
        return f"{v:.{dp}f}" if isinstance(v, float) else str(v)
    hdr = (f"{'vendor':<14}{'WER':>7}{'ent':>7}{'DER':>7}{'purity':>8}{'frag':>7}"
           f"{'weld':>6}{'spk':>6}{'names':>7}{'REVIEW':>9}{'w/spk':>7}")
    out = [hdr, "-" * len(hdr)]
    for r in sorted(rows, key=lambda x: x["review_burden"]["estimated_review_seconds"]):
        out.append(
            f"{r['vendor'][:13]:<14}"
            f"{g(r,'word','wer'):>7}{g(r,'entities','entity_accuracy'):>7}"
            f"{g(r,'diarization','der'):>7}{g(r,'clusters','mean_purity'):>8}"
            f"{g(r,'turns','fragment_rate'):>7}{g(r,'turns','welded_rate'):>6}"
            f"{str(r['clusters']['clusters_found'])+'/'+str(r['clusters']['reference_speakers']):>6}"
            f"{g(r,'naming','accuracy'):>7}"
            f"{r['review_burden']['estimated_review_minutes']:>8.1f}m"
            f"{('yes' if r['practical']['speaker_per_word'] else 'no'):>7}")
    for r in rows:
        for w in r.get("warnings", []):
            out.append(f"  !! {r['vendor']}: {w}")
    out += ["", "WER/DER/frag/weld: lower is better.  ent/purity/names: higher is better.",
            "REVIEW = estimated human minutes to make it trustworthy (the objective).",
            "w/spk  = speaker label on every WORD, i.e. no ASR/diarization join step.",
            "spk    = speakers FOUND / speakers actually present. Under-clustering is the",
            "         most expensive failure: a cluster can be renamed in one edit, but it",
            "         cannot be SPLIT — every absorbed turn must be reassigned by hand."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="diarized-transcript benchmark")
    ap.add_argument("--video", default="lWvRVUMyLP4")
    ap.add_argument("--vendors", default="local",
                    help="comma list, or 'all' for every vendor with a key present")
    ap.add_argument("--clip", action="store_true", help="cut the test clip and exit")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=480.0)
    ap.add_argument("--list", action="store_true", help="show which API keys are present")
    ap.add_argument("--check", action="store_true",
                    help="validate every configured vendor on a 3s clip, then exit")
    ap.add_argument("--score-only", action="store_true",
                    help="re-score cached raw output; makes no API calls")
    a = ap.parse_args()

    if a.list:
        print("API keys (presence only — values are never printed):\n")
        for v, env in A.KEY_ENV.items():
            if env is None:
                print(f"  {v:<14} (no key needed — reads cached S1/S2 output)")
            else:
                print(f"  {v:<14} {'SET  ' if os.environ.get(env) else 'MISSING'} {env}")
        print("\nAdd missing keys to .env — it is gitignored and must never be pasted "
              "into a chat.\nThen: python -m pipeline.bench.run --vendors all")
        return 0

    if a.clip:
        make_clip(a.video, a.start, a.end)
        return 0

    if a.check:
        from pipeline.bench import preflight
        gt = GT.load(a.video)
        clip = clip_path(a.video)
        if not clip.exists():
            print("[bench] no clip — run: python -m pipeline.bench.run --clip")
            return 1
        vendors = ([v for v in A.ADAPTERS] if a.vendors in ("all", "local")
                   else [v.strip() for v in a.vendors.split(",")])
        preflight.run(vendors, preflight.tiny_clip(clip), list(gt["entities"].keys()))
        return 0

    gt = GT.load(a.video)
    clip = clip_path(a.video)
    want = ([v for v in A.ADAPTERS if A.have_key(v)] if a.vendors == "all"
            else [v.strip() for v in a.vendors.split(",")])

    rows = []
    for v in want:
        rp = raw_path(a.video, v)
        if a.score_only or rp.exists():
            if not rp.exists():
                print(f"[bench] {v}: no cached run, skipping")
                continue
            tx = to_transcript(json.loads(rp.read_text()))
            print(f"[bench] {v}: scoring cached run")
        else:
            if not A.have_key(v):
                print(f"[bench] {v}: no {A.KEY_ENV[v]} — skipping")
                continue
            # `local` reads cached S1-S3 output and never touches the audio file.
            if v != "local" and not clip.exists():
                print("[bench] no clip yet — run: python -m pipeline.bench.run --clip")
                return 1
            print(f"[bench] {v}: calling API...")
            try:
                kw = {"video_id": a.video, "window": (a.start, a.end)} if v == "local" else {}
                tx = A.ADAPTERS[v](clip, list(gt["entities"].keys()), **kw)
            except Exception as e:
                print(f"[bench] {v}: FAILED {type(e).__name__}: {str(e)[:200]}")
                continue
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(tx.to_dict(), indent=2))
        rows.append(score(tx, gt))

    if not rows:
        print("[bench] nothing scored"); return 1
    out = REPO / "processing" / a.video / "bench" / "results.json"
    out.write_text(json.dumps(rows, indent=2))
    print("\n" + table(rows) + f"\n\n[bench] full detail: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
