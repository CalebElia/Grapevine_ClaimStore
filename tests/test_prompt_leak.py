"""S1 — ASR prompt leakage.

`initial_prompt` is prepended to Whisper as if it were prior transcript, so the decoder
can continue it instead of transcribing the audio. It did, once, on lWvRVUMyLP4: seven
seconds of Missy Stults explaining dig-once coordination were replaced by the prompt's
own sentence, with a clause repeated. Roughly 62 words of real content vanished.

It was found by a human listening, not by any check in this repo, and no statistical
signal would have caught it — the fabricated segment reads 231.9 wpm, is grammatical,
and is on topic. These tests pin the only signal that does work.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.transcribe import detect_prompt_leak

REPO = Path(__file__).parent.parent
VOCAB = json.loads((REPO / "registries" / "ann_arbor" / "asr_vocabulary.json").read_text())

# The actual text Whisper emitted at 23:19-23:26, and the prompt that caused it.
LEAK = ("Speakers include Missy Stults of the Office of Sustainability and Innovations "
        "and AmeriCorps members of the Ann Arbor Climate Corps members of the Ann Arbor "
        "Climate Corps.")
BAD_PROMPT = VOCAB["_prompt_leak_incident"]["old_prompt"]

# Real speech from the same meeting that must never be flagged.
GENUINE = [
    "I think I know most of you, but if not, I'm Missy Stults, the Director of our "
    "Office of Sustainability and Innovations, and I'm joined by colleagues tonight.",
    "All right, so in Ann Arbor, the Office of Sustainability and Innovations, OSI, "
    "hosts our program and is titled Ann Arbor Climate Corps.",
    "We'll also hear tonight about the Agreement Regarding Climate Action, or ARCA, "
    "and progress on the RFPs on the next two-year work plan.",
]


def _seg(text, start=0.0, end=7.0):
    return {"start": start, "end": end, "text": text}


def test_the_actual_leak_is_detected():
    hits = detect_prompt_leak([_seg(LEAK)], BAD_PROMPT)
    assert len(hits) == 1
    assert hits[0]["coverage"] == 1.0


def test_genuine_speech_containing_prompt_terms_is_not_flagged():
    """The prompt necessarily contains the terms people actually say. Flagging those
    would make the warning noise, and a warning everyone ignores is worse than none."""
    assert detect_prompt_leak([_seg(t) for t in GENUINE], BAD_PROMPT) == []


def test_a_single_matching_phrase_is_not_enough():
    """Coverage, not presence, is the discriminator."""
    s = _seg("So the Agreement Regarding Climate Action is really about what happens "
             "next year when the pilot money runs out and we have to find another way "
             "to pay for all of this work across the whole city.")
    assert detect_prompt_leak([s], BAD_PROMPT) == []


def test_partial_regurgitation_is_caught():
    """A leak need not reproduce the whole prompt to have destroyed the audio."""
    s = _seg("the Sustainable Energy Utility SEU networked geothermal in the Bryant "
             "neighborhood Solarize municipalization")
    assert len(detect_prompt_leak([s], BAD_PROMPT)) == 1


def test_current_shipped_prompt_is_a_term_list_not_prose():
    """The structural fix. Prose is continuable; a term list is not. Guards against
    someone helpfully rewriting the prompt into a nice sentence again."""
    p = VOCAB["_prompt_template"]
    assert p.count(",") >= 10, "prompt should be a comma-separated term list"
    for phrase in ("This is a", "Speakers include", "They discuss"):
        assert phrase not in p, f"prompt reads as prose ({phrase!r}) — that is the bug"


def test_shipped_prompt_produces_no_hits_on_the_real_transcript():
    """Regression against the full 1284-segment transcript, not a synthetic sample."""
    tp = REPO / "processing" / "lWvRVUMyLP4" / "transcript.json"
    if not tp.exists():
        import pytest
        pytest.skip("cached transcript not present")
    segs = json.loads(tp.read_text())["segments"]
    assert detect_prompt_leak(segs, VOCAB["_prompt_template"]) == []


def test_old_prompt_flags_exactly_one_segment_of_the_real_transcript():
    tp = REPO / "processing" / "lWvRVUMyLP4" / "transcript.json"
    if not tp.exists():
        import pytest
        pytest.skip("cached transcript not present")
    segs = json.loads(tp.read_text())["segments"]
    hits = detect_prompt_leak(segs, BAD_PROMPT)
    assert len(hits) == 1 and 1399 <= hits[0]["start"] <= 1400
