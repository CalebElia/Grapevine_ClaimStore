"""S4 — resolve diarization clusters to named people, from text signals.

NO VIDEO. v1 uploaded a 20-minute clip to a multimodal model and asked who was on
screen. It named 18 of 23 clusters, of which FIVE were fabricated: five clusters whose
speakers first talk after minute 20 were confidently labelled `source: "placard"` with
`confidence: 1.0` from footage they never appear in. One of them first speaks at 86:48.
The registry then auto-set `validated: true` because confidence exceeded 0.85.

Everything here comes from the transcript, in descending order of evidence strength:

  1. HUMAN        a person listened and said so. Nothing overrides this.
  2. SELF_ID      "I'm Ken Garber, Ward 2." The speaker names themselves.
  3. ROLL_CALL    the clerk reads the roster and each member answers. This is a
                  labelled enrollment set that regenerates at every meeting, free.
  4. CHAIR_ADDRESS "Commissioner Levin?" immediately before Levin speaks.

Roster-constrained throughout: a name is only accepted if it matches someone on the
body's roster, so the failure mode is "unresolved" rather than "plausible fiction".

Calibration from human review of this meeting: clusters SPEAKER_03 and SPEAKER_04 have
cosine similarity 0.542 and are DIFFERENT people (Maggie Halpern, Erin Donnelly). Any
future embedding merge threshold must therefore sit above 0.542, not below it.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).parent.parent

# Ordered strongest first; a stronger method never loses to a weaker one.
METHOD_RANK = {"human": 0, "self_id": 1, "roll_call": 2, "chair_address": 3}

TITLES = r"(?:Commissioner|Chair|Vice Chair|Council ?Member|Councilmember|Mayor|Director)"
# "Commissioner Levin?" — a name being CALLED, which is what precedes an answer.
CALL_RE = re.compile(rf"\b({TITLES})\s+([A-Z][A-Za-z'\-]+)\s*\?")
# A HANDOFF ends a turn by naming who speaks next, with no question mark:
#   "...I'll turn it over to Council Member Malik."
# Distinct from a roll-call question and easy to miss — it accounts for several of
# the longest unresolved clusters, because substantive turns are handed off this way
# while roll call is the only place people are asked to confirm presence.
HANDOFF_RE = re.compile(rf"\b({TITLES})\s+([A-Z][A-Za-z'\-]+)\s*[.,]?\s*$")
# "I'm Ken Garber" — require two capitalised words so "I'm going to" cannot match.
SELF_RE = re.compile(
    r"\b(?:I'?m|I am|my name is|this is)\s+([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,2})\b")
# A roll-call answer: short, and affirmative.
ANSWER_RE = re.compile(r"\b(here|present|hello|hi|yes)\b", re.I)


@dataclass
class Resolution:
    cluster: str
    name: str
    method: str
    evidence: str
    confidence: float


@dataclass
class Report:
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def offer(self, r: Resolution) -> None:
        """Accept only if this method outranks whatever is already recorded."""
        cur = self.resolutions.get(r.cluster)
        if cur is None or METHOD_RANK[r.method] < METHOD_RANK[cur.method]:
            self.resolutions[r.cluster] = r


# A spoken title constrains WHO can be meant. On this commission "Council Member" is
# only Cornell and Mallek; "Chair" is only Curtis. Ignoring it let "Council Member
# Malik." resolve to Mallika Kothari (a Youth Member) purely on string similarity —
# confident, plausible, and wrong.
TITLE_ROLES = {
    "chair": {"chair"},
    "vice chair": {"vice chair"},
    "council member": {"councilmember"},
    "councilmember": {"councilmember"},
    "commissioner": {"member", "youth member", "chair", "vice chair"},
    "director": set(),
    "mayor": set(),
}


def _match_roster(surname_or_full: str, roster: list[str],
                  title: str | None = None, roles: dict[str, str] | None = None) -> str | None:
    """Map a spoken fragment onto a roster entry. ASR mangles surnames constantly
    ('Katari' for Kothari, 'Nederich' for Nedrich), so match on a normalised prefix
    rather than demanding exactness — but still require a roster hit."""
    frag = re.sub(r"[^a-z]", "", surname_or_full.lower())
    if len(frag) < 4:
        return None
    allowed = None
    if title and roles:
        want = TITLE_ROLES.get(re.sub(r"\s+", " ", title.strip().lower()))
        if want:
            allowed = {n for n, r in roles.items() if (r or "").lower() in want}
    best, best_score = None, 0.0
    for full in roster:
        if allowed is not None and full not in allowed:
            continue
        for part in full.split():
            p = re.sub(r"[^a-z]", "", part.lower())
            if len(p) < 4:
                continue
            # Prefix agreement alone is not enough: ASR corrupts the MIDDLE of names.
            # Observed here — "Malik" for Mallek, "Katari" for Kothari, "Nederich" for
            # Nedrich. Malik/Mallek share only three leading characters and were being
            # rejected, losing a speaker who talks for over a minute.
            ratio = difflib.SequenceMatcher(None, p, frag).ratio()
            n = 0
            while n < min(len(p), len(frag)) and p[n] == frag[n]:
                n += 1
            score = max(ratio, 0.6 + 0.1 * n if n >= 4 else 0.0)
            if ratio >= 0.68 or n >= 4:
                if score > best_score:
                    best, best_score = full, score
    return best


def resolve(turns: list[dict], roster: list[str],
            human: dict[str, str] | None = None,
            roles: dict[str, str] | None = None) -> Report:
    rep = Report()

    # 1. HUMAN — highest authority, applied first so nothing can displace it.
    for cluster, name in (human or {}).items():
        rep.offer(Resolution(cluster, name, "human", "confirmed by listening", 1.0))

    # 2. SELF_ID
    for t in turns:
        for m in SELF_RE.finditer(t["text"]):
            cand = m.group(1).strip()
            hit = _match_roster(cand.split()[-1], roster) or _match_roster(cand, roster)
            if hit:
                rep.offer(Resolution(t["speaker"], hit, "self_id",
                                     f'"{m.group(0)}" @ {int(t["start"])}s', 0.95))
            else:
                # Public commenters are not on the roster. Keep the name — they are
                # real speakers — but mark them so they never enter the voiceprint
                # registry, which is gated on is_public_figure.
                if len(cand.split()) >= 2:
                    rep.offer(Resolution(t["speaker"], cand, "self_id",
                                         f'"{m.group(0)}" @ {int(t["start"])}s (not on roster)',
                                         0.9))

    # 3/4. ROLL_CALL and CHAIR_ADDRESS — a name is called, the next DIFFERENT speaker
    # answers. Roll call is distinguished by the answer being short and affirmative.
    for i, t in enumerate(turns):
        calls = CALL_RE.findall(t["text"])
        handoff = HANDOFF_RE.search(t["text"].strip())
        if not calls and handoff:
            hit = _match_roster(handoff.group(2), roster, handoff.group(1), roles)
            if hit:
                for nxt in turns[i + 1:i + 2]:
                    if nxt["speaker"] != t["speaker"] and nxt["end"] - nxt["start"] > 8:
                        rep.offer(Resolution(
                            nxt["speaker"], hit, "chair_address",
                            f'handed off to "{handoff.group(1)} {handoff.group(2)}" @ {int(t["end"])}s', 0.7))
            continue
        if not calls:
            continue
        # Only the LAST name called in a turn can be answered by the next speaker;
        # earlier ones were answered inside this same turn (the clerk reads on).
        title, called = calls[-1]
        hit = _match_roster(called, roster, title, roles)
        if not hit:
            rep.rejected.append(f'called "{title} {called}" @ {int(t["start"])}s — no roster match')
            continue
        for nxt in turns[i + 1:i + 3]:
            if nxt["speaker"] == t["speaker"]:
                continue
            if nxt["start"] - t["end"] > 6:
                break
            txt = nxt["text"].strip()
            short = len(txt) < 60
            if short and ANSWER_RE.search(txt):
                rep.offer(Resolution(nxt["speaker"], hit, "roll_call",
                                     f'called "{called}" -> "{txt[:40]}"', 0.85))
            elif short:
                rep.offer(Resolution(nxt["speaker"], hit, "chair_address",
                                     f'called "{called}" -> "{txt[:40]}"', 0.6))
            break

    clusters = {t["speaker"] for t in turns}
    rep.unresolved = sorted(clusters - set(rep.resolutions))
    return rep


def load_roles(body_slug: str = "ann_arbor_sustainability_commission") -> dict[str, str]:
    """name -> role, so a spoken title can constrain the candidate set."""
    p = REPO.parent / "video_analysis" / "registries" / body_slug / "body_registry.json"
    if not p.exists():
        return {}
    return {m["name"]: m.get("role", "") for m in json.loads(p.read_text()).get("current_roster", [])}


def load_roster(body_slug: str = "ann_arbor_sustainability_commission") -> list[str]:
    """Roster from the v1 registry if present, else the DB."""
    p = REPO.parent / "video_analysis" / "registries" / body_slug / "body_registry.json"
    if p.exists():
        return [m["name"] for m in json.loads(p.read_text()).get("current_roster", [])]
    return []
