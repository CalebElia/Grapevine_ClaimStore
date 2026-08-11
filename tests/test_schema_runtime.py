"""Runtime verification: apply the DDL to a real Postgres and exercise its guarantees.

test_schema_structure.py reads the SQL as text. These tests EXECUTE it. Everything
asserted here was unverifiable by static inspection — a trigger that raises on a
typo, a view referencing a column that does not exist, a CHECK that does not fire.

Each run builds a throwaway database from scratch, so the DDL is proven to apply
cleanly every time rather than once. Skips (rather than fails) when no server is
reachable, so the suite still runs on a machine without Postgres.

Server setup, one time:
    conda create -y -n grapevine-db -c conda-forge postgresql=18.4 pgvector
    ./scripts/db.sh start

The connection is intentionally a local unix socket with trust auth on a
non-default port — no password, no TCP listener, nothing exposed off the machine.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

SCHEMA_DIR = Path(__file__).parent.parent / "schema"
HOST = os.environ.get("GRAPEVINE_PGHOST", "/tmp")
PORT = os.environ.get("GRAPEVINE_PGPORT", "5433")
USER = os.environ.get("GRAPEVINE_PGUSER", "grapevine")


def _admin_dsn(db="postgres"):
    return f"host={HOST} port={PORT} user={USER} dbname={db}"


@pytest.fixture(scope="module")
def db():
    """A fresh database with the full schema applied. Dropped afterwards."""
    try:
        admin = psycopg.connect(_admin_dsn(), autocommit=True, connect_timeout=5)
    except Exception as exc:                                      # pragma: no cover
        pytest.skip(f"no Postgres at {HOST}:{PORT} — {exc}")

    name = f"grapevine_test_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        conn = psycopg.connect(_admin_dsn(name), autocommit=True)
        for f in ("claim_store.sql", "vocabularies.sql"):
            conn.execute((SCHEMA_DIR / f).read_text())
        _load_fixtures(conn)
        yield conn
        conn.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _load_fixtures(c):
    c.execute("""
    INSERT INTO jurisdictions (id, ocd_division_id, name, state, legistar_client,
                               fiscal_year_start_month)
    VALUES (1,'ocd-division/country:us/state:mi/place:ann_arbor','Ann Arbor','MI','a2gov',7);
    INSERT INTO bodies (id, jurisdiction_id, legistar_body_id, name, classification, authority_type)
    VALUES (1,1,1385,'Sustainability Commission','commission','advisory'),
           (2,1,220,'Energy Commission','commission','advisory'),
           (3,1,222,'Environmental Commission','commission','advisory');
    INSERT INTO events (id, body_id, event_date, meeting_kind) VALUES (1,1,'2026-03-10','regular');
    INSERT INTO media_assets (id, event_id, host, external_id, url)
    VALUES (1,1,'youtube','lWvRVUMyLP4','https://youtube.com/watch?v=lWvRVUMyLP4');
    INSERT INTO segments (id, event_id, sequence, start_ms, end_ms, segment_kind, extraction_tier)
    VALUES (1,1,0,0,145000,'roll_call','C');
    INSERT INTO utterances (id, segment_id, media_asset_id, sequence, start_ms, end_ms, text, actor_capacity)
    VALUES (1,1,1,0,30068,32971,'the ARCA governs the structure','official');
    INSERT INTO persons (id, full_name, is_public_figure)
    VALUES (1,'Public Official',TRUE),(2,'Private Resident',FALSE);
    """)
    # Inserting explicit ids does NOT advance the SERIAL sequence, so the next
    # auto-generated id would collide with the fixture's. The real ingest never
    # supplies ids, so this realigns the fixture with production behaviour.
    for t in ("jurisdictions", "bodies", "events", "media_assets",
              "segments", "utterances", "persons"):
        c.execute(
            f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
            f"COALESCE((SELECT max(id) FROM {t}), 1))"
        )


# ------------------------------------------------------------------ Rule 1
def test_unknown_term_is_stored_logged_and_not_rejected(db):
    """The whole point of the semi-open ontology. A hard reject would discard
    extraction work, which is what v1's enum coercion did badly."""
    db.execute("INSERT INTO barriers (barrier_class, barrier_text, verbatim) "
               "VALUES ('interdimensional_flux','t','the flux capacitor failed')")
    stored = db.execute("SELECT barrier_class FROM barriers").fetchone()[0]
    assert stored == "other", "fallback_term was not substituted"

    row = db.execute("SELECT vocabulary, written_as, occurrences FROM vocabulary_proposals "
                     "WHERE proposed_term='interdimensional_flux'").fetchone()
    assert row == ("barrier_class", "other", 1)


def test_recurrence_increments_rather_than_duplicating(db):
    """Recurrence is the promotion signal — a term proposed thirty times is a real
    gap in the vocabulary, one proposed once is noise."""
    db.execute("INSERT INTO barriers (barrier_class, barrier_text, verbatim) "
               "VALUES ('interdimensional_flux','t2','more words')")
    n = db.execute("SELECT occurrences FROM vocabulary_proposals "
                   "WHERE proposed_term='interdimensional_flux'").fetchone()[0]
    assert n == 2


def test_approved_terms_pass_through_untouched(db):
    db.execute("INSERT INTO barriers (barrier_class, barrier_text, verbatim) "
               "VALUES ('legal','t','Michigan lacks enabling legislation')")
    assert db.execute("SELECT count(*) FROM barriers WHERE barrier_class='legal'").fetchone()[0] == 1


def test_pending_vocab_view_surfaces_the_proposal(db):
    rows = db.execute("SELECT entity_table, proposed_term FROM v_entities_with_pending_vocab").fetchall()
    assert ("barriers", "interdimensional_flux") in rows


# ------------------------------------------------------------------ privacy
def test_public_figure_may_be_voiceprinted(db):
    db.execute("INSERT INTO person_voiceprints (person_id, embedding_model, enrolled_from) "
               "VALUES (1,'wespeaker','roll_call')")
    assert db.execute("SELECT count(*) FROM person_voiceprints").fetchone()[0] == 1


def test_private_individual_may_not_be_voiceprinted(db):
    """A persistent voiceprint index of private residents who spoke at public
    comment is a materially different artifact from one covering elected officials."""
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("INSERT INTO person_voiceprints (person_id, embedding_model, enrolled_from) "
                   "VALUES (2,'wespeaker','roll_call')")


# --------------------------------------------------------------- provenance
def test_empty_verbatim_is_rejected(db):
    """NOT NULL accepts ''. v1's assembler wrote exactly that for 71 turns and every
    one received a fabricated claim."""
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO claims (position,polarity,source_type,utterance_id,verbatim,extracted_by) "
                   "VALUES ('x','support','video_utterance',1,'','m')")


def test_claim_without_any_source_is_rejected(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO claims (position,polarity,source_type,verbatim,extracted_by) "
                   "VALUES ('x','support','video_utterance','some words','m')")


def test_claim_with_no_subject_is_allowed(db):
    """The cold-start fix. Subjects are discovered BY extracting, so requiring one
    at insert time deadlocks the first extraction pass."""
    db.execute("""INSERT INTO claims (id,position,polarity,source_type,utterance_id,
                  verbatim,extracted_by,asserted_date_text,asserted_precision,
                  asserted_calendar,asserted_start,asserted_end)
                  VALUES (1,'Franchise includes climate commitments','support',
                  'video_utterance',1,'the ARCA governs the structure','gpt',
                  'FY25','fiscal_year','fiscal','2024-07-01','2025-06-30')""")
    row = db.execute("SELECT subject_id, curation_state FROM claims WHERE id=1").fetchone()
    assert row == (None, "unassigned")


def test_fiscal_year_is_stored_as_an_interval(db):
    """Ann Arbor's FY runs July 1 - June 30, so FY25 and calendar 2025 overlap by
    only six months. Timeline queries must compare intervals, not start dates."""
    s, e, cal = db.execute("SELECT asserted_start, asserted_end, asserted_calendar "
                           "FROM claims WHERE id=1").fetchone()
    assert (s.isoformat(), e.isoformat(), cal) == ("2024-07-01", "2025-06-30", "fiscal")


def test_inverted_asserted_interval_is_rejected(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("""INSERT INTO claims (position,polarity,source_type,utterance_id,verbatim,
                      extracted_by,asserted_start,asserted_end)
                      VALUES ('x','support','video_utterance',1,'w','m','2025-01-01','2024-01-01')""")


# ----------------------------------------------------------------- identity
def test_duplicate_utterance_sequence_is_a_database_error(db):
    """v1 hashed start times into 10s buckets; 45% of turns were served another
    turn's extraction because collisions were silent dict overwrites."""
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO utterances (segment_id,media_asset_id,sequence,start_ms,end_ms,text) "
                   "VALUES (1,1,0,999,1000,'dup')")


# ---------------------------------------------------------------- curation
def test_uncurated_claim_cannot_enter_a_page_manifest(db):
    """Staging is allowed at insert; publication is not. This is where the nullable
    subject_id is actually enforced."""
    db.execute("INSERT INTO pages (id,page_type,entity_id,slug,content_hash,prompt_version,model_version) "
               "VALUES (1,'subject',1,'s/test','h','p1','m1')")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("INSERT INTO page_manifests (page_id,claim_id) VALUES (1,1)")


# ------------------------------------------------------------------- views
@pytest.mark.parametrize("view", [
    "v_argument_propagation", "v_timeline", "v_dark_matter_corpus",
    "v_dark_matter_record", "v_unmet_conditions", "v_open_commitments",
    "v_reversed_funding", "v_numeric_drift", "v_scope_conditions",
    "v_load_bearing_claims", "v_entities_with_pending_vocab",
])
def test_view_executes(db, view):
    """A view referencing a column that does not exist only fails when queried."""
    db.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()


# ------------------------------------------------------------- idempotency
# Every table the Legistar ingest writes to needs a key that ON CONFLICT can target.
# In Postgres, `ON CONFLICT DO NOTHING` with NO matching constraint is a silent no-op:
# the insert simply succeeds and a duplicate row appears. This bit twice — body_lineage
# tripled to 6 rows and media_assets to 27 across three ingest passes — so it is worth
# a test per table rather than trusting the ingest code to be careful.
@pytest.mark.parametrize("table,cols,vals", [
    ("body_lineage",
     "(successor_body_id,predecessor_body_id,relation,effective_date,source)",
     "(1,2,'merged_into','2025-07-01','test')"),
    ("media_assets",
     "(event_id,host,external_id,url)",
     "(1,'youtube','lWvRVUMyLP4','https://youtube.com/watch?v=lWvRVUMyLP4')"),
    ("votes",
     "(event_item_id,person_id,vote_value)",
     "(1,1,'yea')"),
])
def test_reingest_does_not_duplicate(db, table, cols, vals):
    if table == "votes":
        db.execute("INSERT INTO event_items (id,event_id,legistar_item_id,title) "
                   "VALUES (1,1,999001,'t') ON CONFLICT DO NOTHING")
    for _ in range(3):
        db.execute(f"INSERT INTO {table} {cols} VALUES {vals} ON CONFLICT DO NOTHING")
    n = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    assert n == 1, f"{table} duplicated on re-ingest: {n} rows after 3 identical inserts"


def test_html_sourced_events_dedupe_on_web_meeting_id(db):
    """HTML-sourced events have no API EventId, and in Postgres NULLs are distinct —
    so UNIQUE (body_id, legistar_event_id) does NOT dedupe them. The web-UI meeting id
    is the key that does."""
    for _ in range(3):
        db.execute("""INSERT INTO events (body_id, legistar_event_id, legistar_meeting_id,
                      event_date, meeting_kind) VALUES (1,NULL,1367374,'2026-03-10','regular')
                      ON CONFLICT (legistar_meeting_id) WHERE legistar_meeting_id IS NOT NULL
                      DO NOTHING""")
    n = db.execute("SELECT count(*) FROM events WHERE legistar_meeting_id=1367374").fetchone()[0]
    assert n == 1


def test_media_asset_always_states_its_provenance(db):
    """A video row must say HOW it came to be associated with its meeting.

    This was briefly nullable and backfilled by a separate tool, so rows sat with
    NULL provenance until an unrelated program happened to run — which is how a
    walkthrough ended up describing values that were not in the table. The evidence
    chain differs materially: a Legistar link is the city ASSERTING the association,
    a channel match is us INFERRING it from a title string. Neither is meaningful if
    the column can be absent.
    """
    db.execute("""INSERT INTO media_assets (event_id, host, external_id, url)
                  VALUES (1,'youtube','provTEST123','https://example.invalid/v')""")
    got = db.execute("SELECT discovered_via FROM media_assets "
                     "WHERE external_id='provTEST123'").fetchone()[0]
    assert got == "legistar_calendar", "provenance defaulted to something unexpected"

    nulls = db.execute("SELECT count(*) FROM media_assets "
                       "WHERE discovered_via IS NULL").fetchone()[0]
    assert nulls == 0


# -------------------------------------------------------------- body lineage
def test_body_lineage_records_the_2025_merge(db):
    """Energy + Environmental -> Sustainability, 2025. A body-scoped query that is
    not lineage-aware silently drops half the A2Zero record."""
    # ON CONFLICT because the idempotency test above already wrote the (1,2) edge —
    # the module-scoped fixture is deliberately shared so tests see realistic state.
    db.execute("""INSERT INTO body_lineage (successor_body_id,predecessor_body_id,relation,
                  effective_date,source) VALUES
                  (1,2,'merged_into','2025-01-01','body_description'),
                  (1,3,'merged_into','2025-01-01','body_description')
                  ON CONFLICT DO NOTHING""")
    n = db.execute("SELECT count(*) FROM body_lineage WHERE successor_body_id=1").fetchone()[0]
    assert n == 2, "both predecessor edges must exist — Energy AND Environmental"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
