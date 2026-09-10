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


def test_a_ruling_that_moves_more_rows_than_it_saw_is_flagged(db):
    """_repoint moves every row on the fallback, because that is the only marker a fallback
    leaves. If more rows hold it than the term was ever seen, some meant it genuinely."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('A','co-operative')")
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('B','other')")
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('C','other')")
        cur.execute("SELECT id FROM vocabulary_proposals WHERE proposed_term='co-operative'")
        pid = cur.fetchone()[0]
        r = approve(cur, pid, "member-owned", "tester")
        assert r["repointed"]["orgs.org_type"] == 3      # all three fallback rows moved
        assert r["warnings"], "moving 3 rows for a term seen once should warn"
        assert "seen 1 time" in r["warnings"][0]


def test_no_warning_when_the_counts_match(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO orgs (name, org_type) VALUES ('A','co-operative')")
        cur.execute("SELECT id FROM vocabulary_proposals WHERE proposed_term='co-operative'")
        pid = cur.fetchone()[0]
        r = approve(cur, pid, "member-owned", "tester")
        assert r["warnings"] == []


# ── a ruling has to leave a file behind ───────────────────────────────────────────────────
#
# Rulings write to the live database. scripts/db.sh reset applies only schema/, and the drift
# test compares canonical against canonical-plus-migrations, never against live. So a term
# added by hand is invisible to every check in the repo and disappears on the next rebuild --
# which is exactly what happened to mention_method.core_phrase_verified.

def _emit(tmp_path, ruling):
    from pipeline.review_vocab import write_migration
    (tmp_path / "migrations").mkdir(exist_ok=True)
    return write_migration(ruling, "tester", tmp_path).read_text()


def test_an_approval_emits_the_term(tmp_path):
    sql = _emit(tmp_path, {"vocabulary": "quantity_unit", "term": "weeks",
                           "written_as": "other", "action": "approve",
                           "description": "A duration in weeks.", "repointed": {}})
    assert "INSERT INTO vocabulary_terms" in sql and "'weeks'" in sql


def test_a_mapping_emits_no_term(tmp_path):
    """Adding it would leave the vocabulary with two dialects for one idea."""
    sql = _emit(tmp_path, {"vocabulary": "vote_value", "term": "recused",
                           "written_as": "recused", "mapped_to": "recuse",
                           "action": "map", "repointed": {}})
    assert "INSERT INTO vocabulary_terms" not in sql


def test_the_repoint_matches_what_the_rows_actually_hold(tmp_path):
    """A row that fell back holds the FALLBACK, not the term that was refused -- that term was
    never stored anywhere. Emitting `WHERE unit = 'weeks'` produced a migration that ran clean
    and did nothing, which is worse than one that fails."""
    sql = _emit(tmp_path, {"vocabulary": "quantity_unit", "term": "weeks",
                           "written_as": "other", "action": "approve",
                           "description": "d", "repointed": {"quantities.unit": 1}})
    assert "WHERE unit = 'other'" in sql
    assert "WHERE unit = 'weeks'" not in sql, "that WHERE would match nothing"


def test_a_mapping_repoints_from_the_raw_value_when_there_is_no_fallback(tmp_path):
    """vote_value has a NULL fallback, so the trigger stored 'recused' verbatim."""
    sql = _emit(tmp_path, {"vocabulary": "vote_value", "term": "recused",
                           "written_as": "recused", "mapped_to": "recuse", "action": "map",
                           "repointed": {"votes.vote_value": 9}})
    assert "SET vote_value = 'recuse' WHERE vote_value = 'recused'" in sql


def test_the_emitted_sql_quotes_apostrophes(tmp_path):
    sql = _emit(tmp_path, {"vocabulary": "org_type", "term": "x", "written_as": "other",
                           "action": "approve", "description": "the City's own body",
                           "repointed": {}})
    assert "the City''s own body" in sql


def test_the_migration_number_follows_the_ones_already_there(tmp_path):
    from pipeline.review_vocab import write_migration
    (tmp_path / "migrations").mkdir(exist_ok=True)
    (tmp_path / "migrations" / "041_something.sql").write_text("-- x")
    p = write_migration({"vocabulary": "org_type", "term": "y", "written_as": "other",
                         "action": "approve", "description": "d", "repointed": {}},
                        "tester", tmp_path)
    assert p.name.startswith("042_")
