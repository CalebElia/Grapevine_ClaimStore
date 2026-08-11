"""S3 — turns derived from word-level speaker labels, with suspicious boundaries flagged.

HOW THIS DIFFERS FROM THE OLD S3, WHICH IT REPLACES.

The old S3 had a hard job: reconcile WhisperX word timings against pyannote speaker spans
that disagreed with them, assign each word to a speaker by midpoint, then rescue short
cross-talk into an `interjections` sidecar. Every one of its defects came from that
reconciliation — 141 straddling words, 67% useless interjections, a junk cluster.

With Scribe every word ARRIVES with its speaker. So a turn is not detected, it is simply
read off: a maximal run of consecutive words sharing a speaker. There is no join, no
midpoint rule, and no interjection sidecar — an interjection is just a short run, with
exactly the same structure as any other turn.

WHAT REMAINS, AND WHY WE FLAG RATHER THAN FIX. One risk replaces the old ones: Scribe can
be wrong about a word's speaker, and with a single system there is no second opinion to
disagree with it. Cross-vendor testing measured turn-boundary agreement between
independent systems at 0.056-0.652 even where word agreement exceeded 0.90 — they hear
the same words and disagree about who said them. No vendor solves this.

So this stage does NOT try to correct boundaries. It marks the ones most likely to be
wrong so a reviewer's attention goes where it is needed:

  no_pause        speaker changes with < 150 ms of silence. People rarely swap mid-breath.
  mid_sentence    the boundary falls inside a clause, with no terminal punctuation before
                  it — more likely a flicker than a real interruption.
  short_run       1-2 words sandwiched between two runs of the SAME other speaker. The
                  classic flicker. Flagged, never deleted: "Here." in roll call is a
                  legitimate one-word turn and deleting it would lose a roll-call answer,
                  which is our free speaker-enrollment signal.

Usage:
    python -m pipeline.turns_v2 lWvRVUMyLP4
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).parent.parent

GAP_NEW_TURN = 2.0        # same speaker, longer pause than this -> new turn (paragraph)
NO_PAUSE = 0.15           # speaker change tighter than this is suspicious
SHORT_RUN_WORDS = 2       # runs this short between same-speaker neighbours are flickers
TERMINAL = ".?!"


def make_utterance_key(media_asset_id: str, sequence: int,
                       start_ms: int, end_ms: int) -> str:
    """Unchanged from v1. Identity must not depend on a rounded float — the original
    10-second bucketing collided 1,156 turns into 631 ids and silently gave 45% of them
    another turn's extraction."""
    return f"{media_asset_id}:{sequence:05d}:{start_ms}-{end_ms}"


def build_turns(words: list[dict], media_asset_id: str) -> list[dict]:
    runs: list[dict] = []
    for w in words:
        if not (w.get("text") or "").strip():
            continue
        spk = w.get("speaker") or "unknown"
        gap = (w.get("start") or 0) - (runs[-1]["end"] if runs else 0)
        if runs and runs[-1]["speaker"] == spk and gap <= GAP_NEW_TURN:
            runs[-1]["words"].append(w)
            runs[-1]["end"] = w.get("end") or runs[-1]["end"]
        else:
            runs.append({"speaker": spk, "start": w.get("start") or 0.0,
                         "end": w.get("end") or 0.0, "words": [w]})

    turns = []
    for i, r in enumerate(runs):
        text = _join(t["text"] for t in r["words"])
        flags = []
        prev, nxt = (runs[i - 1] if i else None), (runs[i + 1] if i + 1 < len(runs) else None)

        if prev is not None and prev["speaker"] != r["speaker"]:
            if r["start"] - prev["end"] < NO_PAUSE:
                flags.append("no_pause")
            ptxt = _join(t["text"] for t in prev["words"]).rstrip()
            if ptxt and ptxt[-1] not in TERMINAL:
                flags.append("mid_sentence")
        if (len(r["words"]) <= SHORT_RUN_WORDS and prev is not None and nxt is not None
                and prev["speaker"] == nxt["speaker"] != r["speaker"]):
            flags.append("short_run")

        turns.append({
            "seq": len(turns),
            "speaker": r["speaker"],
            "start": round(r["start"], 3),
            "end": round(r["end"], 3),
            "text": text,
            "key": make_utterance_key(media_asset_id, len(turns),
                                      int(r["start"] * 1000), int(r["end"] * 1000)),
            "word_count": len(r["words"]),
            "boundary_flags": flags,
            # kept so a corrected turn can be re-split later without re-running ASR
            "words": [{"t": t["text"], "s": t.get("start"), "e": t.get("end")}
                      for t in r["words"]],
        })
    return turns


def _join(tokens) -> str:
    """Join word tokens across BOTH tokenizer conventions. Plain Whisper embeds a leading
    space (' Thank'); WhisperX and Scribe strip it ('Thank'). Getting this wrong welds
    text together — it produced 'Thankyou.ChairCurtis?' and was only caught because the
    total text ratio came out at 0.83x instead of ~1.0x."""
    out = []
    for tok in tokens:
        if not tok:
            continue
        if out and not tok[:1].isspace() and not out[-1][-1:].isspace():
            out.append(" ")
        out.append(tok)
    return "".join(out).strip()


def run(video_id: str, out_dir: Path | None = None,
        scribe: str = "scribe.json") -> dict:
    d = out_dir or REPO / "processing" / video_id
    p = d / scribe
    if not p.exists():                      # fall back to the newest windowed run
        cands = sorted(d.glob("scribe*.json"), key=lambda x: x.stat().st_mtime)
        if not cands:
            raise FileNotFoundError(f"no scribe*.json in {d}")
        p = cands[-1]
        print(f"[S3v2] using {p.name}")
    src = json.loads(p.read_text())
    turns = build_turns(src["words"], f"vid_yt_{video_id}")

    flagged = [t for t in turns if t["boundary_flags"]]
    from collections import Counter
    c = Counter(f for t in turns for f in t["boundary_flags"])
    dur = max(t["end"] for t in turns) if turns else 0
    print(f"[S3v2] {len(turns)} turns from {len(src['words'])} words, "
          f"{len(src['speakers'])} clusters, {dur/60:.0f} min")
    print(f"[S3v2] {len(flagged)} turns carry a boundary flag "
          f"({len(flagged)/len(turns)*100:.0f}%): {dict(c)}")

    payload = {"video_id": video_id, "turns": turns,
               "_provenance": {**src.get("_provenance", {}),
                               "turn_rule": f"maximal same-speaker run, split at "
                                            f"{GAP_NEW_TURN}s gap",
                               "boundary_flags": dict(c),
                               "no_interjection_sidecar":
                                   "not needed — speaker-per-word means an interjection "
                                   "is just a short run"}}
    out = d / p.name.replace("scribe", "turns_v2")
    out.write_text(json.dumps(payload))
    print(f"[S3v2] wrote {out}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="S3 turns from word-level speakers")
    ap.add_argument("video_id")
    ap.add_argument("--scribe", default="scribe.json")
    a = ap.parse_args()
    run(a.video_id, scribe=a.scribe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
