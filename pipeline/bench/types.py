"""Normalized transcript format. Every vendor is mapped into this before scoring.

WHY A NORMALIZED FORM AND NOT PER-VENDOR SCORING. Each API returns a different shape:
Deepgram gives speaker-per-word, AssemblyAI gives utterances with word lists, Gemini
gives untimed markdown, ours gives turns with an interjection sidecar. Scoring each in
its own terms makes the numbers incomparable, which is the failure mode of every vendor
bake-off. One format in, one scorer, one table out.

THE MOST IMPORTANT FIELD IS `has_word_speakers`. A vendor that labels every WORD with a
speaker has no join step between ASR and diarization — which is the architectural
difference we are actually testing. A vendor that labels only segments forces the same
midpoint word-assignment that produced our 141 straddling words and 67%-useless
interjections. That distinction should be visible in the results table, not buried.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Word:
    text: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None      # set only when the vendor labels words directly


@dataclass
class Turn:
    speaker: str                    # vendor's own label — cluster id OR a real name
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker_name: str | None = None  # populated when the vendor NAMES people, not just clusters

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class BenchTranscript:
    vendor: str
    model: str
    turns: list[Turn]
    audio_seconds: float
    wall_seconds: float                    # how long the call took, start to finish
    has_word_timestamps: bool
    has_word_speakers: bool
    names_speakers: bool                   # returns real names, not just SPEAKER_00
    cost_usd: float | None = None
    raw: Any = None                        # untouched vendor payload, always kept
    notes: list[str] = field(default_factory=list)

    @property
    def speakers(self) -> set[str]:
        return {t.speaker for t in self.turns}

    def words(self) -> list[Word]:
        """Every word in time order, with a speaker attached however the vendor allows."""
        out: list[Word] = []
        for t in sorted(self.turns, key=lambda x: x.start):
            if t.words:
                for w in t.words:
                    out.append(Word(w.text, w.start, w.end, w.speaker or t.speaker))
            else:
                # No word timings. Spread the turn's words evenly across its span so
                # timestamp metrics can still run — and record that we did, because an
                # interpolated timestamp must never be scored as if it were measured.
                toks = t.text.split()
                if not toks:
                    continue
                step = t.duration / len(toks) if t.duration > 0 else 0.0
                for i, tok in enumerate(toks):
                    out.append(Word(tok, t.start + i * step,
                                    t.start + (i + 1) * step, t.speaker))
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


def normalize_text(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "").strip())
