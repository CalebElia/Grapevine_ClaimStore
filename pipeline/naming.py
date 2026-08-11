"""S2b — names and turn boundaries, from Gemini, transferred deterministically.

WHAT THIS IS FOR. Scribe gives words, timestamps and a speaker label per word, but its
labels are anonymous (`c03_speaker_1`) and — because we chunk — meaningless across chunk
boundaries. Gemini gives names and semantically-derived turn boundaries but no usable
timestamps. Each supplies exactly what the other lacks.

THE JOIN IS ARITHMETIC, NOT A MODEL CALL. Both systems transcribed the same audio, so
their word streams agree ~87-94%. Align them; every matched position casts one vote
linking a Scribe cluster to a Gemini name. That makes the transfer inspectable and
reproducible, which a second LLM call would not be.

WHY GEMINI RATHER THAN MORE ACOUSTICS. Roll call is a LINGUISTIC pattern — the clerk
calls a name, the next voice is that person — and no acoustic system can see it. Measured
on the 3/10 meeting: Scribe's word-level speaker was wrong for Mazloomian, Nedrich and
Smyth (all filed under the clerk), and Gemini corrected all three. It also split both
welds a human reviewer found by ear. Speaker embeddings are weakest exactly here, since
roll-call answers run 1-2s and embeddings need ~1.5s to mean anything.

PURITY IS THE CONFIDENCE SIGNAL. Of the words in one cluster that aligned to a name, the
fraction sharing the majority name. A real person scores 0.9-1.0; the junk cluster a
reviewer rejected by ear scored 0.50 without being told anything.

THE REGISTRY SPELLS. Gemini writes Kathari, Malik, Mazlumian, Nadrich, Smith. Every one
resolves against the roster, constrained by spoken title — without that constraint
"Council Member Malik" matches Mallika Kothari, a Youth Member, on string similarity.
Names are PROPOSALS carrying evidence, never facts. A human confirms.

Usage:
    python -m pipeline.naming lWvRVUMyLP4
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent
FILLER = {"uh", "um", "mm", "hmm", "mhm", "ah", "er"}
MIN_PURITY = 0.60          # below this the cluster is probably not one person

# ATTRIBUTION RISK IS NOT NAME CONFIDENCE, and conflating them cost a review pass.
# `name_tier` says whether a NAME is real and verified. It says nothing about whether
# THIS TURN belongs to that person. Measured against a human pass over 89 turns:
#
#   tier=roster (verified name)   15% of turns had the WRONG person
#   tier=proposed/raw             ~2%
#
# Every one of the roster errors was a short procedural turn — 'So moved.', 'Second.',
# 'Council Member' — median 0.5s and 2 words. 16 of 21 name corrections landed on turns
# of 4 words or fewer. Short turns carry almost no acoustic or semantic evidence, so
# whoever spoke last tends to win them regardless of how well the name resolved.
SHORT_WORDS = 4            # at or below this, attribution is a coin-flip
SHORT_SECONDS = 1.5
MEDIUM_WORDS = 10

# An unnamed run this small is alignment residue, not a speaker. Absorb it into whichever
# neighbour is temporally closer. Left standing they became 120 micro-turns — median 0.6s,
# things like 'Uh,' and 'Kothari?' — and 17 of 22 red-tier rows a reviewer touched were
# these. A LONG unnamed run is different: it is a real speaker nobody named, and must be
# kept so it can be flagged rather than quietly attached to someone.
FRAGMENT_WORDS = 3
FRAGMENT_SECONDS = 2.0
TITLES = r"^(?:Dr\.|Commissioner|Chair|Vice Chair|Council ?Member|Councilmember|Mayor|Director)\s+"

PROMPT = """Transcribe this audio with speaker diarization.
- Identify each speaker by name when it can be determined from introductions, roll call,
  or how others address them. Otherwise use "Speaker 1", "Speaker 2".
- Output ONLY lines of the form:  Speaker Name: their words
- One line per speaker turn. Preserve every word verbatim. Do not summarise or omit.
- These proper nouns appear and must be spelled exactly: {terms}
"""


def _toks(s: str) -> list[str]:
    return [x for x in re.sub(r"[^a-z0-9' ]", " ", (s or "").lower()).split()
            if x not in FILLER]


def gemini_chunk(clip: Path, terms: list[str], model: str | None = None) -> list[tuple]:
    """One Gemini pass over one chunk. Retries across models — 503 'high demand' is
    common enough that a single attempt loses whole runs."""
    from google import genai
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    c = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    up = c.files.upload(file=str(clip))
    txt = None
    for m in (model, "gemini-2.5-flash"):
        for attempt in range(3):
            try:
                txt = c.models.generate_content(
                    model=m, contents=[up, PROMPT.format(terms=", ".join(terms[:60]))]).text
                break
            except Exception as exc:
                print(f"      {m} attempt {attempt+1}: "
                      f"{getattr(exc, 'code', None) or type(exc).__name__}")
                time.sleep(5 * (attempt + 1))
        if txt:
            break
    try:
        c.files.delete(name=up.name)
    except Exception:
        pass
    if not txt:
        return []
    out = []
    for line in txt.splitlines():
        m = re.match(r"^\s*([^:]{1,60}?):\s*(.+)$", line)
        if m:
            out.append((m.group(1).strip(), m.group(2)))
    return out


def transfer(words: list[dict], turns: list[tuple]) -> dict[int, str]:
    """Word-index -> Gemini speaker label, by sequence alignment."""
    sw = [_toks(w["text"]) for w in words]
    sflat = [t for g in sw for t in g]
    smap = [i for i, g in enumerate(sw) for _ in g]
    gflat, gspk = [], []
    for name, text in turns:
        for t in _toks(text):
            gflat.append(t)
            gspk.append(name)
    at: dict[int, str] = {}
    for b in difflib.SequenceMatcher(None, sflat, gflat, autojunk=False).get_matching_blocks():
        for k in range(b.size):
            at[smap[b.a + k]] = gspk[b.b + k]
    # GAP FILL: an unaligned word inherits its neighbours' label when they agree. Without
    # this the ~13% of words that fail to align shatter otherwise-correct turns.
    for i in range(len(words)):
        if i in at:
            continue
        prev = next((at[j] for j in range(i - 1, -1, -1) if j in at), None)
        nxt = next((at[j] for j in range(i + 1, len(words)) if j in at), None)
        if prev and prev == nxt:
            at[i] = prev
    return at


def load_present(event_id: int | None, body_slug: str,
                 dsn: str | None = None) -> set[str]:
    """Names with EVIDENCE OF PRESENCE at this meeting — agenda mentions and members
    of this body. Not the same thing as existing in the person table."""
    names: set[str] = set()
    if event_id is None:
        return names
    try:
        import psycopg
        dsn = dsn or os.environ.get(
            "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine")
        with psycopg.connect(dsn, connect_timeout=5) as c:
            rows = c.execute("SELECT title FROM event_items WHERE event_id=%s",
                             (event_id,)).fetchall()
            blob = " ".join((r[0] or "") for r in rows)
            for (pid, full) in c.execute("SELECT id, full_name FROM persons").fetchall():
                if full and len(full) > 4 and full.split()[-1] in blob:
                    names.add(full)
            for (full,) in c.execute(
                    "SELECT p.full_name FROM memberships m JOIN persons p ON p.id=m.person_id "
                    "JOIN bodies b ON b.id=m.body_id WHERE b.name ILIKE %s",
                    (f"%{body_slug.split('_')[-1]}%",)).fetchall():
                names.add(full)
    except Exception:
        pass
    return names


def load_persons(dsn: str | None = None) -> list[tuple[int, str]]:
    """Everyone Legistar knows about. Wider than any single body's roster."""
    try:
        import psycopg
        dsn = dsn or os.environ.get(
            "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine")
        with psycopg.connect(dsn, connect_timeout=5) as c:
            return [(r[0], r[1]) for r in
                    c.execute("SELECT id, full_name FROM persons").fetchall()]
    except Exception:
        return []


def resolve(label: str, roster: list[str], roles: dict,
            persons: list[tuple[int, str]] | None = None,
            present: set[str] | None = None) -> tuple[str | None, str, str]:
    """Gemini label -> (canonical name, bare label, tier).

    LAYERED, because a commission roster is the wrong authority for most of the speech.
    Measured on this meeting: 71% of speech was correctly NAMED by Gemini and then
    discarded, because Missy Stults (staff, 37% of all speech on her own), Erin Donnelly
    (clerk), Ken Garber (public commenter) and Steve Harvey (DTE) are not commission
    MEMBERS. Throwing away a right answer because it came from the wrong table is worse
    than having no table.

      roster    a member of this body, title-constrained
      persons   in the Legistar record AND with evidence of presence at this meeting
      proposed  surname + first initial only; surfaced, never auto-accepted
      raw       nobody matched, but Gemini produced a plausible name — KEEP IT, flagged
                unverified, so the reviewer confirms rather than types

    The title constraint on the roster tier is load-bearing: without it 'Council Member
    Malik' resolves to Mallika Kothari, a Youth Member, on string similarity alone —
    confident, plausible and wrong. Verified failure, twice.
    """
    from pipeline.speakers import _match_roster
    from pipeline.import_review import name_key, norm_name
    if not label or re.match(r"Speaker \d+$", label):
        return None, label, "none"
    m = re.match(TITLES, label)
    title = m.group(0).strip().rstrip(".") if m else None
    if title == "Dr":
        title = None                       # a doctorate is not a role on any body
    bare = re.sub(TITLES, "", label).strip()

    hit = (_match_roster(bare, roster, title, roles) or
           _match_roster(bare.split()[-1], roster, title, roles))
    if hit:
        return hit, bare, "roster"

    # A `persons` hit only counts as VERIFIED when there is independent evidence the
    # person was at this meeting — named on the agenda, or a member of this body.
    # Without that gate, a 1,771-row table confirms almost any plausible name and
    # launders a model's guess into a verified attribution. Measured: Gemini said
    # 'Erica Briggs' and 'Lisa Disch' — both real Council members, neither on this
    # commission, both wrong — and both came back verified.
    present = present or set()
    for _, full in (persons or []):
        if norm_name(full) == norm_name(bare):
            return full, bare, ("persons" if full in present else "proposed")
    k = name_key(bare)
    if k:
        cand = [f for _, f in (persons or []) if name_key(f) == k]
        if len(cand) == 1:
            return cand[0], bare, ("persons" if cand[0] in present else "proposed")
        weak = [f for _, f in (persons or [])
                if name_key(f) and name_key(f)[1] == k[1]
                and name_key(f)[0][:1] == k[0][:1]]
        if len(weak) == 1:
            return weak[0], bare, "proposed"
    # Nobody matched. The NAME IS STILL EVIDENCE — keep it, mark it unverified.
    return (bare if len(bare) > 2 else None), bare, "raw"


def run(video_id: str, body_slug: str = "ann_arbor_sustainability_commission",
        out_dir: Path | None = None) -> dict:
    from pipeline.speakers import load_roster, load_roles
    from pipeline import vocab as V
    d = out_dir or REPO / "processing" / video_id
    src = json.loads((d / "scribe.json").read_text())
    words, prov = src["words"], src.get("_provenance", {})
    chunk_s = prov.get("chunk_seconds", 480.0)
    terms = prov.get("keyterms") or V.build(None, body_slug=body_slug)["terms"]
    roster, roles = load_roster(body_slug), load_roles(body_slug)
    persons = load_persons()
    present = load_present(prov.get("event_id"), body_slug)
    print(f"[S2b] authorities: {len(roster)} roster, {len(persons)} persons, "
          f"{len(present)} with presence evidence for this meeting")

    n_chunks = prov.get("chunks") or 1
    label_at: dict[int, str] = {}
    covered = 0
    for i in range(n_chunks):
        clip = d / f"chunk_{i:03d}.mp3"
        if not clip.exists():
            continue
        lo, hi = i * chunk_s, (i + 1) * chunk_s
        idx = [k for k, w in enumerate(words) if lo <= w["start"] < hi]
        if not idx:
            continue
        # Cache Gemini per chunk. The transcription is the expensive, non-deterministic
        # part; resolution logic will change many times and must not re-bill or
        # re-randomise it. Delete the cache dir to force a fresh pass.
        cache = d / "gemini" / f"chunk_{i:03d}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            turns = [tuple(x) for x in json.loads(cache.read_text())]
            print(f"  chunk {i+1}/{n_chunks}  {lo/60:5.1f} min  {len(idx):5d} words  "
                  f"(cached)", flush=True)
        else:
            print(f"  chunk {i+1}/{n_chunks}  {lo/60:5.1f} min  {len(idx):5d} words ...",
                  flush=True)
            turns = gemini_chunk(clip, terms)
            if turns:
                cache.write_text(json.dumps(turns))
        if not turns:
            print("      no Gemini output — chunk left unnamed")
            continue
        sub = [words[k] for k in idx]
        for local, lab in transfer(sub, turns).items():
            label_at[idx[local]] = lab
        covered += sum(1 for k in idx if k in label_at)

    # Vote each Scribe cluster to a Gemini label, and measure purity.
    votes: dict[str, Counter] = defaultdict(Counter)
    for i, w in enumerate(words):
        if i in label_at:
            votes[w["speaker"]][label_at[i]] += 1
    clusters = {}
    for cl, c in votes.items():
        top, n = c.most_common(1)[0]
        tot = sum(c.values())
        hit, bare, tier = resolve(top, roster, roles, persons, present)
        clusters[cl] = {"gemini_label": top, "resolved": hit, "bare": bare, "tier": tier,
                        "votes": n, "aligned_words": tot,
                        "purity": round(n / tot, 3) if tot else 0.0,
                        "low_purity": (n / tot if tot else 0) < MIN_PURITY}

    # ABSORB FRAGMENTS. An unlabelled word bounded by labelled ones, in a run short
    # enough to be residue, joins the temporally nearer side. Recorded, never silent.
    absorbed = orphans = 0
    i = 0
    while i < len(words):
        if i in label_at:
            i += 1
            continue
        j = i
        while j < len(words) and j not in label_at:
            j += 1
        run = words[i:j]
        span = (run[-1]["end"] - run[0]["start"]) if run else 0
        prev_lab = label_at.get(i - 1)
        next_lab = label_at.get(j)
        if (run and len(run) <= FRAGMENT_WORDS and span <= FRAGMENT_SECONDS
                and prev_lab and next_lab):
            # ABSORB ONLY INTO A NEIGHBOUR SCRIBE AGREES WITH. Temporal proximity alone
            # is not enough: 'We\'re... Sorry.' at 1:12 is Christopher Graham unmuting,
            # and the nearer neighbour was the clerk — so proximity buried a wrong
            # speaker inside a long correct turn and marked it low risk, which is worse
            # than leaving it exposed. Scribe's word-level cluster is exactly the
            # short-range speaker-change evidence this needs, and it was going unused.
            frag_cl = {w["speaker"] for w in run}
            prev_cl, next_cl = words[i - 1]["speaker"], words[j]["speaker"]
            gap_prev = run[0]["start"] - words[i - 1]["end"]
            gap_next = words[j]["start"] - run[-1]["end"]
            cand = []
            if frag_cl == {prev_cl}:
                cand.append((gap_prev, prev_lab))
            if frag_cl == {next_cl}:
                cand.append((gap_next, next_lab))
            if cand:
                for k in range(i, j):
                    label_at[k] = min(cand)[1]
                absorbed += len(run)
            else:
                orphans += len(run)      # different voice from both sides — leave exposed
        i = j
    if absorbed or orphans:
        print(f"[S2b] absorbed {absorbed} unlabelled words into adjacent turns; "
              f"{orphans} left exposed (Scribe puts them in a different voice cluster "
              f"than either neighbour)")

    # Build turns from GEMINI's boundaries with SCRIBE's words and timestamps.
    turns_out = []
    for i, w in enumerate(words):
        lab = label_at.get(i)
        if turns_out and turns_out[-1]["_lab"] == lab:
            t = turns_out[-1]
            t["end"] = w["end"]
            t["text"] += (" " if t["text"] else "") + w["text"]
            t["clusters"].add(w["speaker"])
        else:
            turns_out.append({"_lab": lab, "start": w["start"], "end": w["end"],
                              "text": w["text"], "clusters": {w["speaker"]}})
    out_turns = []
    for t in turns_out:
        hit, bare, tier = (resolve(t["_lab"], roster, roles, persons, present)
                           if t["_lab"] else (None, None, "none"))
        cls = sorted(t["clusters"])
        pur = min((clusters[c]["purity"] for c in cls if c in clusters), default=None)
        nwords = len(t["text"].split())
        dur = t["end"] - t["start"]
        orphan = not t["_lab"]
        if orphan or nwords <= SHORT_WORDS or dur <= SHORT_SECONDS:
            risk = "high"
        elif nwords <= MEDIUM_WORDS or (pur is not None and pur < 0.8) or len(cls) > 1:
            risk = "medium"
        else:
            risk = "low"
        out_turns.append({
            "seq": len(out_turns),
            "attribution_risk": risk, "words": nwords,
            "start": round(t["start"], 3), "end": round(t["end"], 3),
            "text": t["text"],
            "key": f"vid_yt_{video_id}:{len(out_turns):05d}:"
                   f"{int(t['start']*1000)}-{int(t['end']*1000)}",
            "speaker_name": hit, "name_tier": tier,
            "verified_source": tier in ("roster", "persons"),
            "gemini_label": t["_lab"], "unresolved_label": bare,
            "clusters": cls, "min_purity": pur,
            "flags": ([] if tier in ("roster", "persons")
                      else (["unnamed"] if not hit else [f"name_{tier}"]))
                     + (["low_purity"] if pur is not None and pur < MIN_PURITY else [])
                     + (["multi_cluster"] if len(cls) > 1 else []),
        })

    named = sum(t["end"] - t["start"] for t in out_turns if t["speaker_name"])
    verified = sum(t["end"] - t["start"] for t in out_turns if t["verified_source"])
    risk_s = defaultdict(float)
    for t in out_turns:
        risk_s[t["attribution_risk"]] += t["end"] - t["start"]
    total = sum(t["end"] - t["start"] for t in out_turns) or 1
    people = sorted({t["speaker_name"] for t in out_turns if t["speaker_name"]})
    print(f"\n[S2b] {len(out_turns)} turns | {covered}/{len(words)} words named "
          f"({covered/len(words)*100:.0f}%)")
    print(f"[S2b] {len(clusters)} per-chunk clusters -> {len(people)} distinct people")
    print(f"[S2b] {named/total*100:.0f}% of speech carries a name "
          f"({verified/total*100:.0f}% verified against roster/persons, "
          f"{(named-verified)/total*100:.0f}% unverified — reviewer confirms)")
    print(f"[S2b] people: {', '.join(people)}")
    tot_s = sum(risk_s.values()) or 1
    print(f"[S2b] attribution risk by MINUTES OF AUDIO — the number that decides how "
          f"much a reviewer must watch:")
    for k in ("low", "medium", "high"):
        print(f"        {k:<7}{risk_s[k]/60:6.1f} min{risk_s[k]/tot_s*100:5.0f}%")
    low = [c for c, v in clusters.items() if v["low_purity"]]
    if low:
        print(f"[S2b] {len(low)} cluster(s) below purity {MIN_PURITY} — probably not "
              f"one person each: {', '.join(sorted(low)[:8])}")

    payload = {"video_id": video_id, "turns": out_turns, "clusters": clusters,
               "_provenance": {**prov, "naming": "gemini + deterministic alignment",
                               "registry": body_slug,
                               "words_named_pct": round(covered / len(words) * 100, 1),
                               "speech_named_pct": round(named / total * 100, 1),
                               "distinct_people": len(people)}}
    out = d / "named.json"
    out.write_text(json.dumps(payload))
    print(f"[S2b] wrote {out}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="S2b names + boundaries via Gemini")
    ap.add_argument("video_id")
    ap.add_argument("--body", default="ann_arbor_sustainability_commission")
    a = ap.parse_args()
    import pipeline.config as cfg
    cfg.load_dotenv()
    run(a.video_id, a.body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
