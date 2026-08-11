"""Reference-free vendor comparison: where do independent systems disagree?

THE PROBLEM THIS SOLVES. Scoring needs ground truth, and ground truth needs a human.
That caps us at whatever someone has hand-verified — 8 minutes of one easy meeting. But
the question that matters is how these systems behave on a 2.5-hour Council meeting with
crosstalk, and nobody has labelled that.

THE SUBSTITUTE. Run several INDEPENDENT systems on the same audio. Where they agree,
they are probably right — three systems converging on the same wrong words is unlikely
when they share no training data, no architecture, and no vendor. Where one differs from
the others, it is probably the one that is wrong. That gives a quality signal on audio
nobody has ever transcribed, and it is exactly the reasoning that produced the vocabulary
entry for 'Missy Stults': Whisper said 'Stoltz' six times, YouTube captions said 'staltz'
four times, two systems guessing at the same sound and disagreeing = a proper noun in
neither lexicon.

WHAT IT CANNOT DO. Consensus is not truth. Three systems can share a blind spot — an
unusual proper noun none of them knows will be mangled consistently and look like
agreement. So disagreement is a strong signal of a problem; AGREEMENT is only weak
evidence of correctness. Every finding here is a lead for a human, never a verdict.
"""
from __future__ import annotations

import re
import statistics as st
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from pipeline.bench.types import BenchTranscript

FILLER = {"uh", "um", "mm", "hmm", "mhm", "ah", "er"}


def _toks(tx: BenchTranscript) -> list[tuple[str, float]]:
    """(word, start) in time order, filler removed."""
    out = []
    for w in tx.words():
        t = re.sub(r"[^a-z0-9']", "", (w.text or "").lower())
        if t and t not in FILLER:
            out.append((t, w.start if w.start is not None else 0.0))
    return out


def pairwise_word_agreement(txs: dict[str, BenchTranscript]) -> dict:
    """How much of each pair's output is identical. Symmetric, so it is agreement,
    not accuracy — neither one is the reference."""
    names = sorted(txs)
    toks = {n: [t for t, _ in _toks(txs[n])] for n in names}
    matrix = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sm = SequenceMatcher(None, toks[a], toks[b], autojunk=False)
            same = sum(bl.size for bl in sm.get_matching_blocks())
            denom = max(len(toks[a]), len(toks[b])) or 1
            matrix[f"{a}|{b}"] = round(same / denom, 4)
    return {"pairwise_agreement": matrix,
            "word_counts": {n: len(toks[n]) for n in names}}


def outliers(txs: dict[str, BenchTranscript], min_run: int = 1) -> dict:
    """Where exactly one system differs from all the others.

    THE CORE SIGNAL. With three systems, a position where two agree and one differs is
    evidence against the one. Counting those per vendor gives a reference-free quality
    proxy: the vendor that is most often alone is most often wrong.

    Implemented by aligning every system to a PIVOT (the median-length output, so no
    vendor is privileged by being longest or shortest), then comparing at each aligned
    position.
    """
    names = sorted(txs)
    if len(names) < 3:
        return {"note": "needs at least 3 systems to identify an outlier"}
    seqs = {n: _toks(txs[n]) for n in names}
    pivot = sorted(names, key=lambda n: len(seqs[n]))[len(names) // 2]

    # map pivot index -> each vendor's token at that index
    aligned: dict[int, dict[str, str]] = defaultdict(dict)
    pv = [t for t, _ in seqs[pivot]]
    for n in names:
        other = [t for t, _ in seqs[n]]
        sm = SequenceMatcher(None, pv, other, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    aligned[i1 + k][n] = other[j1 + k]
            elif tag == "replace":
                for k in range(min(i2 - i1, j2 - j1)):
                    aligned[i1 + k][n] = other[j1 + k]
            elif tag == "delete":
                for k in range(i2 - i1):
                    aligned[i1 + k][n] = ""          # this vendor omitted it

    counts = Counter()
    examples: list[dict] = []
    disputed = 0
    for idx in sorted(aligned):
        row = aligned[idx]
        if len(row) < len(names):
            continue
        vals = Counter(row.values())
        if len(vals) == 1:
            continue
        disputed += 1
        top, n_top = vals.most_common(1)[0]
        if n_top == len(names) - 1:                  # exactly one dissenter
            odd = next(k for k, v in row.items() if v != top)
            counts[odd] += 1
            if len(examples) < 60:
                examples.append({"at_s": round(seqs[pivot][idx][1], 1),
                                 "consensus": top or "(omitted)",
                                 "outlier": odd,
                                 "said": row[odd] or "(omitted)",
                                 "context": " ".join(pv[max(0, idx - 4):idx + 5])})
    total_aligned = sum(1 for i in aligned if len(aligned[i]) == len(names))
    return {
        "pivot": pivot,
        "aligned_positions": total_aligned,
        "disputed_positions": disputed,
        "dispute_rate": round(disputed / total_aligned, 4) if total_aligned else None,
        "outlier_counts": dict(counts),
        "outlier_rate": {n: round(counts.get(n, 0) / total_aligned, 4)
                         for n in names} if total_aligned else {},
        "examples": examples,
    }


def speaker_structure(txs: dict[str, BenchTranscript], expected: int | None = None) -> dict:
    """Do they agree on HOW MANY people spoke, and WHERE the changes are?

    Speaker count is the cheapest disagreement to read, and for some segments we know
    the answer from Legistar without any audio work — a public-comment block lists every
    registered speaker by name, so `expected` can be a real number rather than a guess.

    Boundary agreement asks whether two systems place speaker changes at the same
    moments (within tolerance), independent of what they call the speakers. That
    isolates segmentation from labelling.
    """
    from pipeline.bench.metrics import coalesce
    names = sorted(txs)
    out = {"speaker_counts": {}, "turn_counts": {}, "expected_speakers": expected}
    bounds = {}
    for n in names:
        c = coalesce(txs[n])
        out["speaker_counts"][n] = len(c.speakers)
        out["turn_counts"][n] = len(c.turns)
        bounds[n] = sorted(t.start for t in c.turns)
    if expected:
        out["speaker_count_error"] = {n: out["speaker_counts"][n] - expected
                                      for n in names}

    tol = 0.75
    agree = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            hit = sum(1 for x in bounds[a]
                      if any(abs(x - y) <= tol for y in bounds[b]))
            denom = max(len(bounds[a]), len(bounds[b])) or 1
            agree[f"{a}|{b}"] = round(hit / denom, 3)
    out["boundary_agreement"] = agree
    out["boundary_tolerance_s"] = tol
    return out


def entity_disagreement(txs: dict[str, BenchTranscript],
                        min_len: int = 4) -> dict:
    """Capitalised-looking tokens the systems render differently.

    THE HIGHEST-VALUE OUTPUT FOR THE VOCABULARY. A proper noun that three independent
    ASR systems each spell differently is, by definition, a word none of them has in its
    lexicon — which is exactly the term that needs to be in the custom vocabulary. This
    is how 'Stults' was found; it should find the Council equivalents automatically.
    """
    names = sorted(txs)
    o = outliers(txs)
    cand = defaultdict(Counter)
    for ex in o.get("examples", []):
        c, s = ex["consensus"], ex["said"]
        if len(c) >= min_len and c.isalpha() and s and s != "(omitted)":
            if SequenceMatcher(None, c, s).ratio() > 0.5:   # a spelling variant
                cand[c][s] += 1
    rows = [{"consensus_spelling": k, "variants": dict(v),
             "systems_disagreeing": len(v)}
            for k, v in sorted(cand.items(), key=lambda kv: -sum(kv[1].values()))]
    return {"candidates": rows[:40],
            "note": "words that independent systems spell differently are vocabulary "
                    "candidates — none of them has the term in its lexicon"}


def analyze(txs: dict[str, BenchTranscript], expected_speakers: int | None = None,
            label: str = "") -> dict:
    return {
        "segment": label,
        "systems": sorted(txs),
        "words": pairwise_word_agreement(txs),
        "outliers": outliers(txs),
        "speakers": speaker_structure(txs, expected_speakers),
        "vocabulary_candidates": entity_disagreement(txs),
    }


def report(a: dict) -> str:
    L = [f"── {a['segment']} " + "─" * max(0, 60 - len(a['segment']))]
    w = a["words"]
    L.append(f"  words transcribed: " +
             ", ".join(f"{k} {v}" for k, v in w["word_counts"].items()))
    L.append("  pairwise word agreement:")
    for k, v in sorted(w["pairwise_agreement"].items(), key=lambda kv: -kv[1]):
        L.append(f"    {k:<28} {v:.3f}")
    o = a["outliers"]
    if "outlier_rate" in o and o["outlier_rate"]:
        L.append(f"  disputed positions: {o['disputed_positions']}/"
                 f"{o['aligned_positions']} ({(o.get('dispute_rate') or 0)*100:.1f}%)")
        L.append("  ODD-ONE-OUT RATE (lower is better — how often each system stands alone):")
        for n, r in sorted(o["outlier_rate"].items(), key=lambda kv: kv[1]):
            L.append(f"    {n:<16} {r*100:5.2f}%   ({o['outlier_counts'].get(n,0)} positions)")
    s = a["speakers"]
    L.append(f"  speakers found: " +
             ", ".join(f"{k} {v}" for k, v in s["speaker_counts"].items()) +
             (f"   (Legistar expects {s['expected_speakers']})"
              if s.get("expected_speakers") else ""))
    L.append("  turn-boundary agreement (±0.75s):")
    for k, v in sorted(s["boundary_agreement"].items(), key=lambda kv: -kv[1]):
        L.append(f"    {k:<28} {v:.3f}")
    vc = a["vocabulary_candidates"]["candidates"][:8]
    if vc:
        L.append("  vocabulary candidates (systems spell these differently):")
        for c in vc:
            L.append(f"    {c['consensus_spelling']:<22} vs "
                     f"{', '.join(c['variants'])}")
    return "\n".join(L)
