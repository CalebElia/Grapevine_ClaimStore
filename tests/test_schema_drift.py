"""The canonical schema must already contain everything the migrations do.

WHY THIS EXISTS. `scripts/db.sh reset` applies ONLY schema/claim_store.sql and
schema/vocabularies.sql -- migrations are never replayed. So a migration whose DDL was never
folded back into the canonical files is not merely untidy: a rebuilt database silently lacks
it. When this test was written, seventeen migrations were unfolded and nine whole tables
existed only in migrations, `document_sections` among them -- the table every claim joins
through. `./scripts/db.sh reset` would have produced a database unable to hold the corpus.

Nothing caught it because tests/test_schema_runtime.py builds from the canonical files and
then exercises only the objects it happens to touch. Absence is invisible to a test that never
looks.

WHAT IS COMPARED, AND WHY NOT THE LIVE DATABASE. Two throwaway databases, both built from
files in this repo: one from the canonical schema alone, one from the canonical schema plus
every migration in order. Anything the second has and the first lacks is drift. Comparing
against the live local database instead would make the suite depend on a mutable thing that
someone may have hand-edited -- which is exactly how `mention_method.core_phrase_verified`
came to exist live with no file anywhere claiming it.

MIGRATIONS MUST THEREFORE STAY REPLAYABLE on top of an up-to-date canonical schema: additive,
guarded with IF NOT EXISTS / OR REPLACE / DROP ... IF EXISTS. That is a real constraint this
test imposes, and a reasonable one -- a migration that cannot be applied twice cannot be
trusted after a partial failure either.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

ROOT = Path(__file__).parent.parent
SCHEMA_DIR = ROOT / "schema"
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
HOST = os.environ.get("GRAPEVINE_PGHOST", "/tmp")
PORT = os.environ.get("GRAPEVINE_PGPORT", "5433")
USER = os.environ.get("GRAPEVINE_PGUSER", "grapevine")

# Migration 012 inserts an alias row, which needs a jurisdiction to hang off. Seeding it is
# not part of the schema; it is a prerequisite for replaying data-bearing migrations at all.
_SEED = ("INSERT INTO jurisdictions (ocd_division_id, name, state) "
         "VALUES ('ocd-division/country:us/state:mi/place:ann_arbor','Ann Arbor','MI')")

_INVENTORY = {
    "tables": """SELECT table_name FROM information_schema.tables
                  WHERE table_schema='public' AND table_type='BASE TABLE'""",
    "columns": """SELECT table_name||'.'||column_name FROM information_schema.columns
                   WHERE table_schema='public'""",
    "views": """SELECT table_name FROM information_schema.views WHERE table_schema='public'""",
    "triggers": """SELECT c.relname||'.'||t.tgname FROM pg_trigger t
                     JOIN pg_class c ON c.oid=t.tgrelid
                     JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE NOT t.tgisinternal AND n.nspname='public'""",
    "indexes": """SELECT tablename||'.'||indexname FROM pg_indexes WHERE schemaname='public'""",
    "vocabularies": "SELECT name FROM vocabularies",
    "vocabulary_terms": "SELECT vocabulary||'.'||term FROM vocabulary_terms",
}


def _build(admin, apply_migrations: bool) -> dict[str, set[str]]:
    name = f"grapevine_drift_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        c = psycopg.connect(f"host={HOST} port={PORT} user={USER} dbname={name}",
                            autocommit=True)
        for f in ("claim_store.sql", "vocabularies.sql"):
            c.execute((SCHEMA_DIR / f).read_text())
        if apply_migrations:
            c.execute(_SEED)
            for m in MIGRATIONS:
                c.execute(m.read_text())
        out = {k: {r[0] for r in c.execute(q).fetchall()} for k, q in _INVENTORY.items()}
        c.close()
        return out
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def inventories():
    try:
        admin = psycopg.connect(f"host={HOST} port={PORT} user={USER} dbname=postgres",
                                autocommit=True, connect_timeout=5)
    except Exception as exc:                                      # pragma: no cover
        pytest.skip(f"no Postgres at {HOST}:{PORT} — {exc}")
    try:
        yield _build(admin, False), _build(admin, True)
    finally:
        admin.close()


@pytest.mark.parametrize("kind", list(_INVENTORY))
def test_the_canonical_schema_is_not_missing_what_the_migrations_add(inventories, kind):
    """One failure per object kind, so the report names what to fold in rather than one blob."""
    canonical, with_migrations = inventories
    missing = sorted(with_migrations[kind] - canonical[kind])
    assert not missing, (
        f"{len(missing)} {kind} exist only after migrations, so `./scripts/db.sh reset` "
        f"builds a database without them — fold them into schema/claim_store.sql or "
        f"schema/vocabularies.sql:\n  " + "\n  ".join(missing))


def test_every_migration_replays_on_the_current_canonical_schema(inventories):
    """If the fixture built at all, every migration applied cleanly on top of canonical.

    Stated as its own test because it is a real constraint, not an accident: migrations have
    to stay additive and guarded, or this suite cannot check drift and a partial failure
    cannot be re-run.
    """
    _, with_migrations = inventories
    assert with_migrations["tables"], "migrations produced no tables — the fixture is broken"
