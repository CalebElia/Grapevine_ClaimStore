"""Link a document's named efforts to the initiative subjects that already exist.

WHAT THIS IS FOR. 845 CAP claims and 903 annual-report claims can say what was said and when,
and cannot be grouped by WHAT THEY ARE ABOUT below the level of a strategy. The CAP names 44
Actions and 276 considered ideas; the wiki has already curated 229 initiatives as subjects.
Nothing joins the two, so "how has district geothermal progressed" has no query -- even though
the store now holds both `Geothermal districts` (an idea the 2020 plan declined) and `won
$10,000,000 to support the deployment of a networked geothermal system in the Bryant
neighborhood` (what it became).

IT CREATES NOTHING. Every subject it links to was seeded by a person from the wiki, because
`subjects.created_by` is NOT NULL and human by design -- "a cluster ID never becomes a
canonical key". An effort with no matching subject is a FINDING, not a licence to mint one:
either the wiki is missing it or the name has changed, and both want a human.

ONE CANDIDATE OR NONE, which is resolve_orgs' rule and for its reason: choosing between two
is not string matching's job. A heading that matches three initiatives is queued, not guessed.

TWO SHAPES, ONE MATCHER. An Action is a SECTION -- "Implement Community Choice Aggregation" --
and its whole section belongs to one initiative. An idea is a CLAIM -- "Geothermal districts"
-- and its section holds dozens of unrelated ones, so the link has to be per claim. The
matching is identical; only what carries the result differs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Verbs the CAP puts in front of an Action that the wiki's initiative names drop:
# "Implement Community Choice Aggregation" against "Community Choice Aggregation". Removing
# them is what takes matching from 3 of 50 to 40 of 50. They are stripped from BOTH sides, so
# an initiative genuinely named "Launch X" still matches a heading "Launch X".
_LEAD_VERBS = {
    "implement", "develop", "launch", "promote", "expand", "support", "increase",
    "establish", "enhance", "preserve", "conduct", "assist", "transition", "electrify",
    "invest", "move", "require", "foster", "update", "benchmark", "power", "reduce",
    "offset", "advance", "create", "change", "improve", "build", "deploy", "adopt",
    "encourage", "incentivize", "expedite", "allow", "set", "pair", "subsidize",
}
# Function words carry no identity.
_STOP = {"the", "a", "an", "of", "and", "or", "in", "to", "with", "our", "for", "toward",
         "towards", "more", "at", "on", "by", "from", "into", "new", "all", "we", "us",
         "its", "their", "this", "that", "as", "is", "are", "be"}


def slugify(text: str) -> str:
    """Lowercase, ampersand spelled out, everything else hyphenated. Matches wiki slugs."""
    t = (text or "").replace("&", " and ").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t)).strip("-")


def key_terms(text: str) -> frozenset[str]:
    """The words that carry identity: no lead verbs, no function words, no bare numbers.

    A number is dropped because "Strategy 1" and "Strategy 2" would otherwise differ only by
    a token that means something completely different from a name.
    """
    return frozenset(w for w in slugify(text).split("-")
                     if w and w not in _STOP and w not in _LEAD_VERBS and not w.isdigit())


def candidates(text: str, subjects: list[tuple[int, str, str | None]]) -> list[int]:
    """Subject ids this text could name. Empty or many are both normal answers.

    `subjects` is (id, name, wiki_slug). Three passes, strongest first, and the first pass
    that yields anything wins -- a slug that matches exactly is not made ambiguous by a
    keyword pass that would also have matched four other things.
    """
    if not (t := (text or "").strip()):
        return []
    ts, tk = slugify(t), key_terms(t)

    exact, tail, keyword = [], [], []
    for sid, name, slug in subjects:
        stail = (slug or "").rsplit("/", 1)[-1]
        if ts and (ts == slugify(name) or ts == stail):
            exact.append(sid)
            continue
        # ALTERNATIVES, NOT A UNION. The name and the slug are two spellings of one thing,
        # and unioning them makes the subject's term set BIGGER -- which makes both equality
        # and containment harder, exactly backwards. "Sustaining Ann Arbor Together Grant
        # Program" has slug ...-grants, so the union gained a plural `grants` the heading
        # could never contain and the match was lost.
        forms = [k for k in (key_terms(name), key_terms(stail)) if k]
        if tk and any(tk == nk for nk in forms):
            tail.append(sid)
            continue
        # Containment: "Implement Community Choice Aggregation" holds every identity word of
        # "Community Choice Aggregation". Requires at least two, so a single shared word like
        # "energy" cannot carry a match on its own.
        if tk and any(len(nk) >= 2 and nk <= tk for nk in forms):
            keyword.append(sid)

    for tier in (exact, tail, keyword):
        if tier:
            return sorted(set(tier))
    return []


def resolve(rows: list[tuple[int, str]],
            subjects: list[tuple[int, str, str | None]]) -> tuple[dict, list[dict]]:
    """(row_id -> subject_id, queue). The queue holds everything not resolved, with why.

    Pure: no database, no side effects, so the matching can be tested and tuned against real
    headings without a server.
    """
    linked: dict[int, int] = {}
    queue: list[dict] = []
    for row_id, text in rows:
        c = candidates(text, subjects)
        if len(c) == 1:
            linked[row_id] = c[0]
        else:
            names = {sid: n for sid, n, _ in subjects}
            queue.append({"id": row_id, "text": text,
                          "reason": "no candidate" if not c else "ambiguous",
                          "candidates": [{"subject_id": s, "name": names[s]} for s in c]})
    return linked, queue


# ── the two shapes ────────────────────────────────────────────────────────────────────────

_SECTIONS = """
    SELECT s.id, s.heading FROM document_sections s
     WHERE s.document_id = %s AND s.subject_id IS NULL AND s.heading IS NOT NULL
       AND (s.section_topic IS NULL OR s.section_topic <> 'ideas_considered')
       AND EXISTS (SELECT 1 FROM claims WHERE document_section_id = s.id)
     ORDER BY s.sequence"""

# An idea is a CLAIM, because its section holds dozens of unrelated ones.
_IDEA_CLAIMS = """
    SELECT cl.id, cl.verbatim FROM claims cl
      JOIN document_sections s ON s.id = cl.document_section_id
     WHERE s.document_id = %s AND s.section_topic = 'ideas_considered'
       AND cl.subject_id IS NULL
     ORDER BY cl.id"""


def run(document_id: int, dsn: str = DSN, dry_run: bool = False,
        queue_path: Path | None = None) -> dict:
    """Resolve one document's sections and idea-claims. Returns counts."""
    import psycopg

    counts = {"sections_linked": 0, "section_claims": 0, "ideas_linked": 0,
              "queued_sections": 0, "queued_ideas": 0, "ambiguous": 0}
    queue: list[dict] = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        # live_subjects excludes merged duplicates, which would otherwise be offered as
        # candidates and make a resolvable heading look ambiguous. See migrations/024.
        cur.execute("SELECT id, name, wiki_slug FROM live_subjects "
                    "WHERE subject_kind='initiative'")
        subs = cur.fetchall()

        cur.execute(_SECTIONS, (document_id,))
        linked, q = resolve(cur.fetchall(), subs)
        counts["sections_linked"] = len(linked)
        counts["queued_sections"] = len(q)
        queue += [{**x, "kind": "section"} for x in q]
        if not dry_run:
            for sec_id, sid in linked.items():
                cur.execute("UPDATE document_sections SET subject_id=%s WHERE id=%s",
                            (sid, sec_id))
                # INHERIT, NEVER OVERWRITE -- the same rule subjects.assign() applies. A claim
                # that already carries a subject was given one deliberately.
                cur.execute("UPDATE claims SET subject_id=%s WHERE document_section_id=%s "
                            "AND subject_id IS NULL", (sid, sec_id))
                counts["section_claims"] += cur.rowcount

        cur.execute(_IDEA_CLAIMS, (document_id,))
        linked, q = resolve(cur.fetchall(), subs)
        counts["ideas_linked"] = len(linked)
        counts["queued_ideas"] = len(q)
        queue += [{**x, "kind": "idea_claim"} for x in q]
        if not dry_run:
            for claim_id, sid in linked.items():
                cur.execute("UPDATE claims SET subject_id=%s WHERE id=%s AND subject_id IS NULL",
                            (sid, claim_id))
        if not dry_run:
            c.commit()

    counts["ambiguous"] = sum(1 for x in queue if x["reason"] == "ambiguous")
    if queue_path:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(json.dumps(queue, indent=1))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--document-id", type=int, required=True)
    ap.add_argument("--queue", help="where to write everything not resolved, and why")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    r = run(a.document_id, a.dsn, a.dry_run, Path(a.queue) if a.queue else None)
    print(f"[actions] document {a.document_id}")
    print(f"[actions]   sections linked {r['sections_linked']} "
          f"(carrying {r['section_claims']} claim(s)) · queued {r['queued_sections']}")
    print(f"[actions]   idea claims linked {r['ideas_linked']} · queued {r['queued_ideas']}")
    print(f"[actions]   ambiguous (two or more candidates, never guessed): {r['ambiguous']}")
    if a.dry_run:
        print("[actions]   dry run — nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
