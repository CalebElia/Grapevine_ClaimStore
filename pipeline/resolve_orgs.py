"""Seed `orgs` from the a2zero-wiki actor registry, and attach claims to them.

TWO STEPS, DELIBERATELY SEPARATE. Seeding imports 154 hand-curated actors one way from
`../a2zero-wiki` (READ-ONLY -- nothing here writes to it). Resolving attaches a claim to an
org only when the claim's own verbatim names it.

WHY THIS IS STRING MATCHING AND NOT A MODEL. An org is a referent: get it wrong and the
claim says a thing about the wrong body, which is worse than saying nothing. The wiki
already carries the judgement -- somebody decided Ann Arbor SPARK is one organisation with
that name -- so the only question left here is whether these characters appear in this
sentence, and characters are what string matching is for.

THE AMBIGUOUS LIST IS LOAD-BEARING. entity_aliases.json marks terms that resolve to more
than one entity, and "CAN" is the case that matters: it is Community Action Network in this
corpus and also an ordinary English modal verb. Matching it would attach an org to every
sentence containing "can". Anything the wiki calls ambiguous is skipped, and a short
all-caps alias must appear as a standalone token.

A PERSON IS NOT AN ORG. 16 of the 154 actors are people and belong in `persons`, which is
already populated from Legistar. They are not imported here.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

WIKI = Path("../a2zero-wiki")

# actor-type values that are not organisations. Everything else is.
_NOT_AN_ORG = {"person", "government-role"}

# The wiki's actor-type spellings translated to the store's org_type vocabulary. Kept in a
# registry rather than here because several entries are judgement calls, and a judgement
# should be visible to the person who has to live with it.
_ORG_TYPES = Path(__file__).parent.parent / "registries" / "ann_arbor" / "org_types.json"


def org_type_map(path: Path = _ORG_TYPES) -> dict[str, str]:
    return json.loads(path.read_text())["map"] if path.exists() else {}


def map_org_type(kind: str | None, mapping: dict[str, str]) -> tuple[str | None, str | None]:
    """(org_type, unmapped). Exactly one is non-None; an unknown kind is a finding.

    NOT A PASSTHROUGH, AND NOT A GUESS. Passing an unrecognised value on lets the vocabulary
    trigger store the fallback 'other' and file a proposal nobody reads -- which is how 81 of
    142 orgs came to be typed 'other'. Returning it as unmapped puts it in front of a person
    while it is still one row rather than eighty.
    """
    if not kind:
        return None, None
    k = kind.strip().strip("'\"").lower()
    if k in mapping:
        return mapping[k], None
    return None, k

# An alias this short must be a standalone token, never a substring. "CAN" inside
# "candidate" is not Community Action Network, and neither is "can" in "can be".
_SHORT_ALIAS = 5


def _clean(v: str) -> str:
    return (v or "").strip().strip("'\"").strip()


def read_actors(wiki: Path = WIKI) -> list[dict]:
    """The wiki's actor files -> org records. One-way; the wiki is never written to."""
    mapping = org_type_map()
    out = []
    for f in sorted(glob.glob(str(wiki / "wiki/actors/*.md"))):
        head = open(f).read()[:1200]
        get = lambda k: _clean((re.search(rf"^{k}:\s*(.+)$", head, re.M) or [None, ""])[1])
        kind, title = get("actor-type"), get("title")
        if not title or kind in _NOT_AN_ORG:
            continue
        org_type, unmapped = map_org_type(kind, mapping)
        out.append({"name": title, "org_type": org_type, "unmapped_type": unmapped,
                    "slug": os.path.basename(f)[:-3]})
    return out


def read_aliases(wiki: Path = WIKI) -> tuple[dict[str, str], set[str]]:
    """(alias -> canonical slug, ambiguous aliases). Ambiguity is respected, not resolved."""
    p = wiki / "registry/entity_aliases.json"
    if not p.exists():
        return {}, set()
    raw = json.loads(p.read_text())
    ambiguous = {a.lower() for e in raw.get("_ambiguous_terms", [])
                 for a in e.get("aliases", [])}
    out: dict[str, str] = {}
    for key, e in raw.items():
        if key.startswith("_") or not isinstance(e, dict):
            continue
        canon = str(e.get("canonical", ""))
        if not canon.startswith("actors/"):
            continue
        slug = canon.split("/", 1)[1]
        for a in [*e.get("aliases", []), key]:
            if a and a.lower() not in ambiguous:
                out[a.lower()] = slug
    return out, ambiguous


def read_funder_aliases(
        path: Path = Path("registries/ann_arbor/funder_aliases.json")) -> dict[str, str]:
    """How this corpus writes a funder's name -> the org registry's spelling.

    The wiki's alias table covers the bodies the wiki curated. A funder is a different
    population: MI-HOPE awards money and is not an actor in Ann Arbor's climate work, so
    nobody wrote it a wiki page. This file is where those pairs live, each with who decided.
    """
    if not path.exists():
        return {}
    return {a["as_written"].lower(): a["org"]
            for a in json.loads(path.read_text()).get("aliases", [])}


def read_funder_programs(
        path: Path = Path("registries/ann_arbor/funder_aliases.json")) -> dict[str, str]:
    """How the corpus writes a funder -> the NAMED PROGRAM the money came under.

    Distinct from read_funder_aliases, which answers "who paid". A document writing
    "SEMCOG Carbon Reduction Program" states two facts: the body is SEMCOG and the vehicle
    is that programme. Resolving only the first answers who and discards under-what, and
    under-what is what a researcher follows across years and cities.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    # A PROGRAMME'S OWN NAME IS A KEY. The Year 3 report writes "Energy Efficiency and
    # Conservation Block Grant" and nothing else -- no agency, no abbreviation. Requiring
    # every such string to be duplicated into the alias list as well is curation the file
    # already contains, and the first version of this silently resolved nothing for exactly
    # that reason: EECBG was declared as a programme and never as an alias.
    out = {p["name"].lower(): p["name"] for p in raw.get("programs", [])}
    out |= {p["abbreviation"].lower(): p["name"]
            for p in raw.get("programs", []) if p.get("abbreviation")}
    out |= {a["as_written"].lower(): a["program"]
            for a in raw.get("aliases", []) if a.get("program")}
    return out


def mentions(text: str, name: str) -> bool:
    """Whether `text` names this organisation. Word-bounded; short names must stand alone."""
    n = name.strip()
    if not n:
        return False
    if len(n) <= _SHORT_ALIAS:
        return re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text) is not None
    return re.search(rf"(?<![\w-]){re.escape(n)}", text, re.I) is not None


def seed(dsn: str = DSN, wiki: Path = WIKI, jurisdiction_id: int = 1) -> int:
    import psycopg

    actors = read_actors(wiki)
    n = 0
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for a in actors:
            cur.execute("SELECT id FROM orgs WHERE lower(name) = lower(%s)", (a["name"],))
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO orgs (name, org_type, jurisdiction_id, notes) "
                "VALUES (%s,%s,%s,%s)",
                (a["name"], a["org_type"], jurisdiction_id,
                 f"seeded from a2zero-wiki/wiki/actors/{a['slug']}.md"))
            n += 1
        c.commit()
    return n


def backfill_types(dsn: str = DSN, wiki: Path = WIKI, dry_run: bool = False) -> dict:
    """Retype orgs already seeded, from the wiki via the mapping registry.

    SEPARATE FROM seed(), which skips a row that already exists -- correct for names, wrong
    for a column whose translation has since been fixed. Matching is on the wiki SLUG kept in
    `notes`, not the name: a name can be edited in the store, the provenance string is what
    says which file the row came from.
    """
    import psycopg

    actors = {a["slug"]: a for a in read_actors(wiki)}
    changed, unchanged, no_source = [], 0, []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id, name, org_type, notes FROM orgs ORDER BY id")
        for oid, name, current, notes in cur.fetchall():
            slug = None
            if notes and "wiki/actors/" in notes:
                slug = notes.rsplit("wiki/actors/", 1)[1].removesuffix(".md")
            a = actors.get(slug or "")
            if not a:
                no_source.append((oid, name))
                continue
            if a["org_type"] == current:
                unchanged += 1
                continue
            changed.append((oid, name, current, a["org_type"]))
            if not dry_run:
                cur.execute("UPDATE orgs SET org_type=%s WHERE id=%s", (a["org_type"], oid))
        if not dry_run:
            c.commit()
    return {"changed": changed, "unchanged": unchanged, "no_wiki_source": no_source}


def resolve(section_id: int | None, dsn: str = DSN, wiki: Path = WIKI,
            dry_run: bool = False) -> list[dict]:
    """Attach org_id where a claim's own verbatim names exactly one organisation.

    EXACTLY ONE. A sentence naming two organisations -- "In collaboration with Community
    Action Network (CAN), won $500,000" names CAN and, implicitly, the City -- cannot be
    reduced to a single org_id without choosing, and choosing is not string matching's job.
    Those are reported for a human rather than guessed at, which is the same rule the
    conversion used for every ambiguity it met.
    """
    import psycopg

    aliases, _ = read_aliases(wiki)
    out = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        # SLUG -> ORG, EXACTLY. The seed records which wiki file each org came from, so an
        # alias resolves to one org by identity. Matching the slug against org NAMES by
        # substring instead fans out catastrophically: "city-of-ann-arbor" is a substring
        # of six department names, so any claim mentioning the City named all six and was
        # reported ambiguous. A containment test between two identifiers is not a lookup.
        # live_orgs, not orgs: a merged duplicate keeps its row so the wiki seeder can still
        # resolve the file that made it (migrations/026), but attaching a claim to a tombstone
        # would put the claim somewhere nothing else points.
        cur.execute("SELECT id, name, notes FROM live_orgs")
        rows_ = cur.fetchall()
        orgs = [(oid, name) for oid, name, _ in rows_]
        # A MERGE MUST NOT COST A NAME. `SPARK Ann Arbor` and `United States Department of
        # Energy` are names documents actually print; they survive as aliases of the survivor,
        # and matching reads them alongside the canonical name. Without this the merge would
        # quietly reduce what the resolver can find.
        cur.execute("""SELECT a.org_id, a.alias FROM org_aliases a
                         JOIN live_orgs o ON o.id = a.org_id""")
        alias_rows = cur.fetchall()
        by_slug = {}
        for oid, _name, notes in rows_:
            m = re.search(r"wiki/actors/([\w.-]+)\.md", notes or "")
            if m:
                by_slug[m.group(1)] = oid

        q = ("SELECT id, verbatim FROM claims WHERE org_id IS NULL"
             + (" AND document_section_id = %s" if section_id else ""))
        cur.execute(q, (section_id,) if section_id else ())
        for cid, verbatim in cur.fetchall():
            hits = {oid: name for oid, name in orgs if mentions(verbatim, name)}
            names = dict(orgs)
            for oid, alias in alias_rows:
                if oid not in hits and mentions(verbatim, alias):
                    hits[oid] = names[oid]
            for alias, slug in aliases.items():
                if slug in by_slug and mentions(verbatim, alias):
                    oid = by_slug[slug]
                    hits[oid] = next(n for i, n in orgs if i == oid)
            rec = {"claim_id": cid, "matched": sorted(hits.values()),
                   "verbatim": verbatim[:70]}
            if len(hits) == 1 and not dry_run:
                cur.execute("UPDATE claims SET org_id = %s WHERE id = %s",
                            (next(iter(hits)), cid))
            out.append(rec)
        if not dry_run:
            c.commit()
    return out


def backfill_funders(dsn: str = DSN, dry_run: bool = False) -> list[tuple]:
    """Re-resolve stored funder names against the registry as it stands NOW.

    WHY THIS EXISTS. funder_name_text records what the document said whether or not the
    registry could place it, so a registry gap is recoverable -- but only if adding the row
    can reach the claims already written. Without this, closing a gap means re-running the
    extraction, which costs a model call and returns a DIFFERENT name: the funder's
    specificity drifts between runs ('SEMCOG' vs 'SEMCOG Carbon Reduction Program'), so
    re-extracting to fix one gap can open another. The registry is the thing that changed;
    only the resolution should be recomputed.

    Only ever fills a NULL. An awarding_org_id already set was decided against the evidence
    at the time and is not overwritten by a later registry edit.
    """
    import psycopg
    from pipeline.extract_claims import _funder_org

    done = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        programs = read_funder_programs()
        cur.execute("SELECT id, funder_name_text, awarding_org_id, program_id "
                    "FROM fiscal_references WHERE funder_name_text IS NOT NULL "
                    "AND (awarding_org_id IS NULL OR program_id IS NULL)")
        for fid, name, had_org, had_prog in cur.fetchall():
            oid = _funder_org(cur, name) if had_org is None else None
            # THE PROGRAMME IS RESOLVED SEPARATELY FROM THE BODY. A reference can know one
            # and not the other in either direction: "MI-HOPE" names a programme whose
            # administering agency this corpus never states, and "State of Michigan" names
            # a body under no programme we can see. Filling both from one lookup would
            # force a guess in whichever direction was short.
            pid = None
            if had_prog is None and (pname := programs.get((name or "").lower())):
                cur.execute("SELECT id FROM funding_programs WHERE name=%s", (pname,))
                if (r := cur.fetchone()):
                    pid = r[0]
                    # A programme carries its administering body. If the reference could
                    # not resolve an org on its own, inherit it -- that is not inference,
                    # it is the curated link in funding_programs.
                    if oid is None and had_org is None:
                        cur.execute("SELECT administering_org_id FROM funding_programs "
                                    "WHERE id=%s", (pid,))
                        oid = cur.fetchone()[0]
            if oid is None and pid is None:
                continue
            if not dry_run:
                if oid is not None:
                    cur.execute("UPDATE fiscal_references SET awarding_org_id=%s WHERE id=%s",
                                (oid, fid))
                if pid is not None:
                    cur.execute("UPDATE fiscal_references SET program_id=%s WHERE id=%s",
                                (pid, fid))
            done.append((fid, name, oid))
        if not dry_run:
            c.commit()
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="seed orgs and attach claims to them")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--backfill-funders", action="store_true",
                    help="re-resolve stored funder names against the current registry")
    ap.add_argument("--section-id", type=int)
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--wiki", default=str(WIKI))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.seed:
        print(f"[orgs] seeded {seed(a.dsn, Path(a.wiki))} organisation(s)")
    if a.backfill_funders:
        got = backfill_funders(a.dsn, a.dry_run)
        for _fid, name, _oid in got:
            print(f"  funder {name!r} -> org {_oid}")
        print(f"[funders] {len(got)} reference(s) resolved from the registry")
        return 0

    rows = resolve(a.section_id, a.dsn, Path(a.wiki), a.dry_run)
    one = [r for r in rows if len(r["matched"]) == 1]
    many = [r for r in rows if len(r["matched"]) > 1]
    none = [r for r in rows if not r["matched"]]
    print(f"[orgs] {len(one)} attached · {len(many)} name more than one (left for a human) "
          f"· {len(none)} name none")
    for r in many:
        print(f"  AMBIGUOUS claim {r['claim_id']}: {', '.join(r['matched'])}")
        print(f"            {r['verbatim']}")
    for r in one:
        print(f"  claim {r['claim_id']:>3} -> {r['matched'][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
