"""Subjects from a document's own structure, where the document type has one.

WHY THIS IS PER DOCUMENT TYPE. The seven A2ZERO strategies are formal to Ann Arbor's plan,
and the STRATEGY heading language appears only in formal reports. Council minutes, dockets,
news coverage and staff memos talk about the projects, policies and initiatives that SUPPORT
a strategy and almost never name the strategy itself. So a structural route from heading to
subject exists for annual reports and will simply not exist for most document types. A doc
type with no rules is the NORMAL case, not a misconfiguration -- those documents will get
their subject by matching initiative names against subject_aliases, or through a matter, and
until that pass exists their claims carry no subject rather than a guessed one.

WHAT IS AND IS NOT CORPUS-SPECIFIC. The SUBJECTS are a property of Ann Arbor's climate
policy: a council minute about a solar millage maps to the same subject row as an annual
report section. The MAPPING -- that a heading reading "STRATEGY 4" means Strategy 4 -- is a
property of one document type. Only the mapping lives in a registry file; the subjects live
in the database, seeded from the wiki.

NO NUMBER IS HARDCODED. Seven is true of Ann Arbor and breaks at the first other
jurisdiction, so the valid strategy numbers are whichever strategy subjects were actually
seeded for that jurisdiction, read at assignment time. A plan with nine works without an
edit.

WHAT INHERITANCE CANNOT DO, said here rather than left to be discovered: a claim about solar
inside the Resilience section inherits Resilience, and that is wrong for that claim. Section
inheritance is a floor -- a defensible subject for every claim, drawn from the document's own
structure -- with the 229 wiki initiatives left for a finer pass that can overrule it.
"""
from __future__ import annotations

import re

import json
from pathlib import Path

REGISTRIES = Path("registries")

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10}


def load_section_rules(jurisdiction: str, doc_type: str,
                       root: Path = REGISTRIES) -> dict | None:
    """Structural section rules for one jurisdiction and document type, or None.

    None means this document type has no structural route to a subject, which is the normal
    case for everything except formal reports. Callers must treat it as "no route", never as
    an error.
    """
    p = root / jurisdiction / "section_subjects.json"
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("doc_types", {}).get(doc_type)


def strategy_number(heading: str | None, valid: set[int] | None = None,
                    keyword: str = r"strateg(?:y|ies)") -> int | None:
    """The numbered series member a heading names, or None.

    ANCHORED ON THE KEYWORD, never on a digit anywhere in the heading: "YEAR 5 PRIORITIES"
    contains a 5 and is not Strategy 5.

    `valid` is the set of numbers that actually exist for this jurisdiction. Passing None
    parses without judging, which is what the pure tests want; passing a set is what callers
    do, and is why no maximum is written into this module.
    """
    pat = re.compile(rf"\b{keyword}\s+(\d{{1,2}}|" + "|".join(_WORDS) + r")\b", re.I)
    m = pat.search(heading or "")
    if not m:
        return None
    token = m.group(1).lower()
    n = _WORDS.get(token) or (int(token) if token.isdigit() else None)
    if not n or n < 1:
        return None
    return n if valid is None or n in valid else None


def cross_cutting_subject(heading: str | None, rules: dict | None) -> str | None:
    """A non-strategy section's subject key from the registry's heading rules."""
    h = (heading or "").strip()
    if not h or not rules:
        return None
    for rule in rules.get("headings", []):
        pattern = rule["match"]
        rx = re.compile(pattern, re.I)
        hit = rx.match(h) if rule.get("anchored") else rx.search(h)
        if hit:
            return rule["subject_key"]
    return None


def section_topics(headings: list[tuple[int, str | None]],
                   rules: dict | None) -> dict[int, str]:
    """{section_id: topic} for one document's sections, in sequence order.

    A RUN, NOT A PATTERN PER SECTION. The CAP's ideas appendix is 35 sections whose headings
    are ordinary words -- "Financing", "Energy", "Water", "Response" -- that also name real
    content sections elsewhere in the same document. Matching those words would mis-frame the
    real ones. What identifies the appendix is CONTAINMENT: everything after its heading
    belongs to it. document_sections is flat, so containment has to be expressed as a run
    over `sequence`, which is exactly what the reading order already encodes.

    `headings` must be (section_id, heading) in sequence order. Sections before the opening
    heading, and any document type with no rule, get nothing -- absence is the normal case.
    """
    out: dict[int, str] = {}
    for rule in ((rules or {}).get("section_topics", {}) or {}).get("runs", []):
        rx = re.compile(rule["opens_with"], re.I)
        topic, started = rule["topic"], False
        for sec_id, heading in headings:
            if not started:
                if rx.search((heading or "").strip()):
                    started = True
                    if rule.get("include_opening_section"):
                        out[sec_id] = topic
                continue
            out[sec_id] = topic
        # A run that never opened is not an error: the rule describes a section this
        # particular document may simply not have.
    return out


def section_subject_key(heading: str | None, is_front_matter: bool = False,
                        rules: dict | None = None,
                        valid: set[int] | None = None) -> str | None:
    """The single subject key for a section, or None when the section maps to nothing.

    Returns None for every section when `rules` is None -- a document type with no structural
    route. That is the expected outcome for minutes, dockets and articles.
    """
    if not rules:
        return None
    series = rules.get("numbered_series")
    if series:
        n = strategy_number(heading, valid, series.get("keyword", r"strateg(?:y|ies)"))
        if n is not None:
            return series["subject_key"].format(n=n)
    key = cross_cutting_subject(heading, rules)
    if key:
        return key
    return rules.get("front_matter") if is_front_matter else None


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


def seeded_series_numbers(cur, key_template: str = "strategy-{n}") -> set[int]:
    """Which members of the numbered series actually exist as subjects.

    THIS IS WHY NO MAXIMUM IS WRITTEN ANYWHERE. Seven is true of Ann Arbor and breaks at the
    first other jurisdiction. The answer is whatever was seeded, so a plan with nine works
    without an edit and a plan with five refuses a spurious "Strategy 6".
    """
    prefix = key_template.split("{", 1)[0]
    cur.execute("SELECT alias FROM subject_aliases WHERE alias LIKE %s", (prefix + "%",))
    out = set()
    for (alias,) in cur.fetchall():
        tail = alias[len(prefix):]
        if tail.isdigit():
            out.add(int(tail))
    return out


def assign(dsn: str, dry_run: bool = False) -> dict:
    """Point every section at its subject, then let claims inherit it.

    Rules are looked up per DOCUMENT TYPE. A document whose type has no rules contributes
    nothing and is counted, not warned about: minutes and dockets are expected to have no
    structural route to a subject.
    """
    import psycopg

    counts = {"sections": 0, "claims": 0, "no_subject": 0, "no_rules": 0}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id, name FROM subjects")
        by_alias: dict[str, int] = {}
        for sid, name in cur.fetchall():
            cur.execute("SELECT alias FROM subject_aliases WHERE subject_id=%s", (sid,))
            for (a,) in cur.fetchall():
                by_alias[a] = sid
            if name == "A2ZERO":
                by_alias["a2zero"] = sid

        valid = seeded_series_numbers(cur)

        cur.execute(
            """SELECT s.id, s.heading, s.sequence, d.doc_type, j.name
                 FROM document_sections s
                 JOIN documents d ON d.id = s.document_id
                 LEFT JOIN jurisdictions j ON j.id = d.jurisdiction_id
             ORDER BY s.id""")
        rules_cache: dict[tuple, dict | None] = {}
        for sec_id, heading, seq, doc_type, juris in cur.fetchall():
            # registries/ directories are named after the jurisdiction, lowercased and
            # underscored -- "Ann Arbor" -> registries/ann_arbor. There is no slug column
            # to read, and inventing one for this would be a schema change to save a
            # two-line normalisation.
            juris = re.sub(r"[^a-z0-9]+", "_", (juris or "ann arbor").lower()).strip("_")
            ck = (juris, doc_type)
            if ck not in rules_cache:
                rules_cache[ck] = load_section_rules(juris, doc_type)
            rules = rules_cache[ck]
            if rules is None:
                counts["no_rules"] += 1
                continue
            key = section_subject_key(heading, seq == 0, rules, valid)
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


def apply_topics(dsn: str, dry_run: bool = False) -> dict:
    """Write section_topic for every document whose type declares a run. Returns counts.

    Separate from assign() because they answer different questions and one must not gate the
    other: a section can have a topic and no subject (the ideas appendix), or a subject and
    no topic (every strategy section).
    """
    import psycopg

    counts = {"topics": 0, "documents": 0, "no_rules": 0}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT d.id, d.doc_type, j.name FROM documents d
                       LEFT JOIN jurisdictions j ON j.id = d.jurisdiction_id ORDER BY d.id""")
        for doc_id, doc_type, juris in cur.fetchall():
            juris = re.sub(r"[^a-z0-9]+", "_", (juris or "ann arbor").lower()).strip("_")
            rules = load_section_rules(juris, doc_type)
            if not rules or not rules.get("section_topics"):
                counts["no_rules"] += 1
                continue
            cur.execute("""SELECT id, heading FROM document_sections
                           WHERE document_id=%s ORDER BY sequence""", (doc_id,))
            found = section_topics(cur.fetchall(), rules)
            if not found:
                continue
            counts["documents"] += 1
            counts["topics"] += len(found)
            if not dry_run:
                for sec_id, topic in found.items():
                    cur.execute("UPDATE document_sections SET section_topic=%s WHERE id=%s",
                                (topic, sec_id))
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
    ap.add_argument("--topics", action="store_true",
                    help="set section_topic from the doc type's declared runs")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.seed:
        r = seed(a.dsn, a.created_by, a.dry_run)
        print(f"[subjects] {r['created']} created from {r['strategies']} wiki strategies")
    if a.assign:
        r = assign(a.dsn, a.dry_run)
        print(f"[subjects] {r['sections']} section(s) · {r['claims']} claim(s) · "
              f"{r['no_subject']} section(s) left without one · "
              f"{r['no_rules']} section(s) in doc types with no structural route"
              + ("  (dry run — nothing written)" if a.dry_run else ""))
    if a.topics:
        r = apply_topics(a.dsn, a.dry_run)
        print(f"[subjects] {r['topics']} section topic(s) across {r['documents']} document(s)"
              + ("  (dry run — nothing written)" if a.dry_run else ""))
    if not (a.seed or a.assign or a.topics):
        ap.error("nothing to do: pass --seed, --assign and/or --topics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
