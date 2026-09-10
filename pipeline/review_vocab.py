"""Read the vocabulary proposals, and rule on them.

WHAT A PROPOSAL IS. Every controlled vocabulary is enforced by a trigger that NEVER rejects an
insert -- rejecting would discard extraction work. On an unrecognised term it stores the
vocabulary's fallback instead, logs a row here, and increments `occurrences`. So a proposal is
the store saying "something wrote a term I do not know, and I kept the row anyway."

THE COST OF NOT READING THEM IS SILENT. 81 orgs were typed 'other' because seventeen proposals
sat unread for weeks; the data looked fine and every query on org_type was quietly wrong. There
was no command for this until now, which is most of why nobody looked.

FOUR RULINGS, AND THE DIFFERENCE MATTERS:

  --approve   The term is a real category the vocabulary lacked. Adds it, and re-points the
              rows that fell back. Growth of the ontology; use it sparingly.
  --map       The term is an existing category spelled differently -- `government-office` for
              `government`, `company` for `business`. Records the mapping and re-points the
              rows. Sixteen of seventeen org_type proposals were this. Approving them instead
              would have left two dialects for one idea.
  --stale     The proposal describes rows that no longer exist. 38 were closed this way: the
              corpus had been re-extracted and the proposals pointed at deleted ids.
  --reject    The term should never have been written. The rows keep the fallback.

WHY --map DOES NOT ADD A TERM. That is the whole point of it: the vocabulary stays one
dialect, and the translation belongs at the boundary where the foreign spelling enters --
a registry the loader reads, like registries/ann_arbor/org_types.json.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")


def pending(cur, vocabulary: str | None = None) -> list[dict]:
    """Open proposals, with whether they still describe anything that exists."""
    cur.execute("""
        SELECT p.id, p.vocabulary, p.proposed_term, p.written_as, p.occurrences,
               p.entity_table, p.entity_id, p.example_verbatim,
               p.first_seen_at::date, p.last_seen_at::date,
               v.fallback_term, v.is_open,
               (SELECT count(*) FROM vocabulary_terms t WHERE t.vocabulary = p.vocabulary)
          FROM vocabulary_proposals p
          JOIN vocabularies v ON v.name = p.vocabulary
         WHERE p.status = 'pending' AND (%s::text IS NULL OR p.vocabulary = %s::text)
         ORDER BY p.occurrences DESC, p.vocabulary""", (vocabulary, vocabulary))
    rows = []
    for (pid, voc, term, written, n, tbl, eid, ex, first, last, fb, is_open, nterms) \
            in cur.fetchall():
        live = None
        if tbl and eid is not None:
            # A proposal whose row is gone describes a corpus that no longer exists. Ruling on
            # it as though it were current is how a changelog gets read as a bug tracker.
            cur.execute(f"SELECT EXISTS (SELECT 1 FROM {tbl} WHERE id = %s)", (eid,))
            live = cur.fetchone()[0]
        rows.append({"id": pid, "vocabulary": voc, "term": term, "written_as": written,
                     "occurrences": n, "entity": f"{tbl}.{eid}" if tbl else None,
                     "row_still_exists": live, "example": ex, "first_seen": str(first),
                     "last_seen": str(last), "fallback": fb, "is_open": is_open,
                     "existing_terms": nterms})
    return rows


def terms_of(cur, vocabulary: str) -> list[str]:
    cur.execute("SELECT term FROM vocabulary_terms WHERE vocabulary=%s ORDER BY term",
                (vocabulary,))
    return [r[0] for r in cur.fetchall()]


def columns_using(cur, vocabulary: str) -> list[tuple[str, str]]:
    """Which (table, column) the trigger guards, so a ruling knows what it re-points."""
    cur.execute("""SELECT c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t
                     JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal""")
    import re
    out = []
    for tbl, d in cur.fetchall():
        m = re.search(r"enforce_vocabulary\('([a-z_]+)',\s*'(\w+)'", d)
        if m and m.group(1) == vocabulary:
            out.append((tbl, m.group(2)))
    return out


def approve(cur, pid: int, description: str, who: str, dry_run: bool = False) -> dict:
    """Add the proposed term, and re-point rows that fell back to it."""
    cur.execute("SELECT vocabulary, proposed_term, written_as FROM vocabulary_proposals "
                "WHERE id=%s AND status='pending'", (pid,))
    if not (r := cur.fetchone()):
        raise SystemExit(f"[vocab] no pending proposal {pid}")
    voc, term, written = r
    # THE TERM GOES IN FIRST. Repointing before approving makes the enforcement trigger fire on
    # a value it still does not know, and it folds every row straight back to the fallback --
    # the update reports rows changed and nothing has changed.
    if not dry_run:
        cur.execute("""INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (voc, term, description, who))
    repointed = _repoint(cur, voc, written, term, dry_run)
    if not dry_run:
        _close(cur, pid, "approved", who)
    return {"vocabulary": voc, "term": term, "written_as": written,
            "repointed": repointed, "action": "approve",
            "warnings": _check_scope(cur, pid, repointed)}


def map_to(cur, pid: int, existing: str, who: str, dry_run: bool = False) -> dict:
    """Declare the proposed term a spelling of an existing one. Adds no term."""
    cur.execute("SELECT vocabulary, proposed_term, written_as FROM vocabulary_proposals "
                "WHERE id=%s AND status='pending'", (pid,))
    if not (r := cur.fetchone()):
        raise SystemExit(f"[vocab] no pending proposal {pid}")
    voc, term, written = r
    if existing not in terms_of(cur, voc):
        raise SystemExit(f"[vocab] {existing!r} is not an approved {voc} term. "
                         f"Approved: {', '.join(terms_of(cur, voc))}")
    # MAPPING A UNIT DOES NOT CONVERT THE VALUE. `weeks` -> `days` rewrites the unit column
    # and leaves value_low alone, so "48 weeks" silently becomes 48 days. That exact bug has
    # already happened once in this store. A unit that is a different SCALE of an approved one
    # must be approved, not mapped -- mapping is for a different SPELLING of the same thing.
    if voc.endswith("_unit") and existing != term:
        raise SystemExit(
            f"[vocab] refusing to map a unit. {term!r} -> {existing!r} would rewrite the unit "
            f"and leave the number alone, so '48 {term}' becomes 48 {existing}. If {term!r} is "
            f"a real unit, use --approve. If the values genuinely need converting, do that "
            f"first in pipeline/units.py where the conversion is tested.")
    repointed = _repoint(cur, voc, written, existing, dry_run)
    if not dry_run:
        _close(cur, pid, f"mapped_to:{existing}", who)
    return {"vocabulary": voc, "term": term, "written_as": written, "mapped_to": existing,
            "repointed": repointed, "action": "map",
            "warnings": _check_scope(cur, pid, repointed)}


def _check_scope(cur, pid: int, moved: dict) -> list[str]:
    """Warn when a ruling would move more rows than the proposal ever saw.

    _repoint moves EVERY row sitting on the fallback, because that is the only marker a
    fallback leaves. If ten rows hold 'other' and only seven of them fell back from this
    term, approving it relabels three rows that legitimately meant "none of the above" --
    silently, and in the direction of looking more complete than the corpus is.

    Not a refusal: on an open vocabulary the fallback rows usually ARE all from one term, and
    on these three they matched exactly. But the reviewer should be told which case they are in.
    """
    cur.execute("SELECT occurrences FROM vocabulary_proposals WHERE id=%s", (pid,))
    seen = (cur.fetchone() or [0])[0]
    total = sum(moved.values())
    if total > seen:
        return [f"this ruling moves {total} row(s) but the term was only seen {seen} time(s); "
                f"{total - seen} row(s) may have meant the fallback genuinely. Check before "
                f"committing -- --dry-run shows the counts."]
    return []


def _repoint(cur, vocabulary: str, from_value: str, to_value: str, dry_run: bool) -> dict:
    """Move rows sitting on the fallback onto the ruled term.

    ONLY ROWS HOLDING THE FALLBACK. A row with any other value was written with a term the
    vocabulary already knew, and a later ruling about a different spelling does not get to
    reinterpret it.
    """
    moved = {}
    for table, column in columns_using(cur, vocabulary):
        cur.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (from_value,))
        n = cur.fetchone()[0]
        if not n:
            continue
        moved[f"{table}.{column}"] = n
        if not dry_run:
            cur.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s",
                        (to_value, from_value))
    return moved


def write_migration(ruling: dict, who: str, root: Path | None = None) -> Path:
    """Record a ruling as a migration, so it survives a rebuild.

    WITHOUT THIS THE TOOL IS THE BUG IT WAS BUILT TO FIND. Rulings write to the live database;
    `scripts/db.sh reset` applies only schema/, and tests/test_schema_drift.py compares
    canonical against canonical-plus-migrations and never against live -- deliberately, so it
    does not depend on a mutable thing. A term added by hand is therefore invisible to every
    check in the repo, exactly as mention_method.core_phrase_verified was: live, used by the
    code, claimed by no file, and gone the moment anyone rebuilt.
    """
    root = root or Path(__file__).parent.parent
    mig = root / "migrations"
    nxt = max((int(f.name[:3]) for f in mig.glob("[0-9][0-9][0-9]_*.sql")), default=0) + 1
    voc, term = ruling["vocabulary"], ruling["term"]
    slug = f"{voc}_{term}".lower().replace("-", "_").replace(" ", "_")[:40]
    path = mig / f"{nxt:03d}_vocab_{slug}.sql"

    if ruling["action"] == "approve":
        body = (f"-- Approved by {who}: {voc}.{term}\n--\n"
                f"-- {ruling.get('description', '')}\n\n"
                f"INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by)\n"
                f"VALUES ({_q(voc)}, {_q(term)}, {_q(ruling.get('description',''))}, "
                f"{_q(who)})\nON CONFLICT DO NOTHING;\n")
    else:
        to = ruling["mapped_to"]
        body = (f"-- Mapped by {who}: {voc}.{term} is a spelling of {to}, NOT a new term.\n"
                f"-- Adding it would leave the vocabulary with two dialects for one idea.\n"
                f"-- Fix the boundary too, or the same spelling arrives again next ingest.\n\n")
    # MATCH WHAT THE ROWS ACTUALLY HOLD. A row that fell back holds the FALLBACK, not the
    # term that was refused -- that term was never stored anywhere. Emitting
    # `WHERE unit = 'weeks'` produced a migration that ran clean and did nothing, which is
    # worse than one that fails.
    held = ruling.get("written_as") or term
    for tc, n in (ruling.get("repointed") or {}).items():
        table, col = tc.split(".")
        target = ruling.get("mapped_to", term)
        body += (f"-- {n} row(s) were sitting on {held!r}\n"
                 f"UPDATE {table} SET {col} = {_q(target)} WHERE {col} = {_q(held)};\n")
    body += (f"\nUPDATE vocabulary_proposals SET status = {_q(ruling['action'])}, "
             f"resolved_by = {_q(who)}, resolved_at = now()\n"
             f" WHERE vocabulary = {_q(voc)} AND proposed_term = {_q(term)} "
             f"AND status = 'pending';\n")
    path.write_text(body)
    return path


def _q(v: str) -> str:
    return "'" + (v or "").replace("'", "''") + "'"


def _close(cur, pid: int, status: str, who: str) -> None:
    cur.execute("""UPDATE vocabulary_proposals
                      SET status=%s, resolved_by=%s, resolved_at=now() WHERE id=%s""",
                (status, who, pid))


def close_only(cur, pid: int, status: str, who: str) -> dict:
    cur.execute("SELECT vocabulary, proposed_term FROM vocabulary_proposals "
                "WHERE id=%s AND status='pending'", (pid,))
    if not (r := cur.fetchone()):
        raise SystemExit(f"[vocab] no pending proposal {pid}")
    _close(cur, pid, status, who)
    return {"vocabulary": r[0], "term": r[1], "action": status}


def main() -> int:
    import psycopg

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vocabulary", help="show only this vocabulary")
    ap.add_argument("--approve", type=int, metavar="PROPOSAL_ID")
    ap.add_argument("--as", dest="as_term", metavar="TERM",
                    help="with --map: the existing term this is a spelling of")
    ap.add_argument("--description", help="with --approve: what the new term means")
    ap.add_argument("--map", type=int, metavar="PROPOSAL_ID")
    ap.add_argument("--stale", type=int, metavar="PROPOSAL_ID")
    ap.add_argument("--reject", type=int, metavar="PROPOSAL_ID")
    ap.add_argument("--by", default=os.environ.get("USER", "human"))
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(a.dsn) as c, c.cursor() as cur:
        if a.approve:
            if not a.description:
                raise SystemExit("[vocab] --approve needs --description: a term with no "
                                 "definition is the next reviewer's problem")
            r = approve(cur, a.approve, a.description, a.by, a.dry_run)
        elif a.map:
            if not a.as_term:
                raise SystemExit("[vocab] --map needs --as TERM")
            r = map_to(cur, a.map, a.as_term, a.by, a.dry_run)
        elif a.stale or a.reject:
            r = close_only(cur, a.stale or a.reject, "stale" if a.stale else "rejected", a.by)
        else:
            rows = pending(cur, a.vocabulary)
            print(f"[vocab] {len(rows)} pending proposal(s)\n")
            for x in rows:
                gone = "" if x["row_still_exists"] is not False else \
                    "   <- the row it describes is GONE; likely --stale"
                print(f"  #{x['id']}  {x['vocabulary']}.{x['term']!r}   seen "
                      f"{x['occurrences']}x   {x['first_seen']}..{x['last_seen']}{gone}")
                print(f"        stored instead as {x['written_as']!r} "
                      f"(the vocabulary's fallback), {x['existing_terms']} approved terms")
                if x["entity"]:
                    print(f"        first written by {x['entity']}")
                if x["example"]:
                    print(f"        e.g. {x['example'][:90]}")
                print(f"        approved: {', '.join(terms_of(cur, x['vocabulary']))}")
                print()
            if rows:
                print("  rule with:  --approve N --description '...'  |  --map N --as TERM"
                      "  |  --stale N  |  --reject N")
            return 0
        if a.dry_run:
            c.rollback()
            print(f"[vocab] DRY RUN — rolled back")
        else:
            c.commit()
        for w in r.get("warnings", []):
            print(f"[vocab] WARNING: {w}")
        print(f"[vocab] {r}")
        if not a.dry_run and r["action"] in ("approve", "map"):
            r.setdefault("description", a.description or "")
            m = write_migration(r, a.by)
            print(f"[vocab] recorded in {m.relative_to(Path(__file__).parent.parent)}")
            print(f"[vocab] fold the term into schema/vocabularies.sql too, or a rebuilt "
                  f"database will not have it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
