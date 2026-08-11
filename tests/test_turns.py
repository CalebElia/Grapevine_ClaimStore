"""S3 tests, run against the real cached diarization and transcript.

These are not synthetic fixtures. tests/fixtures/ holds the actual pyannote and
Whisper output for the Ann Arbor Sustainability Commission meeting of 2026-03-10
(YouTube id lWvRVUMyLP4), copied read-only from ../video_analysis. Every threshold
below is a v1 measurement that the rewrite has to beat.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.turns import (  # noqa: E402
    GAP_TOLERANCE_S,
    Turn,
    absorb_interjections,
    build_turns,
    coalesce_speaker_runs,
    make_utterance_key,
)

FIXTURES = Path(__file__).parent / "fixtures"
MEDIA_ID = "vid_yt_lWvRVUMyLP4"


@pytest.fixture(scope="module")
def diarization():
    return json.loads((FIXTURES / "v2_diarization.json").read_text())["segments"]


@pytest.fixture(scope="module")
def transcript():
    return json.loads((FIXTURES / "v1_transcript.json").read_text())


@pytest.fixture(scope="module")
def built(diarization, transcript):
    return build_turns(diarization, transcript["segments"], MEDIA_ID)


# ------------------------------------------------------------------- identity
def test_utterance_key_is_unique_per_boundary():
    a = make_utterance_key("m", 0, 1000, 2000)
    b = make_utterance_key("m", 1, 1000, 2000)
    c = make_utterance_key("m", 0, 1001, 2000)
    assert len({a, b, c}) == 3


def test_utterance_key_is_stable():
    assert make_utterance_key("m", 7, 1234, 5678) == make_utterance_key("m", 7, 1234, 5678)


def test_utterance_key_rejects_inverted_span():
    with pytest.raises(ValueError):
        make_utterance_key("m", 0, 5000, 1000)


def test_no_key_collisions_on_real_meeting(built):
    """v1's 10-second rounding produced 631 unique ids for 1,156 turns; 45% of
    turns were served another turn's extraction."""
    turns, _ = built
    keys = [t.key for t in turns]
    assert len(keys) == len(set(keys))


def test_sequences_are_dense_and_ordered(built):
    turns, _ = built
    assert [t.sequence for t in turns] == list(range(len(turns)))


# -------------------------------------------------------------------- merging
def test_coalescing_actually_reduces_turn_count(diarization):
    """v1 reduced 1,198 segments to 1,156 turns — 3.5% — while 66% of adjacent
    pairs shared a speaker."""
    assert len(diarization) == 1198
    runs = coalesce_speaker_runs(diarization)
    assert len(runs) < 600, f"expected substantial coalescing, got {len(runs)} runs"


def test_turn_count_in_expected_band(built):
    """1,198 diarization segments reduce through four stages:
        coalesce same-speaker   -> 470
        absorb A-shortB-A       -> 320
        drop empty-text turns   -> 293
        final same-speaker pass -> ~291
    A band rather than an exact number, so tuning GAP_TOLERANCE_S does not require
    editing the test, but tight enough that a regression to v1's 1,156 fragments —
    or an over-merge collapsing the meeting into a handful of blocks — fails."""
    turns, _ = built
    assert 240 <= len(turns) <= 360, (
        f"{len(turns)} turns is outside the band measured at a 2.0s gap tolerance"
    )


def test_median_duration_is_not_two_seconds(built):
    """The '73% of utterances are under 5 seconds' figure that the extraction
    economics were built on described diarization fragments, not utterances."""
    turns, _ = built
    durations = sorted(t.duration for t in turns)
    median = durations[len(durations) // 2]
    assert median > 3.0, f"median duration still {median:.1f}s — merging did not work"


def test_no_two_adjacent_turns_share_a_speaker_within_tolerance(built):
    turns, _ = built
    for a, b in zip(turns, turns[1:]):
        if a.speaker == b.speaker:
            assert b.start - a.end > GAP_TOLERANCE_S, (
                f"turns {a.sequence}/{b.sequence} share speaker {a.speaker} "
                f"with only {b.start - a.end:.2f}s between them"
            )


def test_interjection_sandwich_is_absorbed():
    runs = [
        Turn(speaker="A", start=0.0, end=30.0, source_segments=[0]),
        Turn(speaker="B", start=30.5, end=31.5, source_segments=[1]),
        Turn(speaker="A", start=32.0, end=60.0, source_segments=[2]),
    ]
    out = absorb_interjections(runs)
    assert len(out) == 1
    assert out[0].speaker == "A" and out[0].start == 0.0 and out[0].end == 60.0
    assert [i.speaker for i in out[0].interjections] == ["B"]


def test_long_middle_turn_is_not_absorbed():
    """A genuine hand-off must not be swallowed as an aside."""
    runs = [
        Turn(speaker="A", start=0.0, end=30.0),
        Turn(speaker="B", start=30.5, end=90.0),   # 59.5s — a real turn
        Turn(speaker="A", start=90.5, end=120.0),
    ]
    assert len(absorb_interjections(runs)) == 3


# ------------------------------------------------------------ text integrity
def test_text_is_not_duplicated_across_turns(built, transcript):
    """v1 emitted 1.79x the transcript's character count, because it collected
    every transcript segment OVERLAPPING each turn window and adjacent windows
    overlapped the same segments."""
    turns, _ = built
    produced = sum(len(t.text) for t in turns)
    source = len(transcript["text"])
    ratio = produced / source
    assert ratio <= 1.05, f"text ratio {ratio:.2f}x — duplication is back"


def test_most_of_the_transcript_survives(built, transcript):
    """The duplication fix must not silently drop content instead."""
    turns, _ = built
    ratio = sum(len(t.text) for t in turns) / len(transcript["text"])
    assert ratio >= 0.90, f"only {ratio:.2f}x of the transcript was assigned to turns"


def test_no_substantial_text_is_repeated_between_neighbours(built):
    """v1: 516 of 1,155 adjacent pairs (44.6%) had one turn's text wholly inside
    the next one's, because text was collected by interval overlap.

    The threshold matters. Bare containment also fires on legitimate short
    utterances — 'Second.' inside a motion, 'Here in Ann Arbor.' recurring through
    a roll call, and two people saying 'Thanks, everyone.' over each other at
    adjournment (genuine crosstalk, 2.3s of overlap, correctly transcribed twice).
    Those are not duplication. Reuse of a substantial span is."""
    turns, _ = built
    repeated = [
        (a.sequence, b.sequence)
        for a, b in zip(turns, turns[1:])
        if a.text and b.text
        and len(min(a.text, b.text, key=len)) >= 40
        and (a.text in b.text or b.text in a.text)
    ]
    assert not repeated, f"substantial text reused across pairs: {repeated}"


# ------------------------------------------------- interjection attribution
def test_interjection_words_go_to_the_interjector_not_the_speaker():
    """absorb_interjections produces one turn spanning the whole A-shortB-A window,
    so B's words sit inside A's span. Assigning them to A would credit the
    interjector's speech to the primary speaker — the same misattribution class
    that made v1's output unusable, arriving by a different route."""
    from pipeline.turns import assign_words

    turns = [
        Turn(speaker="A", start=0.0, end=30.0),
        Turn(speaker="B", start=30.5, end=31.5),
        Turn(speaker="A", start=32.0, end=60.0),
    ]
    merged = absorb_interjections(turns)
    assert len(merged) == 1

    words = [
        {"text": " Alpha", "start": 10.0, "end": 10.5},
        {"text": " objection", "start": 30.7, "end": 31.2},   # B's
        {"text": " Omega", "start": 40.0, "end": 40.5},
    ]
    assign_words(merged, words)

    assert merged[0].text == "Alpha Omega"
    assert "objection" not in merged[0].text
    assert merged[0].interjections[0].text == "objection"


def test_real_interjections_carry_their_own_text(built):
    turns, _ = built
    injs = [i for t in turns for i in t.interjections]
    assert injs, "no interjections were absorbed at all — absorb pass is inert"
    assert [i for i in injs if i.text], \
        "every interjection lost its text to the containing turn"


def test_no_word_is_counted_twice_including_interjections(built, transcript):
    """The real anti-duplication invariant, and the one worth asserting.

    A substring check cannot express it: during roll call some seventeen people
    each say 'Here in Ann Arbor.', so the same phrase legitimately appears in a
    turn and in an interjection as different word instances. Character
    conservation across BOTH buckets is the property that actually holds —
    assign_words routes each word to exactly one destination, so total output can
    never exceed total input. v1 emitted 1.79x."""
    turns, _ = built
    produced = sum(len(t.text) for t in turns) + sum(
        len(i.text) for t in turns for i in t.interjections
    )
    ratio = produced / len(transcript["text"])
    assert ratio <= 1.05, f"total text ratio {ratio:.2f}x — something is duplicating"


# ----------------------------------------------------------- empty utterances
def test_no_empty_text_turns_are_returned(built):
    """v1 produced 71 empty-text turns and every one received a fabricated claim,
    including 'He is present for the roll call, calling from Ann Arbor's second
    ward' generated from no words at all."""
    turns, _ = built
    assert all(t.text.strip() for t in turns)


def test_empty_turns_are_reported_not_discarded(built):
    """Silence where diarization heard speech is a review item, not a non-event."""
    turns, dropped = built
    assert all(d.reason == "empty_verbatim" for d in dropped)
    # Sanity: dropping should be a minority behaviour, not the main path.
    assert len(dropped) < len(turns)


# ------------------------------------------------------------------ ordering
def test_turns_are_time_ordered_and_well_formed(built):
    turns, _ = built
    for t in turns:
        assert t.end >= t.start
    for a, b in zip(turns, turns[1:]):
        assert b.start >= a.start


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
