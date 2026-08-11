"""S3 — build speaker turns from diarization + transcript.

This replaces v1's `v4_merge_turns.py`, which had three defects that together made
its analytical output unusable. All three are fixed here, and the tests in
tests/test_turns.py assert each fix against the real cached diarization.

DEFECT 1 — identity depended on a rounded float.
    v1: make_segment_id hashed (video, speaker, round(start/10)*10). Any two turns
    by the same speaker starting inside one 10-second bucket collided. On the one
    processed meeting, 1,156 turns produced 631 unique ids, and 525 turns (45%)
    were served another turn's extraction because results were stored in a dict
    keyed by that id.
    FIX: make_utterance_key uses exact millisecond boundaries plus a dense
    sequence number. Uniqueness is additionally enforced by the database
    (utterances UNIQUE (media_asset_id, sequence)).

DEFECT 2 — consecutive same-speaker segments were never merged.
    v1's only merge rule was the interjection sandwich (A, short-B, A). pyannote
    emits a segment per speech burst, so a commissioner talking for three minutes
    with breathing pauses became dozens of "turns". 1,198 diarization segments
    became 1,156 turns — a 3.5% reduction — while 66% of adjacent pairs shared a
    speaker. The resulting 2-second median duration was then read as a fact about
    how people speak, and the extraction economics were built on it.
    FIX: coalesce_speaker_runs merges consecutive same-speaker segments across
    gaps up to GAP_TOLERANCE_S.

DEFECT 3 — text was collected by interval overlap, so it duplicated.
    v1's collect_text gathered every transcript segment overlapping a turn's
    window. Adjacent fragments overlap the same Whisper segments, so the same
    sentence was emitted two or three times: 44.6% of adjacent turn pairs had one
    turn's text fully contained in its neighbour's, and total turn text ran to
    1.79x the transcript's own character count.
    FIX: assign_words routes each word to exactly one turn by its midpoint. A
    word cannot land in two turns, so duplication is structurally impossible
    rather than merely reduced.

A fourth issue is handled by dropping rather than fixing: 71 v1 turns had empty
verbatim_text (diarization heard voice where Whisper transcribed nothing) and all
71 received a fabricated core_claim, including a specific factual assertion about
which ward a person represents. Turns with no words never become utterances here;
they are returned separately for the review queue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

# Maximum silence between two same-speaker diarization segments that still counts
# as one continuous turn. Measured on the Sustainability Commission meeting:
#   0.0s -> 1,156 turns (v1 behaviour)   0.5s -> 848    1.0s -> 576
#   2.0s -> 470                          3.0s -> 436
# 2.0s is the knee: it absorbs breathing and thinking pauses while still splitting
# on a genuine hand-off. Raising it to 3.0s buys only 34 more merges and starts
# swallowing short answers.
GAP_TOLERANCE_S = 2.0

# A turn shorter than this by a different speaker, sandwiched between two turns by
# the same speaker, is treated as an interjection rather than a hand-off
# ("mm-hm", "sorry, one second", "can you repeat that?").
INTERJECTION_MAX_S = 3.0


@dataclass
class Interjection:
    speaker: str
    start: float
    end: float
    text: str = ""


@dataclass
class Turn:
    speaker: str
    start: float
    end: float
    text: str = ""
    interjections: list[Interjection] = field(default_factory=list)
    # Diarization segment indices this turn was built from. Kept for provenance:
    # it is the only way to audit a merge decision after the fact.
    source_segments: list[int] = field(default_factory=list)
    sequence: int | None = None
    key: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class DroppedTurn:
    """A turn that never becomes an utterance, with the reason why.

    These are not discarded silently. Each becomes a review_queue row so that
    "diarization heard something Whisper did not" stays visible instead of
    becoming a claim with no words behind it.
    """
    speaker: str
    start: float
    end: float
    reason: str


def make_utterance_key(
    media_asset_id: str, sequence: int, start_ms: int, end_ms: int
) -> str:
    """Stable, collision-free identity for one utterance.

    Re-run stability is real and worth having — it is why v1 rounded — but it must
    come from determinism upstream (same audio -> same diarization -> same
    sequence), never from discarding precision in a primary key.
    """
    if start_ms > end_ms:
        raise ValueError(f"start_ms {start_ms} after end_ms {end_ms}")
    return f"{media_asset_id}:{sequence:05d}:{start_ms}-{end_ms}"


def coalesce_speaker_runs(
    segments: Sequence[dict], gap_tolerance_s: float = GAP_TOLERANCE_S
) -> list[Turn]:
    """Merge consecutive same-speaker diarization segments into continuous turns.

    Segments are assumed time-ordered by start. Overlaps (crosstalk) yield a
    negative gap, which is clamped to zero and therefore always merges when the
    speaker matches.
    """
    turns: list[Turn] = []
    for idx, seg in enumerate(segments):
        spk, start, end = seg["speaker"], float(seg["start"]), float(seg["end"])
        if turns and turns[-1].speaker == spk:
            gap = max(0.0, start - turns[-1].end)
            if gap <= gap_tolerance_s:
                turns[-1].end = max(turns[-1].end, end)
                turns[-1].source_segments.append(idx)
                continue
        turns.append(Turn(speaker=spk, start=start, end=end, source_segments=[idx]))
    return turns


def absorb_interjections(
    turns: Sequence[Turn], max_interjection_s: float = INTERJECTION_MAX_S
) -> list[Turn]:
    """Fold an A - shortB - A sandwich into a single A turn carrying B as an aside.

    Applied AFTER coalescing, because before coalescing the two A runs are usually
    many fragments each and the pattern is invisible.
    """
    out: list[Turn] = []
    i = 0
    while i < len(turns):
        cur = turns[i]
        if (
            i + 2 < len(turns)
            and turns[i + 1].speaker != cur.speaker
            and turns[i + 1].duration < max_interjection_s
            and turns[i + 2].speaker == cur.speaker
        ):
            mid, resume = turns[i + 1], turns[i + 2]
            merged = Turn(
                speaker=cur.speaker,
                start=cur.start,
                end=resume.end,
                interjections=[
                    *cur.interjections,
                    Interjection(mid.speaker, mid.start, mid.end),
                    *resume.interjections,
                ],
                source_segments=[*cur.source_segments, *resume.source_segments],
            )
            out.append(merged)
            i += 3
            continue
        out.append(cur)
        i += 1
    return out


def _flatten_words(transcript_segments: Iterable[dict]) -> list[dict]:
    """Pull every timed word out of the transcript, in order.

    Whisper occasionally emits a segment with no word list (2 of 1,116 here). Those
    fall back to a single pseudo-word spanning the segment so their text is not
    lost — it will land in whichever turn covers the segment midpoint.
    """
    words: list[dict] = []
    for seg in transcript_segments:
        ws = seg.get("words")
        if ws:
            for w in ws:
                if w.get("start") is None or w.get("end") is None:
                    continue
                words.append(
                    {"text": w["word"], "start": float(w["start"]), "end": float(w["end"])}
                )
        elif seg.get("text", "").strip():
            words.append(
                {
                    "text": seg["text"],
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                }
            )
    words.sort(key=lambda w: w["start"])
    return words


def _join(tokens: list[str]) -> str:
    """Join word tokens, handling BOTH tokenizer conventions.

    Plain Whisper embeds a leading space in each token (' Thank', ' you.'), so
    "".join() is correct. WhisperX's forced alignment strips it ('Thank', 'you.'),
    and "".join() then welds every word together — 'Thankyou.ChairCurtis?'.

    Detecting per-token rather than per-file matters because a single transcript can
    mix them: WhisperX falls back to unaligned Whisper segments where alignment fails.
    """
    out: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        if out and not tok[:1].isspace() and not out[-1][-1:].isspace():
            out.append(" ")
        out.append(tok)
    return "".join(out).strip()


def assign_words(turns: Sequence[Turn], words: Sequence[dict]) -> None:
    """Route each word to exactly one turn or interjection, by midpoint.

    Mutates turns in place.

    Midpoint assignment is what makes duplication impossible: a word has one
    midpoint, so it lands in one place or none. v1 asked "which transcript segments
    overlap this window?", and overlapping windows answered yes repeatedly.

    INTERJECTIONS TAKE PRECEDENCE OVER THEIR CONTAINING TURN. absorb_interjections
    produces a turn spanning the whole A-B-A window, so B's words sit inside A's
    span. Assigning them to A would attribute the interjector's speech to the
    primary speaker — the same misattribution class that made v1's output
    unusable, just arriving by a different route.

    Words falling in no turn (diarized silence, music, seam gaps) attach to the
    nearest turn boundary within GAP_TOLERANCE_S, and are otherwise left out.
    """
    if not turns:
        return
    import bisect

    buckets: list[list[str]] = [[] for _ in turns]
    inj_buckets: list[list[list[str]]] = [[[] for _ in t.interjections] for t in turns]
    starts = [t.start for t in turns]

    for w in words:
        mid = (w["start"] + w["end"]) / 2.0
        i = bisect.bisect_right(starts, mid) - 1
        if 0 <= i < len(turns) and turns[i].start <= mid <= turns[i].end:
            for j, inj in enumerate(turns[i].interjections):
                if inj.start <= mid <= inj.end:
                    inj_buckets[i][j].append(w["text"])
                    break
            else:
                buckets[i].append(w["text"])
            continue
        # nearest boundary within tolerance
        best, best_dist = None, None
        for j in (i, i + 1):
            if 0 <= j < len(turns):
                dist = min(abs(mid - turns[j].start), abs(mid - turns[j].end))
                if best_dist is None or dist < best_dist:
                    best, best_dist = j, dist
        if best is not None and best_dist is not None and best_dist <= GAP_TOLERANCE_S:
            buckets[best].append(w["text"])

    for idx, turn in enumerate(turns):
        turn.text = _join(buckets[idx])
        for j, inj in enumerate(turn.interjections):
            inj.text = _join(inj_buckets[idx][j])


def _final_coalesce(
    turns: list[Turn], dropped: list[DroppedTurn], gap_tolerance_s: float
) -> list[Turn]:
    """Enforce the invariant: no two adjacent turns share a speaker within tolerance.

    Dropping empty turns can re-separate same-speaker turns that coalescing had
    joined, re-fragmenting exactly what S3 exists to fix. Rather than enumerating
    the ways that happens, this pass asserts the invariant directly.

    It is safe to run last because a text-bearing turn by another speaker is still
    a list element, so two same-speaker turns are only ever *adjacent* if whatever
    separated them was dropped or already absorbed.

    Any dropped turn overlapping the merged span is attached as an interjection
    with empty text — an honest record that someone spoke there and we do not know
    what they said. It also remains in `dropped`, so it still raises a review item.
    Observed here on genuine crosstalk: one speaker began, was talked over, and the
    transcript captured only the louder voice.
    """
    if not turns:
        return turns
    out = [turns[0]]
    for nxt in turns[1:]:
        prev = out[-1]
        if prev.speaker != nxt.speaker or nxt.start - prev.end > gap_tolerance_s:
            out.append(nxt)
            continue
        overlapped = [
            d for d in dropped
            if d.end > prev.end and d.start < max(nxt.end, prev.end)
        ]
        prev.interjections.extend(
            Interjection(d.speaker, d.start, d.end, "") for d in overlapped
        )
        prev.interjections.extend(nxt.interjections)
        prev.end = max(prev.end, nxt.end)
        prev.text = f"{prev.text} {nxt.text}".strip()
        prev.source_segments.extend(nxt.source_segments)
    return out


def build_turns(
    diarization_segments: Sequence[dict],
    transcript_segments: Sequence[dict],
    media_asset_id: str,
    gap_tolerance_s: float = GAP_TOLERANCE_S,
    max_interjection_s: float = INTERJECTION_MAX_S,
) -> tuple[list[Turn], list[DroppedTurn]]:
    """Full S3 pass: diarization + transcript -> utterance-ready turns.

    Returns (turns, dropped). Only `turns` may become `utterances` rows; every
    entry in `dropped` needs a review_queue row instead.
    """
    runs = coalesce_speaker_runs(diarization_segments, gap_tolerance_s)
    runs = absorb_interjections(runs, max_interjection_s)
    assign_words(runs, _flatten_words(transcript_segments))

    kept: list[Turn] = []
    dropped: list[DroppedTurn] = []
    for turn in runs:
        if not turn.text.strip():
            dropped.append(
                DroppedTurn(
                    speaker=turn.speaker,
                    start=turn.start,
                    end=turn.end,
                    reason="empty_verbatim",
                )
            )
            continue
        kept.append(turn)

    kept = _final_coalesce(kept, dropped, gap_tolerance_s)

    # Sequence is assigned after filtering so it stays dense, and it is the second
    # half of the database's uniqueness guarantee.
    for seq, turn in enumerate(kept):
        turn.sequence = seq
        turn.key = make_utterance_key(
            media_asset_id, seq, int(round(turn.start * 1000)), int(round(turn.end * 1000))
        )
    return kept, dropped
