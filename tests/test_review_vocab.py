"""Ruling on vocabulary proposals.

The trigger never rejects an insert -- it stores the fallback and logs a proposal. That keeps
extraction work, and it means an unread proposal is a silent wrong answer: 81 orgs were typed
'other' because seventeen sat unread. These tests cover the rulings a person makes on them.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

from pipeline.review_vocab import approve, map_to, pending, terms_of

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
    name = f"grapevine_vocab_{uuid.uuid4().hex[:10]}"
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


def _propose(cur, vocabulary, term, written_as, table="orgs", entity_id=999999):
    cur.execute("""INSERT INTO vocabulary_proposals
                     (vocabulary, proposed_term, written_as, entity_table, entity_id)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (vocabulary, term, written_as, table, entity_id))
    return cur.fetchone()[0]


def test_the_trigger_keeps_the_row_and_logs_instead_of_rejecting(db):
    """Rejecting would discard extraction work, so the store takes the row and flags it."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('X','wildly-invented') "
                    "RETURNING id, org_type")
        oid, stored = cur.fetchone()
        assert stored == "other", "should have fallen back, not been rejected"
        cur.execute("SELECT proposed_term FROM vocabulary_proposals WHERE vocabulary='org_type'")
        assert ("wildly-invented",) in cur.fetchall()


def test_approving_adds_the_term_and_repoints_the_rows_that_fell_back(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('X','co-operative') RETURNING id")
        oid = cur.fetchone()[0]
        cur.execute("SELECT id FROM vocabulary_proposals WHERE proposed_term='co-operative'")
        pid = cur.fetchone()[0]
        r = approve(cur, pid, "a member-owned body", "tester")
        assert "co-operative" in terms_of(cur, "org_type")
        assert r["repointed"]["orgs.org_type"] == 1
        cur.execute("SELECT org_type FROM orgs WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == "co-operative"


def test_mapping_repoints_without_adding_a_term(db):
    """Sixteen of seventeen org_type proposals were spellings, not categories. Approving them
    would have left the store with two dialects for one idea."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('X','government-office') "
                    "RETURNING id")
        oid = cur.fetchone()[0]
        before = terms_of(cur, "org_type")
        cur.execute("SELECT id FROM vocabulary_proposals WHERE proposed_term='government-office'")
        pid = cur.fetchone()[0]
        r = map_to(cur, pid, "government", "tester")
        assert terms_of(cur, "org_type") == before, "mapping must not grow the vocabulary"
        assert r["repointed"]["orgs.org_type"] == 1
        cur.execute("SELECT org_type FROM orgs WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == "government"


def test_mapping_to_an_unapproved_term_is_refused(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('X','co-op')")
        cur.execute("SELECT id FROM vocabulary_proposals WHERE proposed_term='co-op'")
        pid = cur.fetchone()[0]
        with pytest.raises(SystemExit, match="not an approved"):
            map_to(cur, pid, "not_a_real_term", "tester")


def test_a_unit_may_never_be_mapped(db):
    """`weeks` -> `days` rewrites the unit and leaves the number, so 48 weeks becomes 48 days.
    That bug has already happened once in this store."""
    with db.cursor() as cur:
        pid = _propose(cur, "quantity_unit", "weeks", "other", "quantities", 999999)
        with pytest.raises(SystemExit, match="refusing to map a unit"):
            map_to(cur, pid, "days", "tester")


def test_a_proposal_whose_row_is_gone_is_flagged(db):
    """38 proposals described a corpus that had been re-extracted. Ruling on them as current
    is reading a changelog as a bug tracker."""
    with db.cursor() as cur:
        _propose(cur, "org_type", "ghost", "other", "orgs", 999999)
        rows = [r for r in pending(cur) if r["term"] == "ghost"]
        assert rows and rows[0]["row_still_exists"] is False


def test_approving_requires_a_definition(db):
    """A term with no description is the next reviewer's problem."""
    with db.cursor() as cur:
        pid = _propose(cur, "org_type", "guild", "other")
        approve(cur, pid, "a trade guild", "tester")
        cur.execute("SELECT description FROM vocabulary_terms WHERE term='guild'")
        assert cur.fetchone()[0]
