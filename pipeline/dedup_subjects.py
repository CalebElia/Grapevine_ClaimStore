"""Propose duplicate initiative subjects. Never merges anything.

WHY THIS EXISTS. The wiki curated 229 initiatives by hand over time, and hand-curation over
time produces the same programme twice under two names: `Electrify City Fleet` and `City Fleet
Electrification` are one thing, and the review UI offered both as candidates for the same
sentence. A reviewer can catch that once; they should not have to catch it 229 times.

A DIFFERENT FOLD FROM THE MATCHER'S, DELIBERATELY. resolve_actions.key_terms strips leading
verbs, which is exactly right for "Implement Community Choice Aggregation" against "Community
Choice Aggregation" -- and exactly wrong here, because the verb is often the ONLY difference
between two spellings of one programme. Run through that fold, the fleet pair looks like
{city, fleet} versus {city, fleet, electrification} and never surfaces. So this module keeps
every word and folds each to a rough root instead, collapsing electrify / electrification /
electric.

IT PROPOSES, A PERSON DECIDES, which is the standing rule and not a formality here: the
detector cannot tell `Expand and Improve Local Transit` from `Expand and Improve Regional
Transit`, which differ by one word and are two real programmes. Anything it emits is a
question, and `subjects.created_by` stays human.

WHY NOT DELETE THE LOSER. pipeline/initiatives.py resolves a wiki page to a subject with
`WHERE wiki_slug=%s OR lower(name)=lower(%s)`. The wiki is read-only and still holds both
pages, so a deleted duplicate is recreated by the next re-seed and every repointed row is
orphaned again -- silently, because nothing errors. A merge has to leave a tombstone the
seeder can still find. See `merge_sql()`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from itertools import combinations
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Words that describe the container rather than the thing. "Community Solar Pilot" and
# "Community Solar Program" differ only by one of these, which is a strong duplicate signal.
_STOP = {"the", "a", "an", "of", "and", "or", "in", "to", "with", "our", "for", "toward",
         "towards", "more", "at", "on", "by", "from", "into", "new", "all", "we", "us",
         "its", "their", "this", "that", "as", "is", "are", "be",
         "program", "programs", "initiative", "initiatives", "project", "projects"}

# Longest first, so "ification" is tried before "ation" before "ion".
_SUFFIXES = ("ization", "ification", "ation", "ing", "ment", "ize", "ify", "ies", "es",
             "s", "al", "ic")
_MIN_ROOT = 4


def root(word: str) -> str:
    """Fold a word to a rough root. Crude on purpose, and only ever used to ask a question.

    Nothing downstream trusts this: a collision produces a proposal a human reads, never a
    stored fact. That is what licenses a fold this aggressive, which would be unacceptable in
    the mention matcher where a bad stem becomes a bad row.
    """
    w = word.lower()
    for s in _SUFFIXES:
        if len(w) - len(s) >= _MIN_ROOT and w.endswith(s):
            return w[:-len(s)]
    return w


def identity(name: str) -> frozenset[str]:
    """The root words that carry a programme's identity, order-insensitive."""
    return frozenset(root(w) for w in re.split(r"[^a-z0-9]+", (name or "").lower())
                     if w and w not in _STOP and len(w) > 2)


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def pairs(subjects: list[dict], threshold: float = 0.6) -> list[dict]:
    """Candidate duplicate pairs, strongest first. Pure: no database, so it is testable.

    Each subject is {id, name, mentions, claims, parent_id}. The one carrying evidence is
    proposed as the survivor, because repointing rows is cheaper and safer than repointing many.

    A DECLARED HIERARCHY IS AN ANSWER, NOT A QUESTION. `Community Solar Pilot` and `Community
    Solar Program` score 0.67 and are not duplicates: the wiki says `part-of`, and the store
    now records it as parent_subject_id. Re-asking would invite someone to merge away a
    distinction a curator recorded on purpose.
    """
    enriched = [s | {"ident": identity(s["name"])} for s in subjects]
    out = []
    for a, b in combinations(enriched, 2):
        if not a["ident"] or not b["ident"]:
            continue
        j = jaccard(a["ident"], b["ident"])
        if j < threshold:
            continue
        if a.get("parent_id") == b["id"] or b.get("parent_id") == a["id"]:
            continue        # already reconciled as parent and child
        # The one with evidence survives. On a tie, the lower id -- arbitrary but stable, and
        # the reviewer can swap it.
        ev = lambda s: (s["mentions"] + s["claims"], -s["id"])
        keep, drop = (a, b) if ev(a) >= ev(b) else (b, a)
        out.append({
            "score": round(j, 3),
            "identical": a["ident"] == b["ident"],
            "shared": sorted(a["ident"] & b["ident"]),
            "differing": sorted(a["ident"] ^ b["ident"]),
            "survivor": {k: keep[k] for k in ("id", "name", "mentions", "claims")},
            "duplicate": {k: drop[k] for k in ("id", "name", "mentions", "claims")},
            "verdict": None,     # a person fills this in
        })
    out.sort(key=lambda r: (not r["identical"], -r["score"],
                            -(r["survivor"]["mentions"] + r["survivor"]["claims"])))
    return out


# ── organisations ─────────────────────────────────────────────────────────────────────────
#
# ORG NAMES ARE NOT INITIATIVE NAMES, and the same threshold is wrong for both. An initiative
# is a distinctive noun phrase, so shared identity words are strong evidence. An org is
# [Place] [Function] [Type] -- "Ann Arbor · Housing · Commission" -- where two of three tokens
# are structural, so `Housing Commission` pairs with `Planning Commission` and `Transportation
# Commission` at exactly 0.60 each. At the subjects' threshold the queue is mostly false
# positives, which is how a reviewer learns to click without reading.
#
# The place and the org-kind carry no identity ON THEIR OWN; what distinguishes these orgs is
# the FUNCTION word (housing / planning / fire / finance). Removing the structural tokens
# makes the comparison happen where the difference actually lives.
# The jurisdiction every org in this corpus shares. "Ann Arbor" appearing in two names is not
# evidence of anything; "United States" in one and not the other is a spelling difference.
# Reduced through root() so it matches what identity() actually produces.
_ORG_PLACE_WORDS = {"ann", "arbor", "washtenaw", "michigan", "county", "city", "state",
                    "states", "united", "national", "usa", "local", "greater", "america",
                    "american", "of"}


def _place_roots() -> frozenset[str]:
    return frozenset(root(w) for w in _ORG_PLACE_WORDS)


_ORG_PLACE = _place_roots()


def org_identity(name: str) -> frozenset[str]:
    """Identity words for an organisation: everything except the shared jurisdiction.

    STRUCTURAL WORDS STAY. An earlier version stripped department / commission / alliance /
    agency on the theory that they are boilerplate, and it was badly wrong: `Ann Arbor Housing
    Commission` (government) and `Ann Arbor Housing Alliance` (nonprofit) both collapsed to
    {housing} and were reported IDENTICAL, as did `Michigan Public Service Commission` and
    `Ann Arbor Public Schools`. Those words are boilerplate only when they are shared; when
    they differ, they are the whole difference.

    Dropping the place instead is safe, and it is what makes `U.S. Department of Energy` and
    `United States Department of Energy` land on the same set.
    """
    return frozenset(w for w in identity(name) if w not in _ORG_PLACE)


def org_pairs(orgs: list[dict], threshold: float = 0.6) -> list[dict]:
    """Duplicate candidates among organisations. Same shape as pairs(), different fold.

    A shared org_type is not required -- `Ann Arbor SPARK` and `SPARK Ann Arbor` could easily
    be typed differently by two curators -- but a DIFFERENT type is reported so a reviewer can
    weigh it.
    """
    enriched = [o | {"ident": org_identity(o["name"])} for o in orgs]
    out = []
    for a, b in combinations(enriched, 2):
        if not a["ident"] or not b["ident"]:
            continue        # named only by place and kind; nothing to compare
        j = jaccard(a["ident"], b["ident"])
        if j < threshold:
            continue
        ev = lambda o: (o.get("claims", 0), -o["id"])
        keep, drop = (a, b) if ev(a) >= ev(b) else (b, a)
        out.append({
            "score": round(j, 3),
            "identical": a["ident"] == b["ident"],
            "shared": sorted(a["ident"] & b["ident"]),
            "differing": sorted(a["ident"] ^ b["ident"]),
            "same_type": a.get("org_type") == b.get("org_type"),
            "survivor": {k: keep.get(k) for k in ("id", "name", "org_type", "claims")},
            "duplicate": {k: drop.get(k) for k in ("id", "name", "org_type", "claims")},
            "verdict": None,
        })
    out.sort(key=lambda r: (not r["identical"], -r["score"]))
    return out


def load_orgs(dsn: str = DSN) -> list[dict]:
    import psycopg

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT o.id, o.name, o.org_type,
                         (SELECT count(*) FROM claims cl WHERE cl.org_id=o.id)
                       FROM orgs o ORDER BY o.id""")
        return [{"id": i, "name": n, "org_type": t, "claims": k}
                for i, n, t, k in cur.fetchall()]


def load(dsn: str = DSN, kind: str = "initiative") -> list[dict]:
    import psycopg

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT s.id, s.name, s.parent_subject_id,
                         (SELECT count(*) FROM claim_subject_mentions m WHERE m.subject_id=s.id),
                         (SELECT count(*) FROM claims cl WHERE cl.subject_id=s.id)
                       FROM live_subjects s WHERE s.subject_kind=%s ORDER BY s.id""",
                    (kind,))
        return [{"id": i, "name": n, "parent_id": p, "mentions": m, "claims": c_}
                for i, n, p, m, c_ in cur.fetchall()]


def referencing_columns(cur) -> list[tuple[str, str]]:
    """Every (table, column) with a foreign key into subjects, read from the catalog.

    Hand-listing these is how a merge orphans rows: there are 22 today, and a migration that
    adds the 23rd will not come with a reminder to update a literal list here.
    """
    cur.execute("""SELECT c.conrelid::regclass::text, a.attname
                     FROM pg_constraint c
                     JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
                    WHERE c.confrelid='subjects'::regclass AND c.contype='f'
                    ORDER BY 1, 2""")
    return [(t, col) for t, col in cur.fetchall()]


def merge_sql(cur, survivor_id: int, duplicate_id: int) -> list[str]:
    """The statements a merge would run, generated from the catalog. Returns; never executes.

    TOMBSTONE, NOT DELETE. The duplicate row stays, so initiatives.py still resolves its wiki
    page to something, and the fact that the wiki holds two pages for one programme stays
    visible rather than being quietly erased here. Marking it requires a `merged_into_id`
    column that does not exist yet -- the last statement is what that would look like, and is
    the reason this function returns text instead of running it.
    """
    stmts = []
    for table, col in referencing_columns(cur):
        if table == "subjects" and col in ("parent_subject_id",):
            stmts.append(f"UPDATE {table} SET {col}={survivor_id} "
                         f"WHERE {col}={duplicate_id};")
        elif table == "subjects":
            continue
        elif table == "subject_aliases":
            stmts.append(f"UPDATE {table} SET {col}={survivor_id} WHERE {col}={duplicate_id};")
        else:
            stmts.append(f"UPDATE {table} SET {col}={survivor_id} WHERE {col}={duplicate_id};")
    stmts.append(f"INSERT INTO subject_aliases (subject_id, alias, alias_type) "
                 f"SELECT {survivor_id}, name, 'other' FROM subjects WHERE id={duplicate_id} "
                 f"ON CONFLICT DO NOTHING;")
    stmts.append(f"-- requires a migration adding subjects.merged_into_id:\n"
                 f"-- UPDATE subjects SET merged_into_id={survivor_id} WHERE id={duplicate_id};")
    return stmts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="write the proposal here as JSON")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--kind", default="initiative")
    ap.add_argument("--orgs", action="store_true",
                    help="look for duplicate organisations instead of subjects")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--show-merge-sql", nargs=2, type=int, metavar=("SURVIVOR", "DUPLICATE"),
                    help="print the statements a merge would run, and run nothing")
    a = ap.parse_args()

    if a.show_merge_sql:
        import psycopg
        with psycopg.connect(a.dsn) as c, c.cursor() as cur:
            for s in merge_sql(cur, *a.show_merge_sql):
                print(s)
        return 0

    if a.orgs:
        subs = load_orgs(a.dsn)
        rows = org_pairs(subs, a.threshold)
        a.kind = "org"
    else:
        subs = load(a.dsn, a.kind)
        rows = pairs(subs, a.threshold)
    ident = [r for r in rows if r["identical"]]
    print(f"[dedup] {len(subs)} {a.kind} subject(s)")
    print(f"[dedup] {len(ident)} identical, {len(rows) - len(ident)} near "
          f"(>= {a.threshold}). Nothing merged — every row needs a verdict.\n")
    for r in rows:
        tag = "IDENTICAL" if r["identical"] else f"near {r['score']:.2f}"
        s, d = r["survivor"], r["duplicate"]
        print(f"  [{tag}]  differ by: {', '.join(r['differing']) or '(nothing)'}")
        fmt = (lambda x: f"c{x['claims']}  {x.get('org_type') or ''}") if a.orgs else \
              (lambda x: f"m{x['mentions']}/c{x['claims']}")
        print(f"      keep {s['id']:>4}  {s['name'][:48]:<48} {fmt(s)}")
        print(f"      dup  {d['id']:>4}  {d['name'][:48]:<48} {fmt(d)}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rows, indent=1))
        print(f"\n[dedup] wrote {a.out} — set \"verdict\" on each before any merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
