"""Validate every configured vendor against a 3-second clip before the real run.

WHY THIS EXISTS. The real benchmark uploads 8 minutes to six vendors, several of which
are async job APIs that poll for minutes. Discovering a typo'd key, a wrong model name,
or a regional endpoint mismatch at minute nine of that is how people stop running
benchmarks. This makes the same mistakes surface in about ten seconds, for a fraction of
a cent.

It also reports, per vendor, the two facts that decide how to read the results table:
whether the vendor returns a speaker label on every WORD, and whether it returns word
timestamps at all. Those are architectural properties, not quality scores, and knowing
them before the run tells you what the numbers will be able to mean.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from pipeline.bench import adapters as A

REPO = Path(__file__).parent.parent.parent


def explain(e: Exception) -> str:
    """Surface the API's own error message.

    httpx.HTTPStatusError stringifies to "Client error '400 Bad Request' for url ..."
    and a link to MDN — which tells you nothing. The RESPONSE BODY is where every one of
    these APIs says exactly what it rejected ("model not found", "unknown parameter",
    the list of valid values). Discarding it, as the first version of this did, turns a
    ten-second fix into a documentation hunt.
    """
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = json.dumps(resp.json(), separators=(",", ":"))
        except Exception:
            body = (getattr(resp, "text", "") or "")
    body = " ".join(body.split())
    head = f"{type(e).__name__}"
    if resp is not None:
        head += f" {getattr(resp, 'status_code', '')}"
    return f"{head}: {body[:400]}" if body else f"{head}: {' '.join(str(e).split())[:300]}"


def tiny_clip(src: Path, seconds: float = 3.0) -> Path:
    out = src.parent / "preflight_3s.wav"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-ss", "35", "-t", str(seconds), "-ac", "1", "-ar", "16000",
                        str(out)], check=True)
    return out


def check(vendor: str, clip: Path, keyterms: list[str]) -> dict:
    if not A.have_key(vendor):
        return {"vendor": vendor, "status": "no key",
                "detail": A.KEY_ENV.get(vendor) or ""}
    fn = A.ADAPTERS.get(vendor)
    if fn is None:
        return {"vendor": vendor, "status": "NO ADAPTER",
                "detail": "key is set but nothing implements this vendor"}
    t0 = time.time()
    try:
        kw = {"video_id": "lWvRVUMyLP4", "window": (35.0, 38.0)} if vendor == "local" else {}
        tx = fn(clip, keyterms, **kw)
    except Exception as e:
        return {"vendor": vendor, "status": "FAILED",
                "model": A.model_for(vendor),
                "detail": explain(e)}
    return {"vendor": vendor, "status": "ok",
            "model": tx.model,
            "seconds": round(time.time() - t0, 1),
            "turns": len(tx.turns),
            "speaker_per_word": tx.has_word_speakers,
            "word_timestamps": tx.has_word_timestamps,
            "names_speakers": tx.names_speakers,
            "sample": (tx.turns[0].text[:60] if tx.turns else "(no turns returned)")}


def run(vendors: list[str], clip: Path, keyterms: list[str]) -> list[dict]:
    rows = [check(v, clip, keyterms) for v in vendors]
    w = max(len(r["vendor"]) for r in rows) + 2
    print(f"\n{'vendor':<{w}}{'status':<10}{'model':<26}{'w/spk':<7}{'w/ts':<6}"
          f"{'names':<7}{'sec':>5}")
    print("-" * (w + 61))
    for r in rows:
        if r["status"] == "ok":
            print(f"{r['vendor']:<{w}}{'ok':<10}{r['model'][:25]:<26}"
                  f"{('yes' if r['speaker_per_word'] else 'no'):<7}"
                  f"{('yes' if r['word_timestamps'] else 'no'):<6}"
                  f"{('yes' if r['names_speakers'] else 'no'):<7}{r['seconds']:>5}")
        else:
            print(f"{r['vendor']:<{w}}{r['status']:<10}{r.get('detail','')[:70]}")
    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\n{len(ok)}/{len(rows)} ready.")
    for r in ok:
        print(f"  {r['vendor']:<14} first turn: {r['sample']!r}")
    bad = [r for r in rows if r["status"] == "FAILED"]
    if bad:
        print("\nFailures — fix before the real run:")
        for r in bad:
            print(f"  {r['vendor']}:\n     {r['detail']}")
            print(f"     model in force: {r.get('model')!r} "
                  f"(override with {A.MODEL_ENV.get(r['vendor'], 'n/a')} in .env)")
    return rows
