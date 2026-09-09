"""Merging one duplicate subject into another, against a real Postgres.

These cannot be checked statically: the chain guard is a trigger, the collision handling
depends on real unique constraints, and the tombstone only matters because a later re-seed
queries the row it leaves behind.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

from pipeline.merge_subjects import merge

SCHEMA_DIR = Path(__file__).parent.parent / "schema"
HOST = os.environ.get("GRAPEVINE_PGHOST", "/tmp")
PORT = os.environ.get("GRAPEVINE_PGPORT", "5433")
USER = os.environ.get("GRAPEVINE_PGUSER", "grapevine")


@pytest.fixture()
def db():
    try:
        admin = psycopg.connect(f"host={HOST} port={PORT} user={USER} dbname=postgres",
                                autocommit=True, connect_timeout=5)
    except Exception as exc:                                      # pragma: no cover
        pytest.skip(f"no Postgres at {HOST}:{PORT} — {exc}")
    name = f"grapevine_merge_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        conn = psycopg.connect(f"host={HOST} port={PORT} user={USER} dbname={name}",
                               autocommit=True)
        for f in ("claim_store.sql", "vocabularies.sql"):
            conn.execute((SCHEMA_DIR / f).read_text())
        yield conn
        conn.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _subject(cur, name):
    cur.execute("INSERT INTO subjects (name, subject_kind, created_by) "
                "VALUES (%s,'initiative','test') RETURNING id", (name,))
    return cur.fetchone()[0]


def test_the_duplicate_is_tombstoned_not_deleted(db):
    """Deleting it lets pipeline/initiatives.py recreate it from the wiki page that still
    exists, orphaning every row the merge just repointed."""
    with db.cursor() as cur:
        a, b = _subject(cur, "Electrify City Fleet"), _subject(cur, "City Fleet Electrification")
        merge(cur, a, b, "tester")
        cur.execute("SELECT merged_into_id, merged_by FROM subjects WHERE id=%s", (b,))
        assert cur.fetchone() == (a, "tester")


def test_the_duplicates_name_becomes_an_alias(db):
    """It is a real published name for the programme; a document may well print it."""
    with db.cursor() as cur:
        a, b = _subject(cur, "Electrify City Fleet"), _subject(cur, "City Fleet Electrification")
        merge(cur, a, b, "tester")
        cur.execute("SELECT alias FROM subject_aliases WHERE subject_id=%s", (a,))
        assert [r[0] for r in cur.fetchall()] == ["City Fleet Electrification"]


def test_a_merged_subject_leaves_the_live_view(db):
    with db.cursor() as cur:
        a, b = _subject(cur, "Keep"), _subject(cur, "Drop")
        merge(cur, a, b, "tester")
        cur.execute("SELECT id FROM live_subjects WHERE id = ANY(%s)", ([a, b],))
        assert [r[0] for r in cur.fetchall()] == [a]


def test_a_merge_may_not_point_at_a_tombstone(db):
    """One hop, always — otherwise resolving a subject needs a recursive CTE."""
    with db.cursor() as cur:
        a, b, c = _subject(cur, "A"), _subject(cur, "B"), _subject(cur, "C")
        merge(cur, a, b, "tester")
        with pytest.raises(psycopg.errors.RaiseException, match="itself merged"):
            cur.execute("UPDATE subjects SET merged_into_id=%s, merged_by='t', "
                        "merged_at=now() WHERE id=%s", (b, c))


def test_a_survivor_cannot_itself_be_merged(db):
    with db.cursor() as cur:
        a, b, c = _subject(cur, "A"), _subject(cur, "B"), _subject(cur, "C")
        merge(cur, a, b, "tester")
        with pytest.raises(psycopg.errors.RaiseException, match="survivor of another merge"):
            cur.execute("UPDATE subjects SET merged_into_id=%s, merged_by='t', "
                        "merged_at=now() WHERE id=%s", (c, a))


def test_a_merge_needs_an_author(db):
    """Merging is an identity decision, and identity decisions are attributed here."""
    with db.cursor() as cur:
        a, b = _subject(cur, "A"), _subject(cur, "B")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE subjects SET merged_into_id=%s WHERE id=%s", (a, b))


def test_a_colliding_junction_row_is_dropped_not_fatal(db):
    """34 and 185 both linked to category 10. Repointing would violate the primary key, and
    the duplicate's row carries nothing the survivor does not already have."""
    with db.cursor() as cur:
        a, b = _subject(cur, "A"), _subject(cur, "B")
        cur.execute("INSERT INTO frameworks (name) VALUES ('F') RETURNING id")
        fw = cur.fetchone()[0]
        cur.execute("INSERT INTO framework_categories (framework_id, code, name) "
                    "VALUES (%s,'c1','C1') RETURNING id", (fw,))
        shared = cur.fetchone()[0]
        cur.execute("INSERT INTO framework_categories (framework_id, code, name) "
                    "VALUES (%s,'c2','C2') RETURNING id", (fw,))
        only_b = cur.fetchone()[0]
        for sid, cid in ((a, shared), (b, shared), (b, only_b)):
            cur.execute("INSERT INTO subject_framework_categories "
                        "(subject_id, category_id, assigned_by) VALUES (%s,%s,'t')", (sid, cid))

        r = merge(cur, a, b, "tester")
        assert r["moved"]["subject_framework_categories.subject_id"] == 1     # only_b
        assert r["dropped"]["subject_framework_categories.subject_id"] == 1   # shared
        cur.execute("SELECT category_id FROM subject_framework_categories "
                    "WHERE subject_id=%s ORDER BY 1", (a,))
        assert [x[0] for x in cur.fetchall()] == sorted([shared, only_b])
        cur.execute("SELECT count(*) FROM subject_framework_categories WHERE subject_id=%s", (b,))
        assert cur.fetchone()[0] == 0


def test_merging_a_subject_into_itself_is_refused(db):
    with db.cursor() as cur:
        a = _subject(cur, "A")
        with pytest.raises(SystemExit, match="cannot merge into itself"):
            merge(cur, a, a, "tester")


def test_merging_an_already_merged_subject_is_refused(db):
    with db.cursor() as cur:
        a, b = _subject(cur, "A"), _subject(cur, "B")
        merge(cur, a, b, "tester")
        with pytest.raises(SystemExit, match="already merged"):
            merge(cur, a, b, "tester")
