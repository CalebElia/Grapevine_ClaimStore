"""Scoring. Vendor-agnostic — runs against any BenchTranscript.

THE OBJECTIVE IS REVIEW BURDEN, NOT ACCURACY. The stated goal is "a diarized transcript
that requires as little review as possible." Those are different targets: a transcript
at 99.6% word accuracy that names zero speakers and shatters roll call into 47 useless
fragments costs more human time than one at 98% that gets structure right. So every
metric below is chosen for how it maps onto minutes of human work, and `review_burden`
combines them into the single number that answers the actual question.

FIVE FAMILIES:
  1. WORD      what was said                    — WER, plus entity WER which matters more
  2. SPEAKER   who said it                      — DER, purity, coverage, count error
  3. TURN      is it legible                    — fragments, welding, over-segmentation
  4. TIME      can a claim be located           — offset vs reference alignment
  5. PRACTICAL cost, wall-clock, determinism, coverage

WHAT EACH METRIC CAN HONESTLY CLAIM is stated on the function. Two need care:

  * TIMESTAMP accuracy has no gold standard here. Our own WhisperX forced alignment is
    the reference, and WhisperX is also a candidate. That is circular and the incumbent
    gets an unearned advantage. Reported anyway because relative differences between
    the OTHER vendors are still informative, but it is labelled `reference` not `gold`
    and must never be the deciding metric.

  * TURN quality is measured INTRINSICALLY — fragment rate, words per turn, turns per
    minute — precisely so it needs no reference segmentation. Scoring turns against our
    turns.json would score every vendor on how well it imitates the incumbent, which is
    the opposite of what we want to learn.
"""
from __future__ import annotations

import re
import statistics as st
from dataclasses import dataclass
from difflib import SequenceMatcher

from pipeline.bench.types import BenchTranscript, Turn

FILLER = {"uh", "um", "mm", "hmm", "mhm", "ah", "er"}


def toks(s: str, drop_filler: bool = True) -> list[str]:
    w = re.sub(r"[^a-z0-9' ]", " ", (s or "").lower()).split()
    return [x for x in w if not (drop_filler and x in FILLER)]


# ───────────────────────────── 1. WORD ─────────────────────────────

def wer(reference: str, hypothesis: str) -> dict:
    """Word error rate, filler-insensitive.

    CLAIM: how much of what was said survived. Nothing about who said it.
    Filler words are stripped because vendors differ wildly in whether they emit 'uh',
    and being punished for transcribing a real disfluency is noise, not signal.
    """
    r, h = toks(reference), toks(hypothesis)
    if not r:
        return {"wer": None, "ref_words": 0}
    sm = SequenceMatcher(None, r, h, autojunk=False)
    sub = dele = ins = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            sub += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            dele += i2 - i1
        elif tag == "insert":
            ins += j2 - j1
    return {"wer": (sub + dele + ins) / len(r), "substitutions": sub,
            "deletions": dele, "insertions": ins, "ref_words": len(r)}


def entity_accuracy(hypothesis: str, entities: dict[str, list[str]],
                    expected: dict[str, int] | None = None) -> dict:
    """Did the load-bearing proper nouns survive?

    CLAIM: this matters MORE than WER for a claim store. A transcript at 99% WER that
    renders 'A2Zero' as 'A20' every time returns nothing on entity match — silently.
    Measured on this corpus: our vocabulary-primed run got 12/12; Gemini got 0/4.

    `entities` maps a canonical term to the wrong spellings observed in the wild, so a
    vendor is scored on getting it RIGHT, not merely on saying something similar.

    `expected` is the count of each term in the HUMAN-VERIFIED text, and without it this
    metric can be gamed by silence. Scoring correct/(correct+known_mangled) means a
    vendor that renders 'A2Zero' as some spelling we never anticipated — or drops the
    word entirely — registers zero of each and is skipped, then scores 1.00 on the
    remaining entities. Rev.ai did exactly that on the live run and took first place on
    entity accuracy by never producing the term at all. Where we know from gold how many
    times a term was actually said, score against THAT.
    """
    h = (hypothesis or "").lower()
    expected = expected or {}
    rows = {}
    for canon, wrong in entities.items():
        good = len(re.findall(re.escape(canon.lower()), h))
        # A "wrong" spelling that differs from the canonical only by case is not wrong —
        # 'Arca' vs 'ARCA' collides after lowercasing and would count every correct
        # rendering as an error too, halving the score for free.
        wrong = [w for w in wrong if w.lower() != canon.lower()]
        bad = sum(len(re.findall(r"\b" + re.escape(w.lower()) + r"\b", h)) for w in wrong)
        want = expected.get(canon, 0)
        if want:
            acc = min(good / want, 1.0)         # gold says how many times it was said
            basis = "verified_count"
        elif good + bad:
            acc = good / (good + bad)
            basis = "observed_variants"
        else:
            acc, basis = None, "not_present"
        rows[canon] = {"correct": good, "mangled": bad, "expected": want or None,
                       "accuracy": acc, "basis": basis}
    scored = [v["accuracy"] for v in rows.values() if v["accuracy"] is not None]
    return {"per_entity": rows,
            "entity_accuracy": (sum(scored) / len(scored)) if scored else None,
            "entities_fully_mangled": sum(1 for v in rows.values()
                                          if v["accuracy"] == 0.0),
            "scored_against_gold": sum(1 for v in rows.values()
                                       if v["basis"] == "verified_count")}


# ───────────────────────────── 2. SPEAKER ─────────────────────────────

@dataclass
class RefSegment:
    start: float
    end: float
    speaker: str           # a real person's name in the reference


def _optimal_map(tx: BenchTranscript, ref: list[RefSegment]) -> dict[str, str]:
    """Greedy cluster -> reference-speaker mapping by shared speech time.

    Diarization labels are arbitrary (SPEAKER_00 means nothing), so any comparison
    needs a mapping first. Greedy on overlap rather than Hungarian: with <30 speakers
    the difference is negligible and greedy is inspectable, which matters more here.
    """
    overlap: dict[tuple[str, str], float] = {}
    for t in tx.turns:
        for r in ref:
            o = min(t.end, r.end) - max(t.start, r.start)
            if o > 0:
                overlap[(t.speaker, r.speaker)] = overlap.get((t.speaker, r.speaker), 0) + o
    mapping, used = {}, set()
    for (cl, sp), _ in sorted(overlap.items(), key=lambda kv: -kv[1]):
        if cl in mapping or sp in used:
            continue
        mapping[cl] = sp
        used.add(sp)
    return mapping


def diarization_error(tx: BenchTranscript, ref: list[RefSegment],
                      collar: float = 0.25) -> dict:
    """DER over a time grid: confusion + miss + false alarm, as a fraction of speech.

    CLAIM: the standard measure of who-spoke-when. A `collar` ignores +/-0.25s around
    every reference boundary, which is convention — human-marked boundaries are not
    precise to the frame and punishing sub-quarter-second disagreement measures
    annotation noise.
    """
    if not ref:
        return {"der": None}
    mapping = _optimal_map(tx, ref)
    step = 0.01
    lo, hi = min(r.start for r in ref), max(r.end for r in ref)
    bounds = [r.start for r in ref] + [r.end for r in ref]

    def near_boundary(t):
        return any(abs(t - b) < collar for b in bounds)

    total = conf = miss = fa = 0
    t = lo
    while t < hi:
        if near_boundary(t):
            t += step
            continue
        true = next((r.speaker for r in ref if r.start <= t < r.end), None)
        # MISS means the system heard SILENCE where the reference has speech. It must not
        # be conflated with "the system heard someone, but that cluster has no slot in a
        # one-to-one mapping" — which is a CONFUSION. Getting this wrong charged every
        # vendor that split a speaker across two clusters with a miss for the whole of
        # the second cluster, and produced an incredible ~50% miss rate for six
        # commercial diarizers at once. When six independent systems all look broken,
        # the measurement is broken.
        turn = next((x for x in tx.turns if x.start <= t < x.end), None)
        got = mapping.get(turn.speaker) if turn is not None else None
        if true is not None:
            total += 1
            if turn is None:
                miss += 1
            elif got != true:
                conf += 1
        elif turn is not None:
            fa += 1
        t += step
    if not total:
        return {"der": None}
    return {"der": (conf + miss + fa) / total,
            "confusion": conf / total, "missed": miss / total,
            "false_alarm": fa / total, "cluster_map": mapping}


def cluster_health(tx: BenchTranscript, ref: list[RefSegment]) -> dict:
    """Splitting, merging, and junk clusters.

    CLAIM: this is where review time actually goes. A person split across two clusters
    must be named twice and merged. A cluster holding two people must be picked apart
    turn by turn. And a JUNK cluster — SPEAKER_11 in this meeting, 15 sub-second
    fragments totalling 5.7s spread over 110 minutes, which S4 confidently named
    'Sara M Nedrich' — costs a human a listening pass to disprove.

    `purity` = of the reference speech a cluster covers, what fraction is one person.
    `coverage` = of one person's speech, what fraction lands in their biggest cluster.
    """
    per_cluster: dict[str, dict[str, float]] = {}
    for t in tx.turns:
        for r in ref:
            o = min(t.end, r.end) - max(t.start, r.start)
            if o > 0:
                per_cluster.setdefault(t.speaker, {})
                per_cluster[t.speaker][r.speaker] = \
                    per_cluster[t.speaker].get(r.speaker, 0) + o
    purities, junk = [], []
    for cl, m in per_cluster.items():
        tot = sum(m.values())
        top = max(m.values())
        p = top / tot if tot else 0
        purities.append(p)
        if p < 0.8:
            junk.append({"cluster": cl, "purity": round(p, 2),
                         "spans_speakers": len(m), "seconds": round(tot, 1)})
    by_speaker: dict[str, dict[str, float]] = {}
    for cl, m in per_cluster.items():
        for sp, sec in m.items():
            by_speaker.setdefault(sp, {})[cl] = sec
    coverages, split = [], []
    for sp, m in by_speaker.items():
        tot, top = sum(m.values()), max(m.values())
        coverages.append(top / tot if tot else 0)
        if len(m) > 1 and top / tot < 0.9:
            split.append({"speaker": sp, "clusters": len(m),
                          "largest_share": round(top / tot, 2)})
    ref_speakers = len({r.speaker for r in ref})
    return {
        "clusters_found": len(tx.speakers),
        "reference_speakers": ref_speakers,
        "speaker_count_error": len(tx.speakers) - ref_speakers,
        "mean_purity": round(st.mean(purities), 3) if purities else None,
        "mean_coverage": round(st.mean(coverages), 3) if coverages else None,
        "impure_clusters": junk,          # holds >1 person
        "split_speakers": split,          # one person across many clusters
    }


def naming(tx: BenchTranscript, ref_names: set[str]) -> dict:
    """Does the vendor produce NAMES, and are they right?

    CLAIM: the single largest lever on review burden. Gemini named 22 of 24 labels in
    one call; our S4 heuristics named 10 of 21 and needed 30 minutes of human work to
    reach 20. A vendor that names correctly removes that entire task.
    Scored loosely on surname, because spelling is the registry's job — 'Malik' for
    Mallek is a lookup problem, not a diarization failure.
    """
    if not tx.names_speakers:
        return {"names_speakers": False, "named": 0, "correct": 0, "accuracy": None}
    got = {t.speaker_name for t in tx.turns if t.speaker_name}
    surn = {n.lower().split()[-1] for n in ref_names}
    ok = sum(1 for g in got if g and g.lower().split()[-1] in surn)
    return {"names_speakers": True, "named": len(got), "correct": ok,
            "accuracy": ok / len(got) if got else None,
            "unmatched": sorted(g for g in got
                                if g and g.lower().split()[-1] not in surn)}


# ───────────────────────────── 3. TURN ─────────────────────────────

def coalesce(tx: BenchTranscript, gap: float = 2.0) -> BenchTranscript:
    """Merge consecutive same-speaker turns. MUST run before any turn metric.

    Vendors do not agree on what a "turn" is, and comparing their raw output measures
    that disagreement rather than quality. Measured here: inside Ken Garber's single
    uninterrupted 3-minute public comment, Deepgram returned 53 turns while AssemblyAI,
    ElevenLabs and Rev.ai returned 2. Deepgram's `utterances` are sentence-level; the
    others are speaker-level. Scored raw, Deepgram looked catastrophically fragmented
    (26%) for making a different — and entirely defensible — segmentation choice.

    So normalise every vendor to the same unit before judging legibility: a turn is a
    maximal run of one speaker with no gap longer than `gap`. That is exactly what S3
    does to pyannote's output, which is also the only reason our own numbers were ever
    comparable to anyone's.
    """
    out: list[Turn] = []
    for t in sorted(tx.turns, key=lambda x: x.start):
        if out and out[-1].speaker == t.speaker and t.start - out[-1].end <= gap:
            prev = out[-1]
            prev.end = max(prev.end, t.end)
            prev.text = (prev.text + " " + t.text).strip()
            prev.words = prev.words + t.words
        else:
            out.append(Turn(t.speaker, t.start, t.end, t.text, list(t.words),
                            t.speaker_name))
    merged = BenchTranscript(tx.vendor, tx.model, out, tx.audio_seconds,
                             tx.wall_seconds, tx.has_word_timestamps,
                             tx.has_word_speakers, tx.names_speakers,
                             tx.cost_usd, None, list(tx.notes))
    if len(out) != len(tx.turns):
        merged.notes.append(f"turns coalesced {len(tx.turns)} -> {len(out)} "
                            f"at {gap}s gap for comparability")
    return merged


def turn_quality(tx: BenchTranscript, ref: list[RefSegment] | None = None) -> dict:
    """Is this transcript legible to a human reading it?

    CLAIM: measured INTRINSICALLY, with no reference segmentation, so no vendor is
    rewarded for imitating the incumbent. These are the numbers that correspond to the
    complaint "very irregular pattern for when they are folded in":

      fragment_rate   turns under 2 words or under 0.5s. Ours: 67% of interjections
                      carried nothing usable. A fragment is pure review cost — it has
                      to be read, understood as noise, and dismissed.
      empty_rate      turns with no text at all. Ours: 33 of 70 interjections.
      welded_rate     (needs ref) turns containing more than one true speaker. The
                      expensive error: a claim inherits its turn's speaker.
      turns_per_min   over-segmentation. A human-legible transcript of a meeting runs
                      roughly 2-6 turns/min; roll call spikes it legitimately.
      median_words    longer coherent turns read better and extract better.
    """
    n = len(tx.turns)
    if not n:
        return {"turns": 0}
    empty = sum(1 for t in tx.turns if not t.text.strip())
    frag = sum(1 for t in tx.turns
               if t.text.strip() and (t.word_count <= 2 or t.duration < 0.5))
    words = [t.word_count for t in tx.turns if t.text.strip()]
    out = {
        "turns": n,
        "turns_per_min": round(n / (tx.audio_seconds / 60), 2),
        "empty_turns": empty,
        "empty_rate": round(empty / n, 3),
        "fragment_turns": frag,
        "fragment_rate": round(frag / n, 3),
        "median_words_per_turn": st.median(words) if words else 0,
        "median_turn_seconds": round(st.median([t.duration for t in tx.turns]), 2),
    }
    if ref:
        welded = 0
        for t in tx.turns:
            spk = set()
            for r in ref:
                o = min(t.end, r.end) - max(t.start, r.start)
                if o > 0.4:                 # ignore slivers at the boundary
                    spk.add(r.speaker)
            if len(spk) > 1:
                welded += 1
        out["welded_turns"] = welded
        out["welded_rate"] = round(welded / n, 3)
    return out


# ───────────────────────────── 4. TIME ─────────────────────────────

def timestamp_accuracy(tx: BenchTranscript, reference_words: list[tuple[str, float]],
                       tolerance: float = 0.5) -> dict:
    """Offset between a vendor's word times and a reference alignment.

    CLAIM IS LIMITED, ON PURPOSE. There is no gold timing here. The reference is our own
    WhisperX wav2vec2 forced alignment, and WhisperX is itself a candidate — so the
    incumbent scores against its own output and will look perfect. Read this metric
    only to compare the OTHER vendors against each other, and never let it decide.

    To make it gold, force-align the human-verified TEXT against the audio and use those
    timings; that reference would be independent of every candidate. Worth doing before
    this metric carries any weight.
    """
    hyp = [(w.text, w.start) for w in tx.words() if w.start is not None]
    if not hyp or not reference_words:
        return {"median_offset_s": None, "reference": "none"}
    a = [t.lower() for t, _ in reference_words]
    b = [re.sub(r"[^a-z0-9']", "", t.lower()) for t, _ in hyp]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    offs = []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            offs.append(abs(reference_words[blk.a + k][1] - hyp[blk.b + k][1]))
    if not offs:
        return {"median_offset_s": None, "reference": "whisperx_forced_alignment"}
    offs.sort()
    return {
        "median_offset_s": round(st.median(offs), 3),
        "p90_offset_s": round(offs[int(len(offs) * 0.9)], 3),
        "within_tolerance": round(sum(1 for o in offs if o <= tolerance) / len(offs), 3),
        "aligned_words": len(offs),
        "reference": "whisperx_forced_alignment (NOT gold — see docstring)",
        "interpolated": not tx.has_word_timestamps,
    }


# ───────────────────────────── 5. PRACTICAL ─────────────────────────────

def coverage(tx: BenchTranscript, min_gap: float = 5.0) -> dict:
    """How much of the audio produced no transcript at all.

    CLAIM: silence is legitimate (slides, video playback), so a gap is not automatically
    a defect. But ours had 6.9 minutes of >=5s holes across 113 minutes and one of them
    swallowed 13 seconds of real speech next to the prompt leak. Large gaps are where
    lost content hides.
    """
    ts = sorted(tx.turns, key=lambda t: t.start)
    spoken = sum(t.duration for t in ts)
    gaps = [(a.end, b.start - a.end) for a, b in zip(ts, ts[1:]) if b.start - a.end >= min_gap]
    return {"speech_seconds": round(spoken, 1),
            "audio_seconds": round(tx.audio_seconds, 1),
            "speech_ratio": round(spoken / tx.audio_seconds, 3) if tx.audio_seconds else None,
            "gaps_over_5s": len(gaps),
            "gap_seconds_total": round(sum(g for _, g in gaps), 1),
            "largest_gap_s": round(max((g for _, g in gaps), default=0), 1)}


def review_burden(word: dict, spk: dict, turn: dict, name: dict,
                  ent: dict) -> dict:
    """THE HEADLINE NUMBER: estimated human edits to make this transcript trustworthy.

    Not a real time estimate — a comparable proxy, built only from things a reviewer
    demonstrably has to fix. Weights come from the one review we have actually watched:
    naming a cluster took ~60s, dismissing a fragment ~5s, un-welding a turn ~90s,
    fixing an entity ~20s. They are rough and they are stated here so they can be argued
    with rather than hidden inside a score.
    """
    W_NAME, W_FRAG, W_WELD, W_ENT, W_SPLIT = 60, 5, 90, 20, 60
    # UNDER-CLUSTERING IS THE MOST EXPENSIVE FAILURE, and the first version of this
    # metric rewarded it. Charging one naming decision per cluster meant a system that
    # collapsed 14 people into 3 clusters was billed for 3 names instead of 14 — and
    # ranked FIRST. Measured: on this 8-minute window Deepgram and AssemblyAI both
    # returned 3 speakers where the human-confirmed count is 14.
    #
    # Merged speakers are not merely unlabelled, they are UNRECOVERABLE from the review
    # sheet: a reviewer can rename a cluster in one edit, but cannot split one, so every
    # affected turn has to be reassigned individually. So charge for the real number of
    # people, and charge the shortfall at the per-turn rate rather than the per-name one.
    n_ref = spk.get("reference_speakers") or 0
    n_got = spk.get("clusters_found") or 0
    if name.get("names_speakers"):
        unnamed = max(0, name.get("named", 0) - name.get("correct", 0))
    else:
        unnamed = max(n_got, n_ref)
    merged_shortfall = max(0, n_ref - n_got)
    frags = turn.get("fragment_turns", 0) + turn.get("empty_turns", 0)
    weld = turn.get("welded_turns", 0)
    ents = ent.get("entities_fully_mangled", 0)
    splits = len(spk.get("split_speakers", []) or [])
    # A merged speaker costs a WELD-sized fix for every person that was absorbed.
    secs = (unnamed * W_NAME + frags * W_FRAG + weld * W_WELD +
            ents * W_ENT + splits * W_SPLIT + merged_shortfall * W_WELD)
    return {"estimated_review_seconds": secs,
            "estimated_review_minutes": round(secs / 60, 1),
            "components": {"unnamed_or_wrong_speakers": unnamed, "fragments": frags,
                           "welded_turns": weld, "mangled_entities": ents,
                           "split_speakers": splits,
                           "merged_speakers_missing": merged_shortfall},
            "weights_seconds": {"name": W_NAME, "fragment": W_FRAG, "weld": W_WELD,
                                "entity": W_ENT, "split": W_SPLIT,
                                "merged_speaker": W_WELD}}
