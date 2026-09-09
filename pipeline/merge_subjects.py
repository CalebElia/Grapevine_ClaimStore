"""Apply a ruled duplicate-subject merge. One survivor, one tombstone, nothing deleted.

WHAT A MERGE IS HERE. Every row that pointed at the duplicate is repointed at the survivor;
the duplicate's name becomes an alias of the survivor, because it is a real name the programme
was published under and a future document is likely to print it; and the duplicate row is
marked `merged_into_id` rather than deleted. See migrations/024 for why deleting does not hold.

THE REPOINTING IS GENERATED, NOT WRITTEN. There are 22 foreign keys into `subjects` today. A
hand-maintained list would silently stop being complete the first time a migration adds the
23rd, and the symptom would be orphaned rows nobody notices. It is read from pg_constraint
every run.

IT REFUSES TO CHOOSE. A merge needs a survivor id and a duplicate id, both supplied by a
person who looked at them. dedup_subjects proposes pairs; it does not rule on them.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

# Subjects and orgs have the same duplicate problem and the same resolution, so they share one
# implementation. What differs is only which table holds the entity and where its other names
# live; a second copy of the FK sweep would be a second thing to forget to update.
ENTITIES = {
    "subject": {"table": "subjects", "aliases": "subject_aliases", "alias_fk": "subject_id"},
    "org":     {"table": "orgs",     "aliases": "org_aliases",     "alias_fk": "org_id"},
}


def _unique_partners(cur, table: str, col: str) -> list[list[str]]:
    """For each UNIQUE/PK constraint containing `col`, the constraint's OTHER columns.

    A junction row is identified by (subject_id, category_id). Repointing subject_id at a
    survivor that already holds the same category_id violates the key -- and because the
    duplicate's row is then redundant rather than lost, the right move is to drop it, not to
    fail the merge. Read from the catalog so a new constraint is handled without an edit.
    """
    cur.execute("""SELECT (SELECT array_agg(a.attname ORDER BY a.attnum)
                             FROM pg_attribute a
                            WHERE a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey))
                     FROM pg_constraint c
                    WHERE c.contype IN ('u','p') AND c.conrelid=%s::regclass""", (table,))
    out = []
    for (cols,) in cur.fetchall():
        if cols and col in cols:
            out.append([x for x in cols if x != col])
    return out


def _fk_columns(cur, table: str) -> list[tuple[str, str]]:
    cur.execute("""SELECT c.conrelid::regclass::text, a.attname
                     FROM pg_constraint c
                     JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
                    WHERE c.confrelid = %s::regclass AND c.contype='f'
                    ORDER BY 1, 2""", (table,))
    return cur.fetchall()


def merge(cur, survivor_id: int, duplicate_id: int, who: str,
          note: str | None = None, dry_run: bool = False, entity: str = "subject") -> dict:
    """Repoint, alias, tombstone. Returns what moved, per table."""
    e = ENTITIES[entity]
    if survivor_id == duplicate_id:
        raise SystemExit(f"[merge] a {entity} cannot merge into itself")

    cur.execute(f"SELECT id, name, merged_into_id FROM {e['table']} WHERE id = ANY(%s)",
                ([survivor_id, duplicate_id],))
    rows = {r[0]: r for r in cur.fetchall()}
    for sid in (survivor_id, duplicate_id):
        if sid not in rows:
            raise SystemExit(f"[merge] no {entity} {sid}")
    if rows[survivor_id][2] is not None:
        raise SystemExit(f"[merge] survivor {survivor_id} is itself merged into "
                         f"{rows[survivor_id][2]}; merge into that one instead")
    if rows[duplicate_id][2] is not None:
        raise SystemExit(f"[merge] {duplicate_id} is already merged into "
                         f"{rows[duplicate_id][2]}; nothing to do")

    moved: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for table, col in _fk_columns(cur, e["table"]):
        if table == e["table"] and col == "merged_into_id":
            continue        # set at the end, deliberately, not swept
        cur.execute(f"SELECT count(*) FROM {table} WHERE {col} = %s", (duplicate_id,))
        if not cur.fetchone()[0]:
            continue
        # COLLISIONS ARE REDUNDANCY, NOT FAILURE. Where a unique key would be violated, the
        # survivor already records the same fact, so the duplicate's row carries nothing.
        guards = []
        for partners in _unique_partners(cur, table, col):
            if not partners:
                continue
            eq = " AND ".join(f"x.{p} IS NOT DISTINCT FROM t.{p}" for p in partners)
            guards.append(f"NOT EXISTS (SELECT 1 FROM {table} x "
                          f"WHERE x.{col} = {survivor_id} AND {eq})")
        where = f"{col} = {duplicate_id}" + ("".join(f" AND {g}" for g in guards))
        cur.execute(f"UPDATE {table} t SET {col} = {survivor_id} WHERE {where}")
        if cur.rowcount:
            moved[f"{table}.{col}"] = cur.rowcount
        if guards:
            cur.execute(f"DELETE FROM {table} WHERE {col} = {duplicate_id}")
            if cur.rowcount:
                dropped[f"{table}.{col}"] = cur.rowcount

    dup_name = rows[duplicate_id][1]
    if True:
        # THE NAME SURVIVES THE ROW. It is a real published name for this programme, and the
        # mention matcher reads aliases -- so merging makes the survivor findable under BOTH
        # spellings instead of losing one.
        cur.execute(f"""INSERT INTO {e['aliases']} ({e['alias_fk']}, alias, alias_type)
                        SELECT %s, %s, 'other'
                         WHERE NOT EXISTS (SELECT 1 FROM {e['aliases']}
                                            WHERE {e['alias_fk']}=%s
                                              AND lower(alias)=lower(%s))""",
                    (survivor_id, dup_name, survivor_id, dup_name))
        cur.execute(f"""UPDATE {e['table']}
                           SET merged_into_id=%s, merged_by=%s, merged_at=now(), merge_note=%s
                         WHERE id=%s""",
                    (survivor_id, who, note, duplicate_id))
    return {"survivor": {"id": survivor_id, "name": rows[survivor_id][1]},
            "duplicate": {"id": duplicate_id, "name": dup_name},
            "moved": moved, "dropped": dropped,
            "rows_moved": sum(moved.values()), "dry_run": dry_run}


def run(merges: list[tuple[int, int]], who: str, dsn: str = DSN,
        note: str | None = None, dry_run: bool = False,
        entity: str = "subject") -> list[dict]:
    """Apply a batch in ONE transaction: a half-merged subject is worse than an unmerged one."""
    import psycopg

    out = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for survivor, dup in merges:
            out.append(merge(cur, survivor, dup, who, note, dry_run, entity))
        # THE WRITES REALLY RUN, THEN ROLL BACK. A dry run that skips them cannot detect a
        # unique-key collision, which is exactly the failure a merge is prone to -- and it
        # would report success right up until the real run aborted mid-batch.
        if dry_run:
            c.rollback()
        else:
            c.commit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merge", action="append", nargs=2, type=int,
                    metavar=("SURVIVOR", "DUPLICATE"), required=True)
    ap.add_argument("--by", default=os.environ.get("USER", "human"))
    ap.add_argument("--note", help="why these are the same programme")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--orgs", action="store_true", help="merge organisations, not subjects")
    a = ap.parse_args()

    for r in run([tuple(m) for m in a.merge], a.by, a.dsn, a.note, a.dry_run,
                 "org" if a.orgs else "subject"):
        s, d = r["survivor"], r["duplicate"]
        print(f"[merge] {d['id']} {d['name']!r}  ->  {s['id']} {s['name']!r}")
        for k, n in sorted(r["moved"].items()):
            print(f"          {n:>4} moved     {k}")
        for k, n in sorted(r["dropped"].items()):
            print(f"          {n:>4} redundant {k}  (survivor already had it)")
        if not r["moved"] and not r["dropped"]:
            print(f"          nothing pointed at it")
    print(f"[merge] {'DRY RUN — rolled back' if a.dry_run else 'committed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
