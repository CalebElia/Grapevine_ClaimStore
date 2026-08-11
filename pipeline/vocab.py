"""Per-meeting ASR keyterm list, assembled from what we already know about the meeting.

THE FINDING THIS EXISTS FOR. Cross-vendor disagreement on the 4/20/26 Council meeting
surfaced `dish`/`disch`, `redina`/`rudina`, `juskevich`/`jaskiewicz`, `malik`/`malek`,
`courtland`/`cortland`, `landy`/`lande`. Every one of those is a person whose correct
spelling was already sitting in Legistar before a second of audio was processed — Lisa
Disch, Travis Radina, Adam Jaskiewicz, Jon Mallek, Cortland Bersee, Lynne Lande.

And in two of those cases the MAJORITY SPELLING WAS WRONG: consensus chose `dish` over
`disch`, and no system produced `Radina` at all. So the rule is narrow and important —
disagreement tells you WHICH terms need an entry; the registry tells you WHAT they should
be. Never let the models vote on a name.

WHY A KEYTERM LIST AND NOT A PROMPT. Whisper's `initial_prompt` is prepended as if it
were prior transcript, so the decoder can continue it — which it did, destroying 62 words
of Missy Stults at 23:19 of the 3/10 meeting and replacing them with the prompt's own
sentence. Every vendor we now use takes custom vocabulary as a LIST of terms. A list is
not continuable prose and cannot fail that way.

Sources, in descending reliability:
  1. the meeting's own Legistar agenda — member names, registered public commenters,
     proclamation subjects. Exact, and specific to this meeting.
  2. the body's roster — everyone who might answer roll call.
  3. the jurisdiction registry — hand-curated domain terms (A2Zero, ARCA, SEU).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).parent.parent
DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Words that look like names on an agenda line but are not worth boosting, plus the
# procedural furniture every agenda carries.
STOP = {"resolution", "approve", "agreement", "city", "council", "commission", "meeting",
        "report", "update", "presentation", "memorandum", "proclamation", "ordinance",
        "public", "comment", "commentary", "minutes", "agenda", "consent", "hearing",
        "communications", "appointments", "confirmation", "introductions", "roll", "call"}


def _people_from_agenda(conn, event_id: int) -> list[str]:
    """Registered public commenters are listed as 'Name - Topic (in person)'.

    These are the highest-value terms in the whole list: a public commenter is not on any
    roster, appears in exactly one meeting, and is therefore in no ASR lexicon anywhere.
    """
    rows = conn.execute(
        "SELECT title FROM event_items WHERE event_id=%s ORDER BY sequence",
        (event_id,)).fetchall()
    out = []
    for (t,) in rows:
        t = re.sub(r"[\r\n]+", " ", t or "").strip()
        m = re.match(r"^([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})\s+-\s+\S", t)
        if m:
            name = m.group(1).strip()
            if not {w.lower() for w in name.split()} & STOP:
                out.append(name)
    return out


def _proper_nouns_from_agenda(conn, event_id: int) -> list[str]:
    """Multi-word capitalised phrases — org names, program names, proclamation subjects.
    'Therapaws of Michigan' came out of exactly this and was mangled by all three vendors.
    """
    rows = conn.execute("SELECT title FROM event_items WHERE event_id=%s", (event_id,)).fetchall()
    seen = {}
    for (t,) in rows:
        t = re.sub(r"[\r\n]+", " ", t or "")
        for m in re.finditer(r"\b([A-Z][A-Za-z]{3,}(?:\s+(?:of|the|and)?\s*[A-Z][A-Za-z]{3,}){1,3})\b", t):
            p = m.group(1).strip()
            words = {w.lower() for w in p.split()}
            if words & STOP or len(p) > 44:
                continue
            seen[p] = seen.get(p, 0) + 1
    return sorted(seen, key=lambda k: -seen[k])[:25]


def build(event_id: int | None = None, jurisdiction: str = "ann_arbor",
          body_slug: str = "ann_arbor_sustainability_commission",
          dsn: str = DSN, limit: int = 100) -> dict:
    terms: list[str] = []
    prov: dict[str, list[str]] = {}

    reg = REPO / "registries" / jurisdiction / "asr_vocabulary.json"
    if reg.exists():
        v = json.loads(reg.read_text())
        curated = []
        for k in ("programs_and_entities", "utilities_and_regulators", "technical_terms",
                  "people", "agenda_and_procedure"):
            curated += [t for t in v.get(k, []) if isinstance(t, str)]
        prov["registry"] = curated
        terms += curated

    from pipeline.speakers import load_roster
    roster = load_roster(body_slug)
    if roster:
        prov["roster"] = roster
        terms += roster

    if event_id is not None:
        try:
            import psycopg
            with psycopg.connect(dsn) as conn:
                people = _people_from_agenda(conn, event_id)
                nouns = _proper_nouns_from_agenda(conn, event_id)
            if people:
                prov["agenda_speakers"] = people
                terms += people
            if nouns:
                prov["agenda_proper_nouns"] = nouns
                terms += nouns
        except Exception as exc:                       # DB down should not block a run
            prov["_agenda_error"] = [f"{type(exc).__name__}: {exc}"]

    # De-duplicate case-insensitively, keeping first-seen casing — the registry and the
    # roster are the authoritative spellings and they are added first.
    #
    # VENDOR LIMITS, enforced here rather than discovered at request time: ElevenLabs
    # caps a keyterm at 50 characters and 5 words, and rejects < > { } [ ] \ outright.
    # A term that violates any of these fails the WHOLE request, so one bad agenda line
    # would cost a 113-minute transcription run.
    out, seen, dropped = [], set(), []
    for t in terms:
        t = (t or "").strip()
        if not t or len(t) < 3 or t.lower() in seen:
            continue
        if len(t) >= 50 or len(t.split()) > 5 or any(c in t for c in "<>{}[]\\"):
            dropped.append(t)
            continue
        seen.add(t.lower())
        out.append(t)
    if dropped:
        prov["_dropped_vendor_limits"] = dropped
    return {"terms": out[:limit], "sources": prov, "dropped": dropped,
            "event_id": event_id, "jurisdiction": jurisdiction, "body": body_slug,
            "_note": "keyterm LIST, never a prose prompt — see module docstring"}


def main() -> int:
    ap = argparse.ArgumentParser(description="build a per-meeting ASR keyterm list")
    ap.add_argument("--event", type=int)
    ap.add_argument("--body", default="ann_arbor_sustainability_commission")
    ap.add_argument("--out")
    a = ap.parse_args()
    v = build(a.event, body_slug=a.body)
    print(f"[vocab] {len(v['terms'])} terms")
    for src, items in v["sources"].items():
        print(f"  {src:<22} {len(items)}")
    if a.out:
        Path(a.out).write_text(json.dumps(v, indent=2))
        print(f"[vocab] wrote {a.out}")
    else:
        print("  " + ", ".join(v["terms"][:24]) + " ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
