"""Vendor adapters. Each maps one API onto BenchTranscript. Nothing else changes.

ADDING A VENDOR = one function + one entry in ADAPTERS. Everything downstream —
metrics, scoring, the results table — is vendor-agnostic and needs no edit.

API KEYS: every adapter reads exactly one environment variable, named in KEY_ENV below.
Put them in .env (gitignored, never pasted into a chat). `python -m pipeline.bench.run
--list` prints which are present WITHOUT printing any value.

CUSTOM VOCABULARY: passed uniformly as `keyterms` — a plain list of terms. Note this is
structurally safer than Whisper's `initial_prompt`, which is prepended as if it were
prior transcript and can therefore be CONTINUED by the decoder. That is what produced
the leak that destroyed 62 words of Missy Stults at 23:19. A keyterm list is not
continuable prose and cannot fail that way.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from pipeline.bench.types import BenchTranscript, Turn, Word

REPO = Path(__file__).parent.parent.parent

# ── PUT YOUR KEYS IN .env UNDER THESE NAMES ──────────────────────────────────
KEY_ENV = {
    "deepgram":    "DEEPGRAM_API_KEY",
    "assemblyai":  "ASSEMBLYAI_API_KEY",
    "speechmatics": "SPEECHMATICS_API_KEY",
    "gemini":      "GOOGLE_API_KEY",
    "openai":      "OPENAI_API_KEY",       # already set — Azure AI Foundry
    "elevenlabs":  "ELEVENLABS_API_KEY",
    "revai":       "REVAI_ACCESS_TOKEN",
    "local":       None,                    # no key; reads cached S1/S2 output
}

# Published list prices per minute of audio, for the cost column. VERIFY THESE — they
# change, and this file will go stale. Set to None to leave cost unscored.
PRICE_PER_MIN = {
    "deepgram": None, "assemblyai": None, "speechmatics": None,
    "gemini": None, "openai": None, "elevenlabs": None, "revai": None, "local": 0.0,
}

# MODEL NAMES ARE ENV-OVERRIDABLE, and this matters more than it looks.
#
# Every default below was written from knowledge that is already months stale, and these
# vendors ship new flagship models faster than any hardcoded string survives. A wrong
# model name is not a subtle degradation — it is either a 400, or worse, a silent
# fallback to an older/cheaper model that then loses the bake-off for a reason that has
# nothing to do with the vendor.
#
# So: check each dashboard for the CURRENT best model, put it in .env, and the default
# below is only what runs when you have not. `--check` prints which is in force.
MODEL_ENV = {
    "deepgram":     "DEEPGRAM_MODEL",
    "assemblyai":   "ASSEMBLYAI_MODEL",
    "speechmatics": "SPEECHMATICS_MODEL",
    "gemini":       "GEMINI_MODEL",
    "elevenlabs":   "ELEVENLABS_MODEL",
    "revai":        "REVAI_MODEL",
    "openai":       "GRAPEVINE_DEPLOYMENT_TRANSCRIBE",
}
DEFAULT_MODEL = {
    "deepgram":     "nova-3",        # keyterm prompting needs nova-3 or newer
    "assemblyai":   "universal",
    "speechmatics": "enhanced",      # operating_point, not a model name
    "gemini":       "gemini-2.5-flash",
    "elevenlabs":   "scribe_v1",
    "revai":        "machine",
    "openai":       "gpt-4o-transcribe-diarize",
}

# REGION MATTERS for two of these — an EU-resident account cannot call the global host
# and will return 401 with a message that does not mention regions. Set only if your
# account is not on the default host.
ENDPOINT_ENV = {
    "assemblyai":   ("ASSEMBLYAI_BASE_URL", "https://api.assemblyai.com"),
    "speechmatics": ("SPEECHMATICS_BASE_URL", "https://asr.api.speechmatics.com"),
}


# DASHBOARD DISPLAY NAMES ARE NOT API IDENTIFIERS, and this cost us three of five
# preflight failures on the first real run. A user reads "Nova-3" or "Universal-3.5 Pro"
# off a pricing page — which is exactly what the docs tell them to do — and the API
# rejects it, sometimes with a message that misdiagnoses the problem: Deepgram replied
# "`keyterm` is only supported for Nova-3" while we were literally asking for 'Nova-3',
# because its matcher is case-sensitive and silently treated it as an older model.
#
# So normalise on the way in, per each vendor's own convention, and keep an explicit
# alias table for display names we have actually seen people paste.
MODEL_ALIASES = {
    "assemblyai": {"universal-3.5-pro": "universal-3-5-pro",
                   "universal 3.5 pro": "universal-3-5-pro",
                   "universal-3.5": "universal-3-5-pro",
                   "universal": "universal-2"},
    "revai": {"speech-to-text-v1": "machine", "speech-to-text": "machine",
              "standard": "machine", "reverb": "fusion"},
}
# Lowercase-and-hyphenate is right for these; left alone for anyone whose ids are
# case-sensitive in the other direction.
SLUGGED = {"deepgram", "assemblyai", "revai", "speechmatics", "gemini"}


def normalize_model(vendor: str, raw: str) -> str:
    if not raw:
        return raw
    s = raw.strip()
    if vendor in SLUGGED:
        s = re.sub(r"[\s_]+", "-", s.lower())
        s = MODEL_ALIASES.get(vendor, {}).get(s, s)
    return s


def model_for(vendor: str, override: str | None = None) -> str:
    env = MODEL_ENV.get(vendor)
    raw = override or (os.environ.get(env) if env else None) or \
        DEFAULT_MODEL.get(vendor, "")
    return normalize_model(vendor, raw)


def base_url(vendor: str) -> str:
    env, default = ENDPOINT_ENV.get(vendor, (None, ""))
    return (os.environ.get(env) if env else None) or default


def have_key(vendor: str) -> bool:
    env = KEY_ENV.get(vendor)
    return env is None or bool(os.environ.get(env))


def _audio_seconds(path: Path) -> float:
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


# ───────────────────────────── Deepgram ─────────────────────────────

def run_deepgram(audio: Path, keyterms: list[str],
                 model: str | None = None) -> BenchTranscript:
    """Speaker-per-WORD. The architecture we are testing for.

    Deepgram returns `speaker` on every word, so there is no ASR/diarization join and
    none of the midpoint-assignment errors that follow from one.
    """
    import httpx
    key = os.environ["DEEPGRAM_API_KEY"]
    model = model_for("deepgram", model)
    params = {"model": model, "diarize": "true", "punctuate": "true",
              "smart_format": "true", "utterances": "true"}
    # keyterm prompting is nova-3+; older models use `keywords`
    q = "&".join([f"{k}={v}" for k, v in params.items()] +
                 [f"keyterm={t}" for t in keyterms])
    t0 = time.time()
    with httpx.Client(timeout=1200) as c:
        # Content-type must match the file. The locator pass sends mp3 (a 2h35m wav is
        # ~300 MB); mislabelling it as wav makes Deepgram reject or misparse the audio.
        ctype = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
                 ".flac": "audio/flac"}.get(audio.suffix.lower(), "audio/wav")
        r = c.post(f"https://api.deepgram.com/v1/listen?{q}",
                   headers={"Authorization": f"Token {key}", "Content-Type": ctype},
                   content=audio.read_bytes())
    r.raise_for_status()
    d = r.json()
    wall = time.time() - t0

    turns = []
    for u in d["results"].get("utterances", []):
        ws = [Word(w["punctuated_word"] if "punctuated_word" in w else w["word"],
                   w["start"], w["end"], f"SPEAKER_{w.get('speaker', '?'):>02}")
              for w in u.get("words", [])]
        turns.append(Turn(f"SPEAKER_{u['speaker']:02d}", u["start"], u["end"],
                          u["transcript"], ws))
    return BenchTranscript("deepgram", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=True, has_word_speakers=True,
                           names_speakers=False, raw=d)


# ───────────────────────────── AssemblyAI ─────────────────────────────

def run_assemblyai(audio: Path, keyterms: list[str],
                   model: str | None = None,
                   speakers_expected: int | None = None) -> BenchTranscript:
    """Speaker-per-word via utterances. Upload, poll, collect.

    `speakers_expected` matters more than it looks: on the 8-minute Sustainability
    window this returned 3 speakers where 14 people spoke. Under-clustering is the most
    expensive failure mode we measure, so the hint is worth passing whenever the count
    is known — and for a Legistar public-comment block it IS known, because every
    registered speaker is listed on the agenda.
    """
    import httpx
    key = os.environ["ASSEMBLYAI_API_KEY"]
    model = model_for("assemblyai", model)
    api = base_url("assemblyai")
    h = {"authorization": key}
    t0 = time.time()
    with httpx.Client(timeout=1200) as c:
        up = c.post(f"{api}/v2/upload", headers=h,
                    content=audio.read_bytes())
        up.raise_for_status()
        # `speech_model` (singular) is deprecated; the API returns 400 telling you to
        # use `speech_models` as a LIST. Reported verbatim by preflight, which is the
        # whole reason the error body is surfaced.
        # Custom vocabulary moved: `word_boost` is rejected outright by universal-3+
        # ("not compatible with universal-3-5-pro. Use prompt or keyterms_prompt"), while
        # older models do not know `keyterms_prompt`. Pick by model family.
        body = {"audio_url": up.json()["upload_url"], "speaker_labels": True,
                "speech_models": [model]}
        if speakers_expected:
            body["speakers_expected"] = speakers_expected
        if model.startswith("universal-3") or model.startswith("slam"):
            body["keyterms_prompt"] = keyterms
        else:
            body["word_boost"] = keyterms
            body["boost_param"] = "high"
        job = c.post(f"{api}/v2/transcript", headers=h, json=body)
        job.raise_for_status()
        tid = job.json()["id"]
        while True:
            s = c.get(f"{api}/v2/transcript/{tid}", headers=h).json()
            if s["status"] in ("completed", "error"):
                break
            time.sleep(3)
    wall = time.time() - t0
    if s["status"] == "error":
        raise RuntimeError(f"assemblyai: {s.get('error')}")
    turns = []
    for u in s.get("utterances") or []:
        ws = [Word(w["text"], w["start"] / 1000, w["end"] / 1000, u["speaker"])
              for w in u.get("words", [])]
        turns.append(Turn(f"SPEAKER_{u['speaker']}", u["start"] / 1000,
                          u["end"] / 1000, u["text"], ws))
    return BenchTranscript("assemblyai", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=True, has_word_speakers=True,
                           names_speakers=False, raw=s)


# ───────────────────────────── Speechmatics ─────────────────────────────

def run_speechmatics(audio: Path, keyterms: list[str],
                     model: str | None = None) -> BenchTranscript:
    """Historically the strongest pure diarization; word-level speaker labels."""
    import httpx
    key = os.environ["SPEECHMATICS_API_KEY"]
    model = model_for("speechmatics", model)
    api = base_url("speechmatics")
    cfg = {"type": "transcription",
           "transcription_config": {
               "language": "en", "operating_point": model,
               "diarization": "speaker",
               "additional_vocab": [{"content": t} for t in keyterms]}}
    t0 = time.time()
    with httpx.Client(timeout=1800) as c:
        j = c.post(f"{api}/v2/jobs",
                   headers={"Authorization": f"Bearer {key}"},
                   files={"data_file": (audio.name, audio.read_bytes())},
                   data={"config": json.dumps(cfg)})
        j.raise_for_status()
        jid = j.json()["id"]
        while True:
            st = c.get(f"{api}/v2/jobs/{jid}",
                       headers={"Authorization": f"Bearer {key}"}).json()
            if st["job"]["status"] != "running":
                break
            time.sleep(5)
        d = c.get(f"{api}/v2/jobs/{jid}/transcript?format=json-v2",
                  headers={"Authorization": f"Bearer {key}"}).json()
    wall = time.time() - t0
    turns, cur = [], None
    for r in d.get("results", []):
        alt = (r.get("alternatives") or [{}])[0]
        spk = alt.get("speaker", "UU")
        w = Word(alt.get("content", ""), r.get("start_time"), r.get("end_time"), spk)
        if cur is None or cur.speaker != spk:
            cur = Turn(spk, r["start_time"], r["end_time"], "", [])
            turns.append(cur)
        cur.words.append(w)
        cur.end = r["end_time"]
        cur.text = (cur.text + " " + w.text).strip()
    return BenchTranscript("speechmatics", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=True, has_word_speakers=True,
                           names_speakers=False, raw=d)


# ───────────────────────────── OpenAI / Azure AI Foundry ─────────────────────────────

def run_openai(audio: Path, keyterms: list[str],
               model: str | None = None) -> BenchTranscript:
    """Azure AI Foundry. NEEDS A DEPLOYMENT, not just catalog visibility.

    As of the last check the endpoint listed 370 catalog models but had zero callable
    deployments — every request returned DeploymentNotFound, including gpt-5.6-luna.
    Create a deployment in Foundry first. `gpt-transcribe-2026-07-28` is the newest
    transcription model in the catalog; `gpt-4o-transcribe-diarize` is nine months older
    but is the one that explicitly diarizes. Try the newest first and fall back.
    """
    from openai import OpenAI
    from pipeline.config import require
    model = model_for("openai", model)
    c = OpenAI(base_url=require("OPENAI_BASE_URL"), api_key=require("OPENAI_API_KEY"))
    t0 = time.time()
    kw = {"model": model, "file": audio.open("rb")}
    try:
        r = c.audio.transcriptions.create(**kw, response_format="diarized_json",
                                          chunking_strategy="auto")
        diarized = True
    except Exception:
        r = c.audio.transcriptions.create(**kw, response_format="verbose_json",
                                          prompt=", ".join(keyterms))
        diarized = False
    wall = time.time() - t0
    d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
    turns = []
    for s in d.get("segments", []):
        spk = s.get("speaker") or "SPEAKER_00"
        turns.append(Turn(str(spk), s.get("start", 0.0), s.get("end", 0.0),
                          s.get("text", ""), []))
    return BenchTranscript("openai", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=False, has_word_speakers=False,
                           names_speakers=False, raw=d,
                           notes=[] if diarized else ["no diarization — fell back"])


# ───────────────────────────── Gemini ─────────────────────────────

GEMINI_PROMPT = """Transcribe this audio with speaker diarization.
Rules:
- Identify each speaker by name when it can be determined from introductions, roll call,
  or how others address them. Otherwise use "Speaker 1", "Speaker 2".
- Output ONLY lines of the form:
  [MM:SS] Speaker Name: their words
- One line per speaker turn. Preserve every word verbatim. Do not summarize.
- These proper nouns appear and must be spelled exactly: {terms}
"""


def run_gemini(audio: Path, keyterms: list[str],
               model: str | None = None) -> BenchTranscript:
    """The colleague's approach, with timestamps demanded.

    Their original prompt produced excellent turns and names but ZERO timestamps, which
    is disqualifying for a claim store. Asking for [MM:SS] costs nothing; whether the
    model honours it accurately is exactly what this benchmark is for.
    """
    import re
    from google import genai
    model = model_for("gemini", model)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    t0 = time.time()
    up = client.files.upload(file=str(audio))
    resp = client.models.generate_content(
        model=model, contents=[up, GEMINI_PROMPT.format(terms=", ".join(keyterms))])
    wall = time.time() - t0
    try:
        client.files.delete(name=up.name)
    except Exception:
        pass
    turns = []
    for line in (resp.text or "").splitlines():
        m = re.match(r"\s*\[(\d+):(\d\d)\]\s*([^:]{1,60}?):\s*(.+)", line)
        if not m:
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        name = m.group(3).strip()
        turns.append(Turn(name, float(start), float(start), m.group(4).strip(), [],
                          speaker_name=None if re.match(r"Speaker \d+$", name) else name))
    for a, b in zip(turns, turns[1:]):
        a.end = b.start
    if turns:
        turns[-1].end = _audio_seconds(audio)
    return BenchTranscript("gemini", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=False, has_word_speakers=False,
                           names_speakers=True, raw=resp.text)


# ───────────────────────────── the incumbent ─────────────────────────────

def run_local(audio: Path, keyterms: list[str], model: str = "whisperx+pyannote",
              video_id: str = "lWvRVUMyLP4", window: tuple[float, float] | None = None
              ) -> BenchTranscript:
    """Our own S1+S2+S3 output, read from cache. The control.

    Reads turns.json rather than re-running, so the benchmark costs nothing to include
    the incumbent. NOTE the unfair advantage on timestamp accuracy: the reference
    alignment IS this pipeline's output.
    """
    d = REPO / "processing" / video_id
    turns_all = json.loads((d / "turns.json").read_text())["turns"]
    lo, hi = window or (0.0, 1e9)
    turns = []
    for t in turns_all:
        if t["end"] < lo or t["start"] > hi:
            continue
        turns.append(Turn(t["speaker"], t["start"], t["end"], t["text"], []))
        for j in t["interjections"]:
            if lo <= j["start"] <= hi:
                turns.append(Turn(j["speaker"], j["start"], j["end"], j["text"], []))
    turns.sort(key=lambda x: x.start)
    return BenchTranscript("local", model, turns,
                           (hi - lo) if window else _audio_seconds(audio), 0.0,
                           has_word_timestamps=True, has_word_speakers=False,
                           names_speakers=False, cost_usd=0.0,
                           notes=["cached S1+S2+S3; timestamp metric is self-referential"])


# ───────────────────────────── ElevenLabs Scribe ─────────────────────────────

def run_elevenlabs(audio: Path, keyterms: list[str],
                   model: str | None = None) -> BenchTranscript:
    """Scribe. Returns a flat word list with `speaker_id` on each word.

    Speaker-per-word, so it belongs in the same architectural class as Deepgram and
    AssemblyAI: no ASR/diarization join, therefore none of the midpoint-assignment
    errors that follow from one. Turns are reconstructed here by grouping consecutive
    words that share a speaker.
    """
    import httpx
    model = model_for("elevenlabs", model)
    t0 = time.time()
    with httpx.Client(timeout=1800) as c:
        r = c.post("https://api.elevenlabs.io/v1/speech-to-text",
                   headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                   files={"file": (audio.name, audio.read_bytes(), "audio/wav")},
                   data={"model_id": model, "diarize": "true",
                         "timestamps_granularity": "word"})
    r.raise_for_status()
    d = r.json()
    wall = time.time() - t0

    turns, cur = [], None
    for w in d.get("words", []):
        if w.get("type") not in (None, "word"):        # skip 'spacing'/'audio_event'
            continue
        spk = str(w.get("speaker_id") or "SPEAKER_00")
        wd = Word(w.get("text", ""), w.get("start"), w.get("end"), spk)
        if cur is None or cur.speaker != spk:
            cur = Turn(spk, wd.start or 0.0, wd.end or 0.0, "", [])
            turns.append(cur)
        cur.words.append(wd)
        cur.end = wd.end or cur.end
        cur.text = (cur.text + " " + wd.text).strip()
    return BenchTranscript("elevenlabs", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=True, has_word_speakers=True,
                           names_speakers=False, raw=d)


# ───────────────────────────── Rev.ai ─────────────────────────────

def run_revai(audio: Path, keyterms: list[str],
              model: str | None = None) -> BenchTranscript:
    """Rev.ai async job. Returns monologues, one per speaker turn.

    Segment-level speakers, not word-level — so it carries the same architecture as our
    incumbent and should be read that way in the results table. Included because Rev's
    diarization has a long track record and it is a useful third opinion.
    """
    import httpx
    model = model_for("revai", model)
    h = {"Authorization": f"Bearer {os.environ['REVAI_ACCESS_TOKEN']}"}
    # A REAL VENDOR LIMITATION, not a config error: Rev.ai rejects any custom-vocabulary
    # phrase containing a digit ("only alpha and punctuation characters are allowed...
    # numbers are not allowed"). That excludes 'A2Zero' — the single most load-bearing
    # entity name in this corpus, and the exact term whose misrendering as 'A20' started
    # this whole line of work. Rev.ai therefore CANNOT be given the one hint that matters
    # most. Dropped terms are recorded and surface in the results.
    usable = [t for t in keyterms if not any(ch.isdigit() for ch in t)]
    dropped = [t for t in keyterms if t not in usable]
    opts = {"transcriber": model, "skip_diarization": False}
    if usable:
        opts["custom_vocabularies"] = [{"phrases": usable}]
    t0 = time.time()
    with httpx.Client(timeout=1800) as c:
        # `options` has to be its own multipart PART with content-type application/json.
        # Sent as an ordinary form field it comes back as "options: Invalid JSON format",
        # which reads like malformed JSON but is actually a missing content-type.
        j = c.post("https://api.rev.ai/speechtotext/v1/jobs", headers=h,
                   files={"media": (audio.name, audio.read_bytes(), "audio/wav"),
                          "options": (None, json.dumps(opts), "application/json")})
        j.raise_for_status()
        jid = j.json()["id"]
        while True:
            st = c.get(f"https://api.rev.ai/speechtotext/v1/jobs/{jid}",
                       headers=h).json()
            if st.get("status") in ("transcribed", "failed"):
                break
            time.sleep(5)
        if st.get("status") == "failed":
            raise RuntimeError(f"rev.ai: {st.get('failure_detail')}")
        d = c.get(f"https://api.rev.ai/speechtotext/v1/jobs/{jid}/transcript",
                  headers={**h, "Accept":
                           "application/vnd.rev.transcript.v1.0+json"}).json()
    wall = time.time() - t0

    turns = []
    for m in d.get("monologues", []):
        els = [e for e in m.get("elements", []) if e.get("type") == "text"]
        if not els:
            continue
        ws = [Word(e["value"], e.get("ts"), e.get("end_ts"),
                   f"SPEAKER_{m.get('speaker', 0):02d}") for e in els]
        turns.append(Turn(f"SPEAKER_{m.get('speaker', 0):02d}",
                          els[0].get("ts", 0.0), els[-1].get("end_ts", 0.0),
                          " ".join(e["value"] for e in els), ws))
    notes = ([f"custom vocabulary REJECTED by vendor (digits not allowed): "
              f"{', '.join(dropped)}"] if dropped else [])
    return BenchTranscript("revai", model, turns, _audio_seconds(audio), wall,
                           has_word_timestamps=True, has_word_speakers=False,
                           names_speakers=False, raw=d, notes=notes)


ADAPTERS = {
    "deepgram": run_deepgram,
    "assemblyai": run_assemblyai,
    "speechmatics": run_speechmatics,
    "elevenlabs": run_elevenlabs,
    "revai": run_revai,
    "openai": run_openai,
    "gemini": run_gemini,
    "local": run_local,
}
