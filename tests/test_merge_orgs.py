"""Merging duplicate organisations. Same machinery as subjects, one table over.

The interesting difference is the alias table: resolve_orgs matches claim text against org
NAMES, so tombstoning `SPARK Ann Arbor` would delete a name documents actually print unless
the merge preserves it.
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
    name = f"grapevine_orgmerge_{uuid.uuid4().hex[:10]}"
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


def _org(cur, name, org_type="nonprofit"):
    cur.execute("INSERT INTO orgs (name, org_type) VALUES (%s,%s) RETURNING id",
                (name, org_type))
    return cur.fetchone()[0]


def test_the_duplicate_is_tombstoned_not_deleted(db):
    """resolve_orgs seeds from the read-only wiki; a deleted row comes straight back."""
    with db.cursor() as cur:
        a, b = _org(cur, "Ann Arbor SPARK"), _org(cur, "SPARK Ann Arbor")
        merge(cur, a, b, "tester", entity="org")
        cur.execute("SELECT merged_into_id, merged_by FROM orgs WHERE id=%s", (b,))
        assert cur.fetchone() == (a, "tester")


def test_the_merged_name_survives_as_an_alias(db):
    """The whole point for orgs: `SPARK Ann Arbor` is a name documents print, and the
    resolver reads aliases alongside canonical names."""
    with db.cursor() as cur:
        a, b = _org(cur, "Ann Arbor SPARK"), _org(cur, "SPARK Ann Arbor")
        merge(cur, a, b, "tester", entity="org")
        cur.execute("SELECT alias FROM org_aliases WHERE org_id=%s", (a,))
        assert [r[0] for r in cur.fetchall()] == ["SPARK Ann Arbor"]


def test_a_merged_org_leaves_the_live_view(db):
    with db.cursor() as cur:
        a, b = _org(cur, "Keep"), _org(cur, "Drop")
        merge(cur, a, b, "tester", entity="org")
        cur.execute("SELECT id FROM live_orgs WHERE id = ANY(%s)", ([a, b],))
        assert [r[0] for r in cur.fetchall()] == [a]


def test_claims_are_repointed_at_the_survivor(db):
    with db.cursor() as cur:
        a, b = _org(cur, "U.S. Department of Energy"), \
               _org(cur, "United States Department of Energy", "government")
        cur.execute("""INSERT INTO jurisdictions (ocd_division_id, name, state)
                       VALUES ('ocd-division/test','Test','MI') RETURNING id""")
        jur = cur.fetchone()[0]
        cur.execute("""INSERT INTO documents (doc_type, title, jurisdiction_id)
                       VALUES ('plan','t',%s) RETURNING id""", (jur,))
        doc = cur.fetchone()[0]
        cur.execute("""INSERT INTO claims (verbatim, source_type, document_id, org_id,
                                           position, polarity, extracted_by)
                       VALUES ('DOE awarded a grant.','document',%s,%s,
                               'The DOE awarded a grant.','supports','t')
                       RETURNING id""", (doc, b))
        claim = cur.fetchone()[0]
        r = merge(cur, a, b, "tester", entity="org")
        assert r["moved"]["claims.org_id"] == 1
        cur.execute("SELECT org_id FROM claims WHERE id=%s", (claim,))
        assert cur.fetchone()[0] == a


def test_the_org_chain_guard_uses_the_same_rule_as_subjects(db):
    """One trigger function, generic over TG_TABLE_NAME, so the two cannot drift."""
    with db.cursor() as cur:
        a, b, c = _org(cur, "A"), _org(cur, "B"), _org(cur, "C")
        merge(cur, a, b, "tester", entity="org")
        with pytest.raises(psycopg.errors.RaiseException, match="itself merged"):
            cur.execute("UPDATE orgs SET merged_into_id=%s, merged_by='t', merged_at=now() "
                        "WHERE id=%s", (b, c))


def test_an_org_merge_needs_an_author(db):
    with db.cursor() as cur:
        a, b = _org(cur, "A"), _org(cur, "B")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE orgs SET merged_into_id=%s WHERE id=%s", (a, b))


def test_merging_an_org_into_itself_is_refused(db):
    with db.cursor() as cur:
        a = _org(cur, "A")
        with pytest.raises(SystemExit, match="cannot merge into itself"):
            merge(cur, a, a, "tester", entity="org")
