"""Cross-vendor disagreement analysis on a City Council meeting.

    python -m pipeline.bench.council_run --video zjOmif3nU7Y --event 253

WHY A COUNCIL MEETING. Everything measured so far is n=1 on a 17-member advisory body
with clean audio, a narrating chair, and a tidy agenda. Council is the case the corpus
actually consists of: ~150 meetings, 2-3 hours each, crosstalk, roll-call theatre, and
public comment from people who are not on any roster. If a vendor only works on the easy
meeting it is the wrong vendor.

WHY DISAGREEMENT INSTEAD OF SCORING. Nobody has hand-labelled this meeting and doing so
would cost hours. But three INDEPENDENT systems on the same audio give a reference-free
signal: where they agree they are probably right, and where one stands alone it is
probably wrong. See pipeline/bench/disagree.py for what that can and cannot claim.

SEGMENT SELECTION IS BY TEXT SEARCH, NOT BY DIARIZATION. A locator pass finds the three
windows by looking for known strings — council surnames for roll call, the registered
commenters' names from Legistar for public comment. That matters because diarization
quality is the thing under test, so using speaker-change density to choose the segments
would let a bad diarizer pick its own exam.

THE THREE WINDOWS, chosen for what each stresses:
  ROLL CALL       ~12 people in ~2 minutes, every utterance under 2 seconds. The case
                  that broke our own pipeline: 38% of segments below the embedding floor.
  PUBLIC COMMENT  Legistar lists all 11 registered speakers BY NAME, so the true speaker
                  count is known without listening to anything. Long clean single-speaker
                  turns with hard transitions — the opposite failure mode to roll call.
  DELIBERATION    Council debating after the consent agenda: interruption, crosstalk,
                  the chair cutting in. Where welding happens.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from pipeline.bench import adapters as A
from pipeline.bench import disagree as D
from pipeline.bench.run import to_transcript
import pipeline.config as _cfg

_cfg.load_dotenv()
REPO = Path(__file__).parent.parent.parent
VENDORS = ["deepgram", "assemblyai", "elevenlabs"]
WINDOW = 480.0

COMMENTERS = ["Jaskiewicz", "Griswold", "Herskovitz", "Lutz", "Harris", "Winters",
              "Bersee", "Coleman", "Brown", "Watson", "Lande"]
# Ann Arbor council: 10 members + mayor. Roll call therefore has ~11 voices plus the
# clerk reading the roster.
ROLL_CALL_SPEAKERS = 12


def out_dir(video: str) -> Path:
    d = REPO / "processing" / video / "bench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_audio(video: str) -> Path:
    src = REPO / "processing" / video / "audio.m4a"
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["yt-dlp", "-f", "bestaudio", "-o", str(src),
                        f"https://www.youtube.com/watch?v={video}"], check=True)
    return src


def cut(src: Path, start: float, end: float, dest: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ss", str(start),
                    "-to", str(end), "-ac", "1", "-ar", "16000", str(dest)], check=True)
    return dest


def locate(video: str, src: Path, keyterms: list[str]) -> dict:
    """One cheap full-meeting pass, used ONLY to find the three windows by text."""
    cache = out_dir(video) / "locator.json"
    if cache.exists():
        d = json.loads(cache.read_text())
    else:
        # 64kbps mono mp3, NOT wav. A 2h35m meeting as 16kHz mono wav is ~300 MB, which
        # is a slow upload and past several vendors' request ceilings; the same audio as
        # mp3 is ~75 MB. Quality is irrelevant here — this pass only has to produce text
        # with timestamps good enough to locate three windows by string search.
        full = out_dir(video) / "full_64k.mp3"
        if not full.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                            "-ac", "1", "-ar", "16000", "-b:a", "64k", str(full)],
                           check=True)
            print(f"[council] locator audio {full.stat().st_size/1e6:.0f} MB")
        print("[council] locator pass (deepgram, full meeting)...")
        tx = A.run_deepgram(full, keyterms)
        d = tx.to_dict()
        cache.write_text(json.dumps(d))
    tx = to_transcript(d)
    words = [(w.text.lower().strip(".,?!"), w.start) for w in tx.words()
             if w.start is not None]
    text_at = lambda t: " ".join(x for x, s in words if t <= s < t + WINDOW)

    def first_time(patterns, after=0.0, need=1):
        """Earliest second at which `need` of the patterns appear inside one window."""
        for t in range(int(after), int(words[-1][1]) - int(WINDOW), 30):
            blob = text_at(t)
            if sum(1 for p in patterns if p.lower() in blob) >= need:
                return float(t)
        return None

    roll = first_time(["roll call", "here", "present"], after=0, need=2) or 0.0
    pub = first_time(COMMENTERS, after=roll + WINDOW, need=2)
    # Deliberation: the densest stretch of speaker alternation AFTER public comment.
    delib = None
    if pub:
        best, best_n = None, -1
        for t in range(int(pub + WINDOW), int(words[-1][1] - WINDOW), 60):
            n = sum(1 for a, b in zip(tx.turns, tx.turns[1:])
                    if t <= a.start < t + WINDOW and a.speaker != b.speaker)
            if n > best_n:
                best, best_n = float(t), n
        delib = best
    return {"roll_call": roll, "public_comment": pub, "deliberation": delib,
            "audio_seconds": words[-1][1] if words else 0}


def run_segment(video: str, src: Path, name: str, start: float,
                keyterms: list[str], expected: int | None) -> dict:
    clip = cut(src, start, start + WINDOW, out_dir(video) / f"seg_{name}.wav")
    txs = {}
    for v in VENDORS:
        raw = out_dir(video) / "raw" / f"{name}_{v}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        if raw.exists():
            txs[v] = to_transcript(json.loads(raw.read_text()))
            continue
        try:
            kw = {}
            # Pass the speaker count where Legistar tells us what it is. Measured on the
            # Sustainability window: AssemblyAI returned 3 clusters for 14 people without
            # this hint and exactly 14 with it. That was a misconfiguration on our side,
            # not a vendor limit, and it nearly disqualified the vendor.
            if v == "assemblyai" and expected:
                kw["speakers_expected"] = expected
            print(f"[council] {name}: {v}...")
            tx = A.ADAPTERS[v](clip, keyterms, **kw)
            raw.write_text(json.dumps(tx.to_dict(), indent=2))
            txs[v] = tx
        except Exception as e:
            print(f"[council] {name}: {v} FAILED {type(e).__name__}: {str(e)[:200]}")
    if len(txs) < 2:
        return {"segment": name, "error": "fewer than 2 systems returned output"}
    a = D.analyze(txs, expected_speakers=expected,
                  label=f"{name}  {int(start)//60}:{int(start)%60:02d}"
                        f"-{int(start+WINDOW)//60}:{int(start+WINDOW)%60:02d}")
    a["start_s"] = start
    return a


def main() -> int:
    ap = argparse.ArgumentParser(description="Council cross-vendor disagreement run")
    ap.add_argument("--video", default="zjOmif3nU7Y")
    ap.add_argument("--event", type=int, default=253)
    a = ap.parse_args()

    gt = json.loads((REPO / "processing" / "lWvRVUMyLP4" / "bench" /
                     "ground_truth.json").read_text())
    keyterms = [k for k in gt["entities"]]

    print(f"[council] {a.video}  vendors={','.join(VENDORS)}")
    src = fetch_audio(a.video)
    loc = locate(a.video, src, keyterms)
    print(f"[council] located: {json.dumps({k: (round(v) if isinstance(v,(int,float)) and v else v) for k,v in loc.items()})}")

    plan = [("roll_call", loc["roll_call"], ROLL_CALL_SPEAKERS),
            ("public_comment", loc["public_comment"], len(COMMENTERS) + 1),
            ("deliberation", loc["deliberation"], None)]
    results = []
    for name, start, expected in plan:
        if start is None:
            print(f"[council] {name}: could not locate — skipped")
            continue
        results.append(run_segment(a.video, src, name, start, keyterms, expected))

    dest = out_dir(a.video) / "disagreement.json"
    dest.write_text(json.dumps({"video": a.video, "located": loc,
                                "segments": results}, indent=2))
    print("\n" + "=" * 72)
    for r in results:
        if "error" in r:
            print(f"── {r['segment']}: {r['error']}")
            continue
        print(D.report(r) + "\n")
    print(f"[council] full detail: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
