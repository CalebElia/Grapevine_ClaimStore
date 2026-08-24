"""The initiative layer: what a project is, where it happens, and who is involved.

WHAT COMES FROM THE WIKI AND WHAT DOES NOT. The wiki is a curated secondary source. Its
REFERENTS -- an initiative's name and slug, which strategies it advances, where it happens,
who is involved -- are the vocabulary of things, and importing them is the same act as
seeding 137 orgs and 7 strategies from it. Its ASSERTIONS -- status, launched year, milestone
targets -- are claims about the world as of its last edit, and those need a verbatim and a
document behind them. Only the referents are read here.

WHY STRATEGIES ARE A FRAMEWORK AND NOT A TREE. Caleb: an initiative "pushes forward a
Strategy or sometimes two". The schema said the same thing first, on
subject_framework_categories: "a Subject can sit in A2Zero Strategy 2 AND the Comprehensive
Plan's Land Use chapter AND the FY26 budget's capital line simultaneously -- which a strict
tree forbade." parent_subject_id can hold one parent; the union of parent-strategy and
related-strategies needs many.

WHY PLACE IS NEITHER. Bryant is where the decarbonization project HAPPENS, not what it is a
kind of. "Every initiative under A2ZERO" and "every initiative in Bryant" are different
questions -- one taxonomic, one geographic -- and a project can span two places.
"""
from __future__ import annotations

import re

# The field an actor is listed under names their role, so the role is never guessed.
ROLE_BY_FIELD = {
    "party-responsible": "lead",
    "partners": "community_partner",
}

_LINK = re.compile(r"\[\[([^\]|]+)")


def _front_matter(text: str) -> str:
    parts = text.split("---")
    return parts[1] if text.lstrip().startswith("---") and len(parts) > 2 else ""


def _scalar(fm: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", fm, re.M)
    return (m.group(1).strip().strip("'\"") if m else "")


def _list(fm: str, key: str) -> list[str]:
    """The wiki writes lists as `key:` then indented `- '[[type/slug]]'` lines.

    The list items are matched EXPLICITLY rather than by scanning to the next unindented
    line. A lookahead for "^\\S" terminates immediately, because the "-" that begins every
    list item is itself non-whitespace -- so every list read as empty and 229 initiatives
    arrived with no places and no related strategies.
    """
    m = re.search(rf"^{key}:[ \t]*\n((?:[ \t]*-[^\n]*\n)+)", fm, re.M)
    return [x.split("/")[-1] for x in _LINK.findall(m.group(1))] if m else []


def parse_initiative(text: str, slug: str) -> dict | None:
    """One wiki initiative file -> the referents it names, or None if it is not one."""
    fm = _front_matter(text)
    title = _scalar(fm, "title")
    kind = _scalar(fm, "type")
    if not title or (kind and kind != "initiative"):
        return None

    parent = _scalar(fm, "parent-strategy")
    parent_slug = _LINK.search(parent).group(1).split("/")[-1] if _LINK.search(parent) else None

    actors: list[tuple[str, str]] = []
    lead = _scalar(fm, "party-responsible")
    if (m := _LINK.search(lead)):
        actors.append((m.group(1).split("/")[-1], ROLE_BY_FIELD["party-responsible"]))
    for a in _list(fm, "partners"):
        actors.append((a, ROLE_BY_FIELD["partners"]))

    return {
        "title": title,
        "wiki_slug": f"initiatives/{slug}",
        "parent_strategy": parent_slug,
        "related_strategies": _list(fm, "related-strategies"),
        "places": _list(fm, "locations"),
        "actors": actors,
    }


def strategy_codes(rec: dict | None) -> list[str]:
    """Every strategy this initiative advances, as framework-category codes.

    The UNION of parent-strategy and related-strategies, in first-seen order. The wiki's
    slugs read "strategy-1-renewable-grid"; the framework's codes are "strategy-1", which is
    what framework_categories.code holds and what the CAP itself numbers them.
    """
    if not rec:
        return []
    out: list[str] = []
    for slug in [rec.get("parent_strategy"), *rec.get("related_strategies", [])]:
        if not slug:
            continue
        m = re.match(r"(strategy-\d+)", slug)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


WIKI = "../a2zero-wiki/wiki"
DSN = __import__("os").environ.get(
    "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine")


def _slug_title(path: str) -> str:
    """A wiki slug -> a readable name, used when no file defines the entity."""
    import re as _re
    return _re.sub(r"[-_]+", " ", path).strip().title()


def load(dsn: str = DSN, wiki: str = WIKI, created_by: str = "caleb (a2zero-wiki)",
         dry_run: bool = False) -> dict:
    """Load initiatives, their strategies, their places and their actors.

    Idempotent by wiki_slug and by (subject, category) / (subject, place) pairs, so a re-run
    after the wiki changes adds rather than duplicates.
    """
    import glob
    import os

    import psycopg

    counts = {"initiatives": 0, "strategy_links": 0, "places": 0, "actors": 0,
              "unresolved_actors": 0}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        # THE FRAMEWORK. The schema names this example itself: 'A2Zero CAP-2020 strategies',
        # with code 'strategy-1'. defining_document_id stays NULL until the CAP is ingested;
        # the framework exists independently of whether we have read its document yet.
        cur.execute("SELECT id FROM frameworks WHERE name = %s",
                    ("A2ZERO CAP-2020 strategies",))
        row = cur.fetchone()
        if row:
            fw = row[0]
        else:
            cur.execute("INSERT INTO frameworks (jurisdiction_id, name, valid_from) "
                        "VALUES (1,%s,'2020-04-01') RETURNING id",
                        ("A2ZERO CAP-2020 strategies",))
            fw = cur.fetchone()[0]

        cats: dict[str, int] = {}
        for f in sorted(glob.glob(os.path.join(wiki, "strategies", "*.md"))):
            head = open(f).read()[:900]
            num = (re.search(r"^strategy-number:\s*(\d+)", head, re.M) or [None, ""])[1]
            title = (re.search(r"^title:\s*(.+)$", head, re.M) or [None, ""])[1].strip().strip("'\"")
            if not num or not title:
                continue
            code = f"strategy-{num}"
            cur.execute("SELECT id FROM framework_categories WHERE framework_id=%s AND code=%s",
                        (fw, code))
            r = cur.fetchone()
            if r:
                cats[code] = r[0]
            elif not dry_run:
                cur.execute("INSERT INTO framework_categories (framework_id, code, name, "
                            "sequence) VALUES (%s,%s,%s,%s) RETURNING id",
                            (fw, code, title, int(num)))
                cats[code] = cur.fetchone()[0]

        def subject(name: str, kind: str, slug: str | None) -> int:
            cur.execute("SELECT id FROM subjects WHERE wiki_slug=%s OR lower(name)=lower(%s)",
                        (slug, name))
            r = cur.fetchone()
            if r:
                return r[0]
            cur.execute("""INSERT INTO subjects (name, jurisdiction_id, wiki_slug,
                                                 subject_kind, created_by)
                           VALUES (%s,1,%s,%s,%s) RETURNING id""",
                        (name, slug, kind, created_by))
            return cur.fetchone()[0]

        for f in sorted(glob.glob(os.path.join(wiki, "initiatives", "*.md"))):
            rec = parse_initiative(open(f).read(), os.path.basename(f)[:-3])
            if not rec or dry_run:
                continue
            sid = subject(rec["title"], "initiative", rec["wiki_slug"])
            counts["initiatives"] += 1

            for code in strategy_codes(rec):
                if code not in cats:
                    continue
                cur.execute("""INSERT INTO subject_framework_categories
                                 (subject_id, category_id, assigned_by)
                               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (sid, cats[code], created_by))
                counts["strategy_links"] += cur.rowcount

            for place in rec["places"]:
                pid = subject(_slug_title(place), "place", f"locations/{place}")
                cur.execute("""INSERT INTO subject_places
                                 (subject_id, place_subject_id, assigned_by)
                               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (sid, pid, created_by))
                counts["places"] += cur.rowcount

            if rec["actors"]:
                cur.execute("SELECT id FROM coalitions WHERE subject_id=%s", (sid,))
                r = cur.fetchone()
                if r:
                    coid = r[0]
                else:
                    cur.execute("INSERT INTO coalitions (subject_id, name, purpose) "
                                "VALUES (%s,%s,%s) RETURNING id",
                                (sid, f"{rec['title']} delivery group",
                                 "Actors the registry records as involved in this initiative"))
                    coid = cur.fetchone()[0]
                for actor_slug, role in rec["actors"]:
                    # EXACTLY ONE ORG, by the wiki file the org was seeded from. The same
                    # identity rule the funder resolver uses: an actor attributed to the
                    # wrong body says something false about who is accountable.
                    cur.execute("SELECT id FROM orgs WHERE notes LIKE %s",
                                (f"%wiki/actors/{actor_slug}.md",))
                    hit = cur.fetchall()
                    if len(hit) != 1:
                        counts["unresolved_actors"] += 1
                        continue
                    cur.execute("""INSERT INTO coalition_members
                                     (coalition_id, org_id, role)
                                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                                (coid, hit[0][0], role))
                    counts["actors"] += cur.rowcount
        if not dry_run:
            c.commit()
    return counts
