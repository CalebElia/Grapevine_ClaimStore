"""Subjects from the reports' own structure.

WHY STRUCTURE AND NOT A MODEL. All five reports organise themselves into the same seven
A2ZERO strategies and title them differently each year -- "Strategy 1: Power our electrical
grid with 100% renewable energy", "STRATEGY ONE: POWER OUR ELECTRICAL GRID...", "STRATEGY 1:
100% RENEWABLES". Same subject, three titles. A section's placement is the document SAYING
what it is about, which makes this evidence rather than inference, and string matching rather
than judgement.

THE SUBJECT LIVES ON THE SECTION, NOT ON EACH CLAIM. There are 59 sections and 903 claims.
Recording the decision once per section makes it auditable and reversible; writing it 903
times makes it 903 things to re-derive when the mapping changes. Claims inherit.

WHAT INHERITANCE CANNOT DO, SAID PLAINLY. A claim about solar inside the Resilience section
inherits Resilience, and that is wrong for that claim. Section inheritance is a floor, not a
ceiling: it gives every claim a defensible subject drawn from the document's own structure,
and leaves finer attribution -- the 229 wiki initiatives -- to a later pass that can overrule
it per claim.
"""
from __future__ import annotations

import re

# A2ZERO has exactly seven strategies. An eighth is a parse error, not a new subject.
MAX_STRATEGY = 7

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

# ANCHORED ON THE WORD "STRATEGY", never on a digit anywhere in the heading. "YEAR 5
# PRIORITIES" contains a 5 and is not Strategy 5; so does "A2ZERO Year 3 Priorities".
_HEAD = re.compile(r"\bstrateg(?:y|ies)\s+(\d{1,2}|" + "|".join(_WORDS) + r")\b", re.I)


def strategy_number(heading: str | None) -> int | None:
    """The A2ZERO strategy a section heading names, or None if it names none."""
    m = _HEAD.search(heading or "")
    if not m:
        return None
    token = m.group(1).lower()
    n = _WORDS.get(token) or (int(token) if token.isdigit() else None)
    return n if n and 1 <= n <= MAX_STRATEGY else None


# Sections that speak about the plan as a whole rather than about one strategy. A claim with
# no subject is invisible to every aggregate, so a narrative section gets the PARENT subject
# rather than nothing -- which is also true to the text: an introduction really is about
# A2ZERO.
_A2ZERO_HEADS = re.compile(
    r"^\s*(introduction|overview|closing|next steps|"
    r"(a2zero\s+)?year\s+\w+\s+priorities|priorities)\s*$", re.I)

# The community-wide inventory every report from Year 3 opens with. It is the measurement the
# whole plan is judged against, not one strategy's business.
_GHG_HEADS = re.compile(r"greenhouse gas emissions summary", re.I)


def cross_cutting_subject(heading: str | None) -> str | None:
    """A non-strategy section's subject key, or None when the section is navigational.

    A table of contents is not about anything, and neither is a repeated cover title.
    Inventing a subject for those would put structural furniture into topic aggregates.
    """
    h = (heading or "").strip()
    if not h or strategy_number(h) is not None:
        return None
    if _GHG_HEADS.search(h):
        return "ghg_emissions"
    if _A2ZERO_HEADS.match(h):
        return "a2zero"
    return None


def section_subject_key(heading: str | None, is_front_matter: bool = False) -> str | None:
    """The single subject key for a section: strategy-N, a2zero, ghg_emissions, or None.

    Front matter -- everything before the first heading -- is the report's own framing of
    A2ZERO. Year 2 carries five claims there, all of them about the plan itself, while other
    years put the same material under INTRODUCTION. Front matter holding only a title and a
    sign-off produces no claims, so the rule costs nothing where it does not apply.
    """
    n = strategy_number(heading)
    if n is not None:
        return f"strategy-{n}"
    key = cross_cutting_subject(heading)
    if key:
        return key
    return "a2zero" if is_front_matter else None


# The seven strategies as the a2zero-wiki curates them: canonical title and slug. Read from
# the wiki rather than typed here, so the store and the wiki cannot drift apart silently.
WIKI = "../a2zero-wiki/wiki/strategies"


def read_wiki_strategies(path: str = WIKI) -> dict[int, dict]:
    """{strategy number: {title, slug}} from the wiki's own front matter."""
    import glob
    import os
    import re as _re

    out: dict[int, dict] = {}
    for f in sorted(glob.glob(os.path.join(path, "*.md"))):
        head = open(f).read()[:800]
        get = lambda k: (_re.search(rf"^{k}:\s*(.+)$", head, _re.M) or [None, ""])[1]
        num = get("strategy-number").strip()
        title = get("title").strip().strip("'\"")
        if num.isdigit() and title:
            out[int(num)] = {"title": title, "slug": os.path.basename(f)[:-3]}
    return out


def seed(dsn: str, created_by: str, dry_run: bool = False) -> dict:
    """Create the strategy subjects under A2ZERO, plus the emissions subject.

    Idempotent by name. The seven strategies come from the wiki, which already carries the
    judgement that these are the seven; inventing a parallel list here would create a second
    place for them to be wrong.
    """
    import psycopg

    strategies = read_wiki_strategies()
    made = 0
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id FROM subjects WHERE name = 'A2ZERO'")
        row = cur.fetchone()
        if not row:
            raise SystemExit("[subjects] no A2ZERO subject to parent these to")
        parent = row[0]

        wanted = [(f"strategy-{n}", s["title"], s["slug"]) for n, s in strategies.items()]
        wanted.append(("ghg_emissions", "Community-wide greenhouse gas emissions", None))

        for key, title, slug in wanted:
            cur.execute("SELECT id FROM subjects WHERE name = %s", (title,))
            if cur.fetchone():
                continue
            if not dry_run:
                cur.execute(
                    """INSERT INTO subjects (name, description, jurisdiction_id,
                                             parent_subject_id, wiki_slug, created_by)
                       VALUES (%s,%s,1,%s,%s,%s) RETURNING id""",
                    (title,
                     f"A2ZERO subject, keyed `{key}` from the reports' own section structure.",
                     parent, f"strategies/{slug}" if slug else None, created_by))
                sid = cur.fetchone()[0]
                # The heading each year gives it, so the same subject is findable under any
                # of its titles.
                cur.execute("INSERT INTO subject_aliases (subject_id, alias, alias_type) "
                            "VALUES (%s,%s,'other') ON CONFLICT DO NOTHING", (sid, key))
            made += 1
        if not dry_run:
            c.commit()
    return {"strategies": len(strategies), "created": made}


def assign(dsn: str, dry_run: bool = False) -> dict:
    """Point every section at its subject, then let claims inherit it."""
    import psycopg

    counts = {"sections": 0, "claims": 0, "no_subject": 0}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id, name FROM subjects")
        by_alias: dict[str, int] = {}
        for sid, name in cur.fetchall():
            cur.execute("SELECT alias FROM subject_aliases WHERE subject_id=%s", (sid,))
            for (a,) in cur.fetchall():
                by_alias[a] = sid
            if name == "A2ZERO":
                by_alias["a2zero"] = sid

        cur.execute("SELECT id, heading, sequence FROM document_sections ORDER BY id")
        for sec_id, heading, seq in cur.fetchall():
            key = section_subject_key(heading, is_front_matter=(seq == 0))
            sid = by_alias.get(key) if key else None
            if sid is None:
                counts["no_subject"] += 1
                continue
            counts["sections"] += 1
            if not dry_run:
                cur.execute("UPDATE document_sections SET subject_id=%s WHERE id=%s",
                            (sid, sec_id))
                # INHERIT, NEVER OVERWRITE. A claim that already carries a subject was given
                # one deliberately -- by a finer pass or by a person -- and the section is
                # the coarser fact.
                cur.execute(
                    "UPDATE claims SET subject_id=%s "
                    "WHERE document_section_id=%s AND subject_id IS NULL", (sid, sec_id))
                counts["claims"] += cur.rowcount
        if not dry_run:
            c.commit()
    return counts


def main() -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="seed A2ZERO subjects and assign them")
    ap.add_argument("--seed", action="store_true", help="create the strategy subjects")
    ap.add_argument("--assign", action="store_true", help="point sections and claims at them")
    ap.add_argument("--created-by", default="caleb (from a2zero-wiki strategies)")
    ap.add_argument("--dsn", default=os.environ.get(
        "GRAPEVINE_DSN", "host=/tmp port=5433 user=grapevine dbname=grapevine"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.seed:
        r = seed(a.dsn, a.created_by, a.dry_run)
        print(f"[subjects] {r['created']} created from {r['strategies']} wiki strategies")
    if a.assign:
        r = assign(a.dsn, a.dry_run)
        print(f"[subjects] {r['sections']} section(s) · {r['claims']} claim(s) · "
              f"{r['no_subject']} section(s) left without one"
              + ("  (dry run — nothing written)" if a.dry_run else ""))
    if not (a.seed or a.assign):
        ap.error("nothing to do: pass --seed and/or --assign")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
