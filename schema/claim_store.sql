-- ============================================================================
-- GRAPEVINE CLAIM STORE — Postgres DDL
-- v0.2 — 2026-07-28
--
-- Supersedes v0.1 (video_analysis/project_handover/schema/claim_store.sql).
-- Derived from: ARCHITECTURE-AND-ROADMAP.md, hand-annotation findings SN-05..SN-21,
-- the v1 pipeline code review (docs/v1-code-review.md), the a2zero-wiki quad schema
-- (retired into this one), and a five-source stress test on 2026-07-27.
--
-- No database has been deployed, so this is CREATE-only rather than a migration.
-- migrations/ starts empty and receives its first entry once something is running.
--
-- READ THIS FIRST — six principles the schema encodes:
--
-- 1. TIERED EXTRACTION. Most utterances in a real meeting are procedural
--    ("Present." / "Commissioner Brown?"). They get classified, not analyzed.
--    Structured extraction happens only where extraction_tier IN ('A','B').
--
-- 2. ANALYTICAL FINDINGS ARE ROWS, NOT PROSE. The first pipeline stored funding,
--    barriers, coalitions, and agreements as section-level free-text sentences.
--    Nice to read, impossible to query, count, or check for absence.
--
-- 3. PROVENANCE IS NOT NULL AND NON-EMPTY. A claim that cannot point at the words
--    supporting it cannot exist. v1 produced 71 fabricated claims from empty
--    transcript text; NOT NULL alone would have accepted every one of them,
--    because '' is not NULL. Hence the length(trim(...)) > 0 checks.
--
-- 4. SEMI-OPEN ONTOLOGY. Controlled vocabularies live in vocabulary_terms, not in
--    CHECK constraints (which cannot grow without a migration) and not in SQL
--    comments (which enforce nothing). Unknown terms are stored, logged, and
--    reviewed — never rejected. See LAYER 0.
--
-- 5. DUPLICATION RESOLVES AT THE REFERENT LAYER, NEVER THE ASSERTION LAYER.
--    Claims are never deduplicated: each is one actor at one moment. Repeated
--    FACTS resolve at asserted_events; repeated REASONS at arguments. And
--    corroboration is measured by INDEPENDENCE, not count — six annual reports
--    restating one sentence is one source, not six.
--
-- 6. TIME IS MODELLED TWICE. When something was SAID (utterance/publication time)
--    is a different column from when the asserted thing HAPPENED (world time).
--    Conflating them makes timeline generation impossible. v0.1 had only the first.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector, for candidate retrieval only

-- ============================================================================
-- LAYER 0 — SEMI-OPEN ONTOLOGY
--
-- Every controlled vocabulary in this schema is a row set, not an enum.
--
-- HOW THE LOOP WORKS, and where each step happens:
--   1. EXTRACTION TIME: the model is handed the approved term list for a field.
--      It picks the closest approved term AND, if nothing fits, reports what it
--      wanted. Choosing "closest" is a semantic judgment; only the model can do it.
--   2. INSERT TIME: this layer's trigger is the BACKSTOP. If a term arrives that
--      is not approved at all, it is replaced with the vocabulary's fallback_term
--      and a proposal is logged. THE INSERT IS NEVER REJECTED — rejecting would
--      discard extraction work, which is exactly what v1's enum coercion did badly.
--   3. REVIEW TIME: proposals surface in review_queue ranked by occurrences.
--      Recurrence is the promotion signal. A human approves or rejects.
--   4. REJECTION IS DURABLE: recorded in curation_decisions so the same term is
--      never re-proposed. (The a2zero-wiki learned this the hard way — a rejected
--      merge with no record re-surfaced on every lint run.)
-- ============================================================================

CREATE TABLE vocabularies (
    name            TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    -- FALSE for genuinely closed sets (polarity, extraction_tier, evidence_grade).
    -- Closed vocabularies still live here so there is one place to look.
    is_open         BOOLEAN NOT NULL DEFAULT TRUE,
    -- What the trigger substitutes when an unapproved term arrives. Must itself
    -- be an approved term. NULL means "store the unknown value as-is and flag it"
    -- — appropriate for high-cardinality descriptive fields.
    fallback_term   TEXT
);

CREATE TABLE vocabulary_terms (
    vocabulary      TEXT NOT NULL REFERENCES vocabularies(name) ON DELETE CASCADE,
    term            TEXT NOT NULL,
    description     TEXT,
    approved_by     TEXT NOT NULL,          -- human required; no machine self-approval
    approved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Vocabularies evolve. Deprecating rather than deleting keeps old rows readable
    -- and lets a migration rewrite them deliberately instead of silently.
    deprecated_by_term TEXT,
    PRIMARY KEY (vocabulary, term)
);

-- The drift log. Mirrors a2zero-wiki's meta/schema-drift.md.
CREATE TABLE vocabulary_proposals (
    id              BIGSERIAL PRIMARY KEY,
    vocabulary      TEXT NOT NULL REFERENCES vocabularies(name),
    proposed_term   TEXT NOT NULL,          -- what the model wanted
    written_as      TEXT,                   -- what was actually stored on the row
    entity_table    TEXT NOT NULL,
    entity_id       BIGINT NOT NULL,
    rationale       TEXT,
    example_verbatim TEXT,
    -- RECURRENCE IS THE PROMOTION SIGNAL. A term proposed once is noise;
    -- a term proposed thirty times is a gap in the vocabulary.
    occurrences     INT NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|merged
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    UNIQUE (vocabulary, proposed_term, entity_table)
);

CREATE INDEX idx_vocab_prop_pending ON vocabulary_proposals(status, occurrences DESC);
CREATE INDEX idx_vocab_prop_entity  ON vocabulary_proposals(entity_table, entity_id);

-- The backstop trigger.
--   TG_ARGV[0] = vocabulary name
--   TG_ARGV[1] = column on this table carrying the term
--   TG_ARGV[2] = primary key column (optional, defaults to 'id')
--
-- NOTE ON A DELIBERATE DEVIATION FROM THE PLAN: the plan called for a
-- `vocab_pending` boolean on each entity. That would mean adding and maintaining a
-- denormalized flag on 15+ tables. vocabulary_proposals already records
-- (entity_table, entity_id) with an index, so "which rows have a pending proposal"
-- is a join, and v_entities_with_pending_vocab exposes it. One source of truth.
CREATE OR REPLACE FUNCTION enforce_vocabulary() RETURNS trigger AS $$
DECLARE
    v_name   TEXT := TG_ARGV[0];
    col_name TEXT := TG_ARGV[1];
    pk_col   TEXT := COALESCE(TG_ARGV[2], 'id');
    rec      JSONB := to_jsonb(NEW);
    val      TEXT  := rec ->> col_name;
    pk_val   BIGINT;
    fb       TEXT;
BEGIN
    IF val IS NULL OR val = '' THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1 FROM vocabulary_terms
        WHERE vocabulary = v_name AND term = val AND deprecated_by_term IS NULL
    ) THEN
        RETURN NEW;
    END IF;

    -- Unknown term. Log it, substitute the fallback if one is defined, continue.
    SELECT fallback_term INTO fb FROM vocabularies WHERE name = v_name;
    pk_val := (rec ->> pk_col)::BIGINT;   -- serial defaults are assigned before BEFORE triggers

    INSERT INTO vocabulary_proposals
        (vocabulary, proposed_term, written_as, entity_table, entity_id)
    VALUES (v_name, val, COALESCE(fb, val), TG_TABLE_NAME, pk_val)
    ON CONFLICT (vocabulary, proposed_term, entity_table) DO UPDATE
        SET occurrences  = vocabulary_proposals.occurrences + 1,
            last_seen_at = now();

    IF fb IS NOT NULL THEN
        rec := jsonb_set(rec, ARRAY[col_name], to_jsonb(fb));
        NEW := jsonb_populate_record(NEW, rec);
    END IF;

    RETURN NEW;   -- NEVER reject
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- LAYER 1 — STRUCTURAL (mirrors Legistar and other venues; machine-populated)
-- ============================================================================

CREATE TABLE jurisdictions (
    id                  SERIAL PRIMARY KEY,
    ocd_division_id     TEXT UNIQUE NOT NULL,   -- ocd-division/country:us/state:mi/place:ann_arbor
    name                TEXT NOT NULL,
    state               CHAR(2) NOT NULL,
    legistar_client     TEXT,                   -- 'a2gov'
    home_rule           BOOLEAN,
    government_form     TEXT,                   -- vocab: government_form
    utility_governance  TEXT,                   -- vocab: utility_governance
    -- Fiscal year boundary. Ann Arbor: July 1 -> June 30, per City Charter.
    -- Load-bearing: an FY25 claim and a calendar-2025 claim overlap by only six
    -- months, so every timeline query must compare intervals, not start dates.
    fiscal_year_start_month SMALLINT CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
    -- TRUE when 'FY25' denotes the year ENDING in 2025 (standard municipal
    -- convention). If a jurisdiction labels by start year this flips, and every
    -- fiscal date shifts twelve months.
    fiscal_year_labeled_by_end_year BOOLEAN DEFAULT TRUE,
    notes               TEXT
);

CREATE TABLE jurisdiction_attributes (
    id                  SERIAL PRIMARY KEY,
    jurisdiction_id     INT NOT NULL REFERENCES jurisdictions(id),
    attribute           TEXT NOT NULL,
    value               TEXT NOT NULL,
    valid_from          DATE NOT NULL,
    valid_to            DATE,                   -- NULL = current
    source              TEXT NOT NULL
);

CREATE TABLE bodies (
    id                  SERIAL PRIMARY KEY,
    jurisdiction_id     INT NOT NULL REFERENCES jurisdictions(id),
    legistar_body_id    INT,
    name                TEXT NOT NULL,
    classification      TEXT,                   -- vocab: body_classification
    authority_type      TEXT,                   -- vocab: authority_type
    active              BOOLEAN DEFAULT TRUE,
    created_date        DATE,
    dissolved_date      DATE,
    description         TEXT,
    UNIQUE (jurisdiction_id, legistar_body_id)
);

-- CONFIRMED NECESSARY: Ann Arbor's Sustainability Commission was created in 2025
-- by combining the Energy and Environmental Commissions. A 5-year retrospective
-- crosses that merge. Body-scoped queries MUST be lineage-aware or they silently
-- drop half the A2Zero record.
CREATE TABLE body_lineage (
    id                  SERIAL PRIMARY KEY,
    successor_body_id   INT NOT NULL REFERENCES bodies(id),
    predecessor_body_id INT NOT NULL REFERENCES bodies(id),
    relation            TEXT NOT NULL,          -- vocab: body_lineage_relation
    effective_date      DATE NOT NULL,
    authorizing_matter_id INT,                  -- FK added after matters
    source              TEXT NOT NULL,
    -- Required for idempotent re-ingest. Without it `ON CONFLICT DO NOTHING` has no
    -- constraint to conflict against and silently inserts a duplicate edge on every
    -- run — observed tripling to 6 rows across three ingest passes.
    UNIQUE (successor_body_id, predecessor_body_id, relation)
);

CREATE TABLE posts (                            -- a seat, not a person
    id                  SERIAL PRIMARY KEY,
    body_id             INT NOT NULL REFERENCES bodies(id),
    label               TEXT NOT NULL,          -- 'Councilmember, Ward 3'
    role                TEXT                    -- vocab: post_role
);

CREATE TABLE persons (
    id                  SERIAL PRIMARY KEY,
    legistar_person_id  INT,
    full_name           TEXT NOT NULL,
    sort_name           TEXT,
    -- PRIVACY GATE. Private individuals who speak at public comment are masked in
    -- rendered output, and NO cross-meeting voiceprint may be stored for them.
    -- A persistent voiceprint index of private residents is a materially different
    -- artifact from one covering elected officials.
    is_public_figure    BOOLEAN NOT NULL DEFAULT FALSE,
    notes               TEXT
);

CREATE TABLE person_aliases (
    id                  SERIAL PRIMARY KEY,
    person_id           INT NOT NULL REFERENCES persons(id),
    alias               TEXT NOT NULL,
    alias_type          TEXT                    -- vocab: alias_type
);

-- Voiceprints, gated on is_public_figure. Enforced by trigger below, not by hope.
CREATE TABLE person_voiceprints (
    id                  SERIAL PRIMARY KEY,
    person_id           INT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    -- 256, not 192. The 192 was wespeaker's dimension under pyannote 3.1; the
    -- community-1 pipeline we actually run emits 256-dim centroids (verified against
    -- processing/lWvRVUMyLP4/diarization.json: 21 clusters x 256). A dimension
    -- mismatch is not a soft failure — pgvector rejects the insert outright.
    -- Dimension is pinned deliberately (PLAN.md: "switching models is a migration"),
    -- which is why embedding_model is NOT NULL right below.
    embedding           vector(256),            -- pyannote community-1 centroid
    embedding_model     TEXT NOT NULL,
    sample_count        INT NOT NULL DEFAULT 1, -- accuracy compounds as samples accumulate
    enrolled_from       TEXT NOT NULL,          -- vocab: speaker_id_method ('roll_call' etc.)
    -- '<media_id>:<diarization_cluster>' per contribution, e.g. 'lWvRVUMyLP4:SPEAKER_05'.
    -- Cluster granularity, not recording granularity: one meeting supplies a dozen
    -- clusters to a dozen different people, so a bare media id can neither answer "has
    -- this already been folded in?" (re-running the importer would double-count and
    -- inflate sample_count) nor "was this voice previously filed under someone else?"
    enrolled_from_media TEXT[],
    last_confirmed_by   TEXT,                   -- the human who confirmed the name
    updated_at          TIMESTAMPTZ DEFAULT now(),
    -- Enrollment is incremental (that is what sample_count is for), so "one row per
    -- person per model" has to be enforced, not assumed. Without this, re-processing a
    -- meeting yields a second centroid for the same person and every later cosine
    -- lookup has to guess which one is real. Model is in the key because embeddings
    -- from different models are not comparable and must never be averaged together.
    CONSTRAINT person_voiceprints_person_model_key UNIQUE (person_id, embedding_model)
);

CREATE OR REPLACE FUNCTION reject_private_voiceprint() RETURNS trigger AS $$
BEGIN
    IF NOT (SELECT is_public_figure FROM persons WHERE id = NEW.person_id) THEN
        RAISE EXCEPTION
          'Refusing voiceprint for person % — is_public_figure is FALSE. '
          'Private individuals get name-based identification only.', NEW.person_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voiceprint_privacy
    BEFORE INSERT OR UPDATE ON person_voiceprints
    FOR EACH ROW EXECUTE FUNCTION reject_private_voiceprint();

-- TIME-BOUNDED. People change seats. A flat mapping misattributes votes.
CREATE TABLE memberships (
    id                  SERIAL PRIMARY KEY,
    person_id           INT NOT NULL REFERENCES persons(id),
    post_id             INT REFERENCES posts(id),
    body_id             INT NOT NULL REFERENCES bodies(id),
    role                TEXT,
    valid_from          DATE NOT NULL,
    valid_to            DATE,
    source              TEXT
);

CREATE TABLE orgs (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    org_type            TEXT NOT NULL,          -- vocab: org_type
    ein                 TEXT,                   -- IRS BMF join
    jurisdiction_id     INT REFERENCES jurisdictions(id),
    notes               TEXT
);

-- Affiliation is a time-bounded CLAIM with a confidence, not a label.
CREATE TABLE affiliations (
    id                  SERIAL PRIMARY KEY,
    person_id           INT NOT NULL REFERENCES persons(id),
    org_id              INT NOT NULL REFERENCES orgs(id),
    role                TEXT,
    valid_from          DATE,
    valid_to            DATE,
    source              TEXT NOT NULL,          -- vocab: affiliation_source
    confidence          NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
    reviewed_by         TEXT
);

-- MATTERS ARE NOT LEGISTAR-ONLY.
-- A ballot measure ('Proposal A'), a state bill ('SB 271'), and an MPSC docket are
-- all matters with authority over a Subject. Scoping this table to Legistar would
-- put Ann Arbor's $1B of claimed ratepayer savings — won at the Michigan Public
-- Service Commission — permanently out of reach.
CREATE TABLE matters (
    id                  SERIAL PRIMARY KEY,
    jurisdiction_id     INT NOT NULL REFERENCES jurisdictions(id),
    legistar_matter_id  INT,                    -- NULL for non-Legistar venues
    venue_type          TEXT NOT NULL DEFAULT 'municipal_legislative',  -- vocab: venue_type
    -- Where authority actually sits, which may not be the jurisdiction studying it:
    -- an MPSC docket is a Michigan matter that binds Ann Arbor.
    venue_jurisdiction_id INT REFERENCES jurisdictions(id),
    external_identifier TEXT,                   -- 'Proposal A', 'SB 271', 'U-21297'
    file_number         TEXT,                   -- 'DC-1', 'ORD-24-0331'
    title               TEXT,
    matter_type         TEXT,
    status              TEXT,
    intro_date          DATE,
    passed_date         DATE,
    enactment_number    TEXT,
    UNIQUE (jurisdiction_id, legistar_matter_id)
);

ALTER TABLE body_lineage
    ADD CONSTRAINT fk_bl_matter FOREIGN KEY (authorizing_matter_id) REFERENCES matters(id);

CREATE TABLE matter_versions (
    id                  SERIAL PRIMARY KEY,
    matter_id           INT NOT NULL REFERENCES matters(id),
    version_label       TEXT,
    retrieved_at        TIMESTAMPTZ NOT NULL,
    body_text           TEXT,
    text_hash           TEXT
);

CREATE TABLE events (                           -- a meeting
    id                  SERIAL PRIMARY KEY,
    body_id             INT NOT NULL REFERENCES bodies(id),
    legistar_event_id   INT,
    -- Legistar document URLs are keyed on EventId + GUID, and neither is derivable
    -- from a date. v1 hardcoded one meeting's URLs into a body-level "pattern" and
    -- therefore fetched the same March 10 agenda for every video it processed.
    legistar_guid       TEXT,
    -- A THIRD id space. MeetingDetail.aspx?ID= and View.ashx?ID= use the web-UI
    -- meeting id (e.g. 1367374), which is NOT legistar_event_id (the API EventId,
    -- e.g. 14156) and NOT the web-UI body id. Required as the idempotency key for
    -- HTML-sourced events, which have no API EventId at all because the API hides
    -- any meeting whose agenda is still Draft.
    legistar_meeting_id INT,
    event_date          DATE NOT NULL,
    start_time          TIME,
    end_time            TIME,
    location            TEXT,
    meeting_kind        TEXT,                   -- vocab: meeting_kind
    -- FINDING: some Ann Arbor work/planning sessions are explicitly NOT broadcast.
    -- Absence of video is NOT absence of contestation. Track it as a known-unknown.
    video_available     BOOLEAN,
    video_absent_reason TEXT,
    agenda_url          TEXT,
    agenda_html_url     TEXT,                   -- View.ashx?M=AADA — parse this, not the PDF
    minutes_url         TEXT,
    minutes_html_url    TEXT,                   -- View.ashx?M=MADA
    ecomment_url        TEXT,
    UNIQUE (body_id, legistar_event_id)
);

CREATE TABLE event_items (
    id                  SERIAL PRIMARY KEY,
    event_id            INT NOT NULL REFERENCES events(id),
    legistar_item_id    INT,
    -- NULLABLE AND LOAD-BEARING. Public comment periods, procedural items,
    -- presentations and consent wrappers have NO matter. Code that assumes this
    -- join silently drops the unagendized material Grapevine most wants.
    matter_id           INT REFERENCES matters(id),
    sequence            INT,
    title               TEXT,
    action_text         TEXT,
    passed_flag         BOOLEAN,
    -- An item PULLED FROM the consent agenda for separate discussion is a strong
    -- contestation signal. A consent item that passed in the bundle is not.
    on_consent_agenda   BOOLEAN DEFAULT FALSE,
    pulled_from_consent BOOLEAN DEFAULT FALSE,
    mover_person_id     INT REFERENCES persons(id),
    seconder_person_id  INT REFERENCES persons(id)
);

CREATE TABLE votes (
    id                  SERIAL PRIMARY KEY,
    event_item_id       INT NOT NULL REFERENCES event_items(id),
    person_id           INT NOT NULL REFERENCES persons(id),
    vote_value          TEXT NOT NULL,          -- vocab: vote_value
    UNIQUE (event_item_id, person_id)
);

-- Idempotency for re-ingest. All three external ids are NULLABLE by design — public
-- comment periods have no Legistar item, non-Legistar matters (ballot measures, MPSC
-- dockets) have no Legistar matter, and hand-entered persons have no Legistar person —
-- so these are PARTIAL indexes rather than UNIQUE constraints. Without them a second
-- ingest pass silently duplicates every row.
CREATE UNIQUE INDEX idx_persons_legistar     ON persons(legistar_person_id)
    WHERE legistar_person_id IS NOT NULL;
CREATE UNIQUE INDEX idx_event_items_legistar ON event_items(legistar_item_id)
    WHERE legistar_item_id IS NOT NULL;
-- events.legistar_event_id is NULL for HTML-sourced meetings, and in Postgres NULLs
-- are distinct, so UNIQUE (body_id, legistar_event_id) does NOT dedupe them. The
-- web-UI meeting id is the key that does.
CREATE UNIQUE INDEX idx_events_meeting_id    ON events(legistar_meeting_id)
    WHERE legistar_meeting_id IS NOT NULL;

CREATE TABLE documents (
    id                  SERIAL PRIMARY KEY,
    jurisdiction_id     INT NOT NULL REFERENCES jurisdictions(id),
    event_id            INT REFERENCES events(id),
    matter_id           INT REFERENCES matters(id),
    doc_type            TEXT NOT NULL,          -- vocab: doc_type (GROWS — see Rule 2)
    title               TEXT,
    source_url          TEXT,

    -- ---- THREE DIFFERENT DATES, AND CONFLATING THEM BREAKS CHRONOLOGY ----
    -- When the document was published. v0.1 had no such column, so
    -- v_argument_propagation fell back to retrieved_at and timestamped every
    -- document-sourced claim at the moment we happened to scrape it. That made
    -- Phase 5 temporal lag uncomputable.
    published_date      DATE,
    -- The real-world period the document COVERS. An annual report published in
    -- 2025 describes June 2024 - May 2025. Borrowed from a2zero-wiki's
    -- covers-period-start/end, which exists because that project hit this exact
    -- bug: synthesis arcs were narrating ingest dates instead of program history.
    -- Also the ONLY way to resolve a partial date like "On May 20th" (Year 5).
    covers_period_start DATE,
    covers_period_end   DATE,
    retrieved_at        TIMESTAMPTZ,            -- bookkeeping ONLY. Never a claim date.

    -- ---- MUTABLE SOURCES ----
    -- A webpage is not a stable document. a2gov.org's carbon-neutrality page has no
    -- last-updated timestamp and silently gained a Year 6 report. A char offset into
    -- a live URL is unverifiable within weeks, so spans resolve against the SNAPSHOT.
    is_mutable_source   BOOLEAN NOT NULL DEFAULT FALSE,
    snapshot_path       TEXT,
    snapshot_hash       TEXT,

    markdown_path       TEXT,                   -- reuses the existing PDF->markdown pipeline
    page_count          INT,
    -- Spans are only meaningful against one specific conversion. Claims carry a copy
    -- of this so a converter upgrade cannot silently shift every offset.
    content_hash        TEXT,
    CHECK (NOT is_mutable_source OR snapshot_path IS NOT NULL)
);

CREATE INDEX idx_documents_type ON documents(doc_type);
CREATE INDEX idx_documents_pub  ON documents(published_date);

CREATE TABLE media_assets (
    id                  SERIAL PRIMARY KEY,
    event_id            INT NOT NULL REFERENCES events(id),
    -- Ann Arbor archives to YouTube (CTN), NOT a Granicus player.
    -- Consequence: no index points. Segmentation is built, not free.
    host                TEXT NOT NULL,          -- vocab: media_host
    external_id         TEXT,                   -- YouTube video id — e.g. 'lWvRVUMyLP4'
    url                 TEXT NOT NULL,
    duration_seconds    INT,                    -- use this; event end_time is often null
    has_index_points    BOOLEAN DEFAULT FALSE,
    captions_available  BOOLEAN,
    asr_model           TEXT,
    asr_completed_at    TIMESTAMPTZ,
    -- Host-side metadata, and the reason it is stored rather than trusted.
    -- OBSERVED: Legistar attached video IkZ4APPWNgY to the 2026-05-12 Sustainability
    -- Commission row, but the video is titled "…Meeting 4/14/26" and was uploaded
    -- 2026-04-24 — it is the April 14 meeting. Meanwhile the 2026-04-14 row carries no
    -- video link at all. Ingested naively that misdates every claim from that meeting
    -- by seven weeks, and chronology is the join key across every source.
    -- So the calendar's meeting->video association is EVIDENCE, not ground truth.
    host_title          TEXT,
    host_upload_date    DATE,
    title_stated_date   DATE,               -- date parsed out of the host's own title
    date_verification   TEXT,               -- vocab: video_date_verification
    -- HOW we came to believe this video is this meeting. Two different evidence
    -- chains, and they do not carry the same weight:
    --   legistar_calendar — the city ASSERTED the link by publishing it. Stronger,
    --                       but demonstrably fallible (one link was 28 days wrong).
    --   host_channel      — WE INFERRED it by matching a video title to a meeting.
    --                       Weaker on its own; it is how videos are recovered for the
    --                       many meetings whose Legistar link is missing or points at
    --                       a CTN landing page with no playable video.
    -- A video found both ways is corroborated; one found only by inference is not.
    -- NOT NULL: a media asset without a stated provenance is not interpretable.
    -- This was briefly nullable and backfilled by a separate tool, which meant
    -- rows sat with NULL provenance until an unrelated program happened to run.
    discovered_via      TEXT NOT NULL DEFAULT 'legistar_calendar',  -- vocab: media_discovery_method
    -- Idempotent re-ingest. Keyed on the triple rather than external_id alone because
    -- a long meeting can legitimately be split across several recordings. Without it,
    -- `ON CONFLICT DO NOTHING` has nothing to conflict against and each pass appends a
    -- duplicate — observed tripling to 27 rows over three runs.
    UNIQUE (event_id, host, external_id)
);

-- ============================================================================
-- LAYER 2 — GRAPEVINE SPINE (curated; the integration happens here)
-- ============================================================================

-- TOPIC is the ONLY cross-city commensurability surface. Broad, few, stable.
-- 'Grid Decarbonization', 'Circular Economy'. Ann Arbor's "Strategy 1: Renewable
-- Grid" is NOT a Topic — it is a framework_category defined by one document.
CREATE TABLE topics (
    id                  SERIAL PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,
    description         TEXT,
    parent_topic_id     INT REFERENCES topics(id),
    named_by            TEXT NOT NULL
);

-- SUBJECT is the unit of discussion and the top-level join key across all corpora.
-- NOT Matter — most public comment, work sessions, and report content has no Matter.
--
-- THE TREE IS WORLD CONTAINMENT ONLY, and its depth is whatever the world is.
-- SEU > Solarize Ann Arbor > Commercial Solarize Pilot.
-- There is deliberately NO tree_level enum: an earlier design fixed the levels as
-- plan > strategy > subject > sub_subject, which broke immediately because a city
-- has many plans (Comprehensive Land Use Plan, Annual Budget, Capital Improvements)
-- and because "strategy" exists only because A2Zero happens to define seven.
-- Document structure lives in frameworks instead.
CREATE TABLE subjects (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT,
    jurisdiction_id     INT REFERENCES jurisdictions(id),
    parent_subject_id   INT REFERENCES subjects(id),
    topic_id            INT REFERENCES topics(id),
    wiki_slug           TEXT,                   -- join key to the a2zero-wiki export
    -- Cross-case classification. NOT BINARY — Boulder failed its formal objective
    -- while extracting major concessions. A single success/failure field erases that.
    formal_objective_achieved TEXT,             -- vocab: objective_outcome
    collateral_gains    TEXT,                   -- vocab: collateral_gains
    outcome_notes       TEXT,
    created_by          TEXT NOT NULL,          -- human; a cluster id is never a key
    created_at          TIMESTAMPTZ DEFAULT now(),
    CHECK (parent_subject_id IS NULL OR parent_subject_id <> id)
);

CREATE INDEX idx_subjects_parent ON subjects(parent_subject_id);
CREATE INDEX idx_subjects_topic  ON subjects(topic_id);

CREATE TABLE subject_aliases (
    id                  SERIAL PRIMARY KEY,
    subject_id          INT NOT NULL REFERENCES subjects(id),
    alias               TEXT NOT NULL,
    alias_type          TEXT                    -- vocab: alias_type
);

CREATE TABLE subject_matters (
    subject_id          INT NOT NULL REFERENCES subjects(id),
    matter_id           INT NOT NULL REFERENCES matters(id),
    confidence          NUMERIC(3,2),
    assigned_by         TEXT NOT NULL,          -- human | heuristic  (NOT llm in phase 1)
    assigned_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (subject_id, matter_id)
);

-- A FRAMEWORK is one document's category system, applied as TAGS rather than as a
-- tier in the Subject tree. This is what makes the model portable: a Subject can sit
-- in A2Zero Strategy 2 AND the Comprehensive Plan's Land Use chapter AND the FY26
-- budget's capital line simultaneously — which a strict tree forbade.
CREATE TABLE frameworks (
    id                  SERIAL PRIMARY KEY,
    defining_document_id INT REFERENCES documents(id),
    jurisdiction_id     INT REFERENCES jurisdictions(id),
    name                TEXT NOT NULL,          -- 'A2Zero CAP-2020 strategies'
    valid_from          DATE,
    valid_to            DATE                    -- plans get superseded
);

CREATE TABLE framework_categories (
    id                  SERIAL PRIMARY KEY,
    framework_id        INT NOT NULL REFERENCES frameworks(id) ON DELETE CASCADE,
    code                TEXT,                   -- 'strategy-1'
    name                TEXT NOT NULL,          -- 'Power Our Electrical Grid with 100% Renewable Energy'
    -- CAP-2020 defines TWO levels: Strategy, then named Actions beneath it
    -- ('Implement Community Choice Aggregation'). Annual reports report only at
    -- the Strategy level. Self-reference handles both.
    parent_category_id  INT REFERENCES framework_categories(id),
    sequence            INT,
    UNIQUE (framework_id, code, name)
);

CREATE TABLE subject_framework_categories (
    subject_id          INT NOT NULL REFERENCES subjects(id),
    category_id         INT NOT NULL REFERENCES framework_categories(id),
    assigned_by         TEXT NOT NULL,
    PRIMARY KEY (subject_id, category_id)
);

-- ISSUE is where contestation lives: narrow, emergent, many, discovered.
--
-- DELIBERATELY NOT CONTAINED BY SUBJECT. If issues.subject_id were a foreign key,
-- "groundwater contamination risk" raised about Bryant and about a different
-- project would be two unrelated rows, and noticing they are the same concern would
-- need fuzzy matching at analysis time. Recurrence across Subjects IS the finding.
-- Containment is derived: SELECT DISTINCT issue_id FROM claims WHERE subject_id = X.
CREATE TABLE issues (
    id                  SERIAL PRIMARY KEY,
    canonical_name      TEXT NOT NULL,          -- 'groundwater contamination risk'
    description         TEXT,
    topic_id            INT REFERENCES topics(id),
    named_by            TEXT NOT NULL,          -- human required; do not auto-name
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE issue_aliases (
    id                  SERIAL PRIMARY KEY,
    issue_id            INT NOT NULL REFERENCES issues(id),
    alias               TEXT NOT NULL
);

-- CANONICAL ARGUMENT REGISTRY.
-- An Argument is the canonical form of a REASON. Reason : Argument :: utterance of
-- a word : dictionary entry. The first occurrence creates the Argument at n=1;
-- repetition makes it interesting, not existent. Propagation is then a GROUP BY.
--
-- Without this, detecting that a resident and a councilmember made the SAME argument
-- requires fuzzy semantic matching at analysis time — reintroducing exactly the
-- retrieval uncertainty this architecture exists to eliminate.
CREATE TABLE arguments (
    id                  SERIAL PRIMARY KEY,
    canonical_name      TEXT NOT NULL,          -- 'IOU service is inadequate'
    description         TEXT,
    argument_class      TEXT,                   -- vocab: reason_class
    named_by            TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- LAYER 3 — TRANSCRIPT (cheap, exhaustive, machine-populated)
-- ============================================================================

CREATE TABLE segments (                         -- agenda-item-scoped block of a meeting
    id                  SERIAL PRIMARY KEY,
    event_id            INT NOT NULL REFERENCES events(id),
    event_item_id       INT REFERENCES event_items(id),   -- nullable: unagendized
    subject_id          INT REFERENCES subjects(id),
    sequence            INT NOT NULL,
    section_topic       TEXT,
    start_ms            INT NOT NULL,
    end_ms              INT NOT NULL,
    segment_kind        TEXT NOT NULL,          -- vocab: segment_kind
    -- THE TRIAGE FIELD. Drives extraction depth AND review budget.
    --   A = decision nodes, contested issues, anything a finding will cite
    --   B = substantive but uncontested -> claims + reasons only
    --   C = procedural / ceremonial / routine -> this row and nothing more
    extraction_tier     CHAR(1) NOT NULL DEFAULT 'C' CHECK (extraction_tier IN ('A','B','C')),
    tier_assigned_by    TEXT,
    summary             TEXT,                   -- Tier C stops here. This is all it gets.
    -- V5's source triangulation detects real procedural friction that v0.1 discarded:
    -- two agendized items discussed together, an item skipped and recovered later.
    -- Free contestation signal from a second corpus.
    agenda_deviation_note TEXT,
    deviation_kind      TEXT,                   -- vocab: deviation_kind
    CHECK (end_ms >= start_ms)
);

-- One speaker turn. Cheap fields only — no argumentation, no demeanor.
CREATE TABLE utterances (
    id                  BIGSERIAL PRIMARY KEY,
    segment_id          INT NOT NULL REFERENCES segments(id),
    media_asset_id      INT NOT NULL REFERENCES media_assets(id),
    sequence            INT NOT NULL,
    person_id           INT REFERENCES persons(id),          -- null until resolved
    speaker_label       TEXT,                                -- raw diarization cluster
    actor_capacity      TEXT,                   -- vocab: actor_capacity
    org_id              INT REFERENCES orgs(id),
    start_ms            INT NOT NULL,
    end_ms              INT NOT NULL,
    duration_ms         INT GENERATED ALWAYS AS (end_ms - start_ms) STORED,
    text                TEXT NOT NULL,
    -- Whether this utterance is worth structured extraction at all.
    is_substantive      BOOLEAN,
    responds_to_utterance_id BIGINT REFERENCES utterances(id),
    asr_confidence      NUMERIC(3,2),
    -- Chronology is the join key across every source. A mangled year silently
    -- breaks temporal ordering in a way a mangled noun does not.
    -- Observed: "spring of 2003" for 2023, "fiscal year 2005" for FY25.
    date_validation_flag BOOLEAN DEFAULT FALSE,
    speaker_id_method   TEXT,                   -- vocab: speaker_id_method
    -- IDENTITY MUST NOT DEPEND ON A ROUNDED FLOAT.
    -- v1 hashed start times rounded to 10-second buckets: 1,156 turns collapsed to
    -- 631 ids and 45% of turns received another turn's extraction. The UNIQUE below
    -- makes that a database error instead of a silent dict overwrite.
    UNIQUE (media_asset_id, sequence),
    CHECK (end_ms >= start_ms)
);

CREATE INDEX idx_utt_segment ON utterances(segment_id);
CREATE INDEX idx_utt_person  ON utterances(person_id);
CREATE INDEX idx_utt_subst   ON utterances(is_substantive) WHERE is_substantive;

-- ============================================================================
-- LAYER 4 — CLAIMS (expensive; Tier A and B segments only)
-- ============================================================================

CREATE TABLE claims (
    id                  BIGSERIAL PRIMARY KEY,

    -- WHAT it's about.
    -- subject_id IS NULLABLE, and that is deliberate. Subjects are hand-curated,
    -- but they are DISCOVERED BY EXTRACTING. Requiring one at insert time is a
    -- cold-start deadlock: no claim can land until a human has named the Subject
    -- that the claim itself is evidence for. curation_state carries the staging.
    subject_id          INT REFERENCES subjects(id),
    curation_state      TEXT NOT NULL DEFAULT 'unassigned',  -- vocab: curation_state
    issue_id            INT REFERENCES issues(id),
    matter_id           INT REFERENCES matters(id),

    -- WHO. Both nullable: a 200-page plan document has no actor capacity, and
    -- v0.1's NOT NULL on actor_capacity/speech_act was induced entirely from video
    -- annotation. Forcing values on text corpora would manufacture garbage.
    actor_id            INT REFERENCES persons(id),
    actor_capacity      TEXT,                   -- vocab: actor_capacity
    org_id              INT REFERENCES orgs(id),             -- who they speak FOR, right now

    -- WHAT they said
    position            TEXT NOT NULL,          -- the assertion, in Grapevine's words
    polarity            TEXT NOT NULL,          -- vocab: polarity
    -- "NOT NOW" IS NOT "NO". Multiple no votes on the Ann Arbor municipalization
    -- study explicitly supported the substance and opposed the timing. polarity
    -- alone files them as policy opponents — a misrepresentation that would
    -- propagate into a causal chain and then into a blueprint.
    contested_dimension TEXT,                   -- vocab: contested_dimension
    modality            TEXT,                   -- vocab: modality
    speech_act          TEXT,                   -- vocab: speech_act (oral sources only)

    -- Opportunity-cost reasoning links two Subjects competing for one resource.
    competing_subject_id INT REFERENCES subjects(id),

    -- ---- WORLD TIME: when the asserted thing HAPPENED ----
    -- Distinct from when it was said. Without these, timeline generation is
    -- impossible: the only date available is the utterance or publication date, so
    -- a 2026 statement about a 2022 budget decision sorts as 2026.
    asserted_start      DATE,
    asserted_end        DATE,                   -- intervals, not points
    asserted_precision  TEXT,                   -- vocab: date_precision
    asserted_calendar   TEXT NOT NULL DEFAULT 'calendar',    -- vocab: calendar_system
    asserted_date_text  TEXT,                   -- 'FY25', 'next budget cycle', 'spring'
    date_confidence     NUMERIC(3,2),
    date_validation_flag BOOLEAN DEFAULT FALSE, -- outside plausible window for its source

    -- ---- DARK MATTER, RECORD LEVEL ----
    -- "an outcome stated with NO mechanism described" — salvaged from the retired
    -- quad schema. Grapevine's Outcome 1 thesis as a boolean. Generates a research
    -- question, never a finding.
    outcome_without_mechanism BOOLEAN NOT NULL DEFAULT FALSE,

    -- WHERE it came from
    source_type         TEXT NOT NULL,          -- vocab: source_type
    utterance_id        BIGINT REFERENCES utterances(id),
    document_id         INT REFERENCES documents(id),
    -- News and case studies are claims ABOUT claims. Without this, an article
    -- reporting on a meeting reads as an independent second source and manufactures
    -- evidence_grade='B' corroboration. (News is out of v2 scope; the hook is cheap.)
    reported_by_document_id INT REFERENCES documents(id),
    -- Spans are meaningful only against one specific markdown conversion. Carrying
    -- the hash means a converter upgrade invalidates loudly instead of shifting
    -- every offset silently.
    source_content_hash TEXT,
    span_start          INT,                    -- ms for video, char offset for text
    span_end            INT,
    span_unit           TEXT,                   -- vocab: span_unit — never mix ms and chars
    -- PROVENANCE IS NOT NULL *AND NON-EMPTY*. NOT NULL alone accepts '', which is
    -- exactly what v1's assembler wrote for 71 turns that had no transcript text
    -- at all — every one of which received a fabricated core_claim.
    verbatim            TEXT NOT NULL,
    -- Arguments spread across a multi-turn exchange have no contiguous span.
    extra_spans         JSONB,

    -- HOW MUCH WEIGHT IT BEARS
    confidence          NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
    evidence_grade      CHAR(1) NOT NULL DEFAULT 'C' CHECK (evidence_grade IN ('A','B','C','D')),
        -- A human-verified · B high confidence + corroborated · C model-only · D degraded
        -- NOTE: 'B' requires corroboration, which is computed from event_attestations
        -- with attestation_type independence — NOT from a raw count of sources.
    extracted_by        TEXT NOT NULL,
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),

    CHECK (utterance_id IS NOT NULL OR document_id IS NOT NULL),
    CHECK (length(trim(verbatim)) > 0),
    CHECK (asserted_end IS NULL OR asserted_start IS NULL OR asserted_end >= asserted_start)
);

CREATE INDEX idx_claims_subject   ON claims(subject_id);
CREATE INDEX idx_claims_issue     ON claims(issue_id);
CREATE INDEX idx_claims_actor     ON claims(actor_id);
CREATE INDEX idx_claims_src       ON claims(source_type);
CREATE INDEX idx_claims_grade     ON claims(evidence_grade);
CREATE INDEX idx_claims_curation  ON claims(curation_state);
CREATE INDEX idx_claims_asserted  ON claims(asserted_start, asserted_end);
CREATE INDEX idx_claims_darkmatter ON claims(outcome_without_mechanism)
    WHERE outcome_without_mechanism;

-- REASONS = the reasons a position is held. They persuade. (Called "warrants" in
-- v0.1, after Toulmin; renamed because the field stores kinds of reason, not
-- Toulmin's inferential bridge, and "reason" describes what is actually there.)
-- A 3-minute public comment is ONE ask supported by SEVERAL reasons.
CREATE TABLE claim_reasons (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    argument_id         INT REFERENCES arguments(id),         -- null until canonicalized
    reason_text         TEXT NOT NULL,
    reason_class        TEXT,                   -- vocab: reason_class
    rationale_frame     TEXT,                   -- lives HERE, not on the claim
    sequence            INT,                    -- 'first... second... lastly'
    verbatim            TEXT NOT NULL,
    -- Candidate retrieval ONLY. Embeddings propose; canonical ids decide.
    -- Clustering MUST be stratified by contested_dimension and reason_class first:
    -- "we can't afford it this year" and "we can't afford it at all" have nearly
    -- identical embeddings and are different arguments (timing vs. substance).
    embedding           vector(1536),
    embedding_model     TEXT,
    embedding_version   TEXT,
    CHECK (length(trim(verbatim)) > 0),
    CHECK (embedding IS NULL OR embedding_model IS NOT NULL)
);

CREATE INDEX idx_reasons_claim ON claim_reasons(claim_id);
CREATE INDEX idx_reasons_arg   ON claim_reasons(argument_id);
CREATE INDEX idx_reasons_vec   ON claim_reasons USING hnsw (embedding vector_cosine_ops);

-- CONDITIONS = contingencies a position depends on. They negotiate.
-- Distinct from reasons. This is where the implementation mechanism lives, because
-- conditions are what get traded to assemble a majority.
CREATE TABLE claim_conditions (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    condition_text      TEXT NOT NULL,
    condition_types     TEXT[],                 -- a real condition is often two at once
    -- CONDITIONS ARE NOT EXCLUSIVE TO LIVE DELIBERATION.
    -- The roadmap predicted them by speech act — negotiating moves in live debate.
    -- But CAP-2020 states one outright in a 2020 planning document ("In order to
    -- implement a CCA, the State will need to enact legislation"), and a third-party
    -- case study supplies a 20 MW viability floor and a 6% interest-rate threshold.
    -- Those are documented constraints, not moves. The hypothesis was not falsified;
    -- its scope was narrower than stated.
    condition_origin    TEXT,                   -- vocab: condition_origin
    is_met              TEXT DEFAULT 'unknown', -- vocab: is_met
    resolved_by_claim_id BIGINT REFERENCES claims(id),
    resolved_at_ms      INT,
    -- When the condition is expected to resolve. CAP-2020's CCA condition carried a
    -- 2027 target and was still unmet five years later — that gap is the finding.
    target_date         DATE,
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

CREATE INDEX idx_conditions_claim ON claim_conditions(claim_id);
CREATE INDEX idx_conditions_unmet ON claim_conditions(is_met) WHERE is_met <> 'true';

CREATE TABLE claim_relations (
    id                  BIGSERIAL PRIMARY KEY,
    claim_a_id          BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    claim_b_id          BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    relation            TEXT NOT NULL,          -- vocab: claim_relation
    confidence          NUMERIC(3,2),
    evidence            TEXT,
    -- DO NOT resolve contradictions into a single truth. The contradiction IS the finding.
    CHECK (claim_a_id <> claim_b_id)
);

-- Practitioners citing other jurisdictions IS the policy-transfer mechanism
-- Grapevine studies. When they DISPUTE comparability they are naming scope
-- conditions for free.
CREATE TABLE jurisdiction_citations (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    cited_jurisdiction  TEXT NOT NULL,
    cited_ocd_id        TEXT,
    cited_as            TEXT NOT NULL,          -- vocab: citation_stance
    claimed_lesson      TEXT,
    comparability_disputed_by INT REFERENCES persons(id),
    -- HIGHEST-VALUE COLUMN IN THE TABLE. A practitioner's reason another case does
    -- not apply here is a candidate scope condition, surfaced by a domain expert at
    -- no cost. Feed directly into the Phase 6 cross-case condition vocabulary.
    dispute_basis       TEXT,
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

-- Speakers cite PRIOR MEETINGS as evidence. A cross-meeting provenance edge.
CREATE TABLE claim_prior_references (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    referenced_event_id INT REFERENCES events(id),
    referenced_body_id  INT REFERENCES bodies(id),
    referenced_period   TEXT,                   -- when the event id is unresolved
    reference_text      TEXT NOT NULL
);

-- ============================================================================
-- LAYER 5 — THE REFERENT LAYER
--
-- Claims are assertions. asserted_events are the THINGS ASSERTED. Four sources
-- describing one grant award are four attestations of one event, not four claims
-- about four things — but they are still four claims, because a claim records who
-- said what and when. Deduplication happens HERE and never on claims.
-- ============================================================================

CREATE TABLE asserted_events (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    canonical_description TEXT NOT NULL,        -- 'City Council declared a climate emergency'
    event_class         TEXT,                   -- vocab: asserted_event_class
    occurred_start      DATE,
    occurred_end        DATE,
    occurred_precision  TEXT,                   -- vocab: date_precision
    occurred_calendar   TEXT NOT NULL DEFAULT 'calendar',
    -- FREE AUTHORITY. Legistar actions arrive pre-identified with dates and votes,
    -- so any event matching one auto-links at high confidence with ZERO human
    -- naming. Only non-Legistar events (grant awards, administrative milestones,
    -- verbally reconstructed pathways) need curation — which makes this the
    -- cheapest of the four curated registries.
    legistar_event_item_id INT REFERENCES event_items(id),
    named_by            TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now(),
    CHECK (occurred_end IS NULL OR occurred_start IS NULL OR occurred_end >= occurred_start)
);

CREATE INDEX idx_asserted_events_subject ON asserted_events(subject_id);
CREATE INDEX idx_asserted_events_when    ON asserted_events(occurred_start, occurred_end);

CREATE TABLE event_attestations (
    id                  BIGSERIAL PRIMARY KEY,
    asserted_event_id   BIGINT NOT NULL REFERENCES asserted_events(id) ON DELETE CASCADE,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    -- What THIS source says the date was. May disagree with the canonical value —
    -- and that disagreement is a Phase 5 date-drift finding, not an error to average.
    stated_start        DATE,
    stated_end          DATE,
    stated_precision    TEXT,
    stated_date_text    TEXT,
    -- INDEPENDENCE, NOT PRESENCE. This is what makes the count mean anything.
    -- Six OSI annual reports repeating one sentence is ONE origin restated five
    -- times. Ranking a timeline by raw attestation count puts boilerplate on top.
    attestation_type    TEXT NOT NULL,          -- vocab: attestation_type
    -- The anti-laundering pointer. A third-party case study whose contributor is
    -- city staff is not an independent source, however different the letterhead.
    derives_from_attestation_id BIGINT REFERENCES event_attestations(id),
    text_similarity     NUMERIC(4,3),           -- cosine vs. nearest prior attestation;
                                                -- the one place geometry measures the
                                                -- right thing (textual reuse, not stance)
    UNIQUE (asserted_event_id, claim_id)
);

CREATE INDEX idx_attest_event ON event_attestations(asserted_event_id);
CREATE INDEX idx_attest_claim ON event_attestations(claim_id);

-- ============================================================================
-- LAYER 6 — TYPED FINDINGS (v1 stored all of these as section-level PROSE)
-- ============================================================================

CREATE TABLE decisions (
    id                  BIGSERIAL PRIMARY KEY,
    event_item_id       INT REFERENCES event_items(id),
    subject_id          INT REFERENCES subjects(id),
    decision_type       TEXT NOT NULL,          -- vocab: decision_type
    motion_text         TEXT,
    mover_person_id     INT REFERENCES persons(id),
    seconder_person_id  INT REFERENCES persons(id),
    threshold_required  TEXT,
    outcome             TEXT,                   -- vocab: decision_outcome
    effective_date      DATE,
    sunset_date         DATE,
    utterance_id        BIGINT REFERENCES utterances(id),
    verbatim            TEXT
);

-- THE PATH NOT TAKEN. The most replication-relevant structure in the corpus, and
-- v0.1 had nowhere to put it.
--
-- Observed in a single case study: air-source heat pumps rejected (operating costs
-- prohibitive); utility-owned geothermal rejected (Michigan lacks enabling
-- legislation); full municipalization rejected (cost, and exceeds the 2030
-- deadline); Sustainable Energy Utility adopted. "We tried X and here is why it
-- failed HERE" is what separates a transferable playbook from a press release,
-- and it is the direct antidote to the spurious-transferability risk.
--
-- NOT the same as `barriers`: a barrier is an obstacle encountered; a rejected
-- alternative is a decision point with a stated counterfactual.
CREATE TABLE considered_alternatives (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT NOT NULL REFERENCES subjects(id),
    alternative_name    TEXT NOT NULL,          -- 'utility-owned geothermal network'
    disposition         TEXT NOT NULL,          -- vocab: alternative_disposition
    rejection_class     TEXT,                   -- vocab: rejection_class
    rejection_reason    TEXT,
    superseded_by_alternative_id BIGINT REFERENCES considered_alternatives(id),
    claim_id            BIGINT REFERENCES claims(id),
    -- A rejection reason IS a candidate scope condition, same as dispute_basis.
    is_scope_condition_candidate BOOLEAN NOT NULL DEFAULT TRUE,
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

-- Open loops. "Staff will return in 60 days with a cost analysis."
CREATE TABLE commitments (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    committed_by_person_id INT REFERENCES persons(id),
    committed_by_org_id INT REFERENCES orgs(id),
    commitment_text     TEXT NOT NULL,
    due_date            DATE,
    due_description     TEXT,                   -- 'within 60 days', 'year 6', 'next budget cycle'
    status              TEXT NOT NULL DEFAULT 'open',   -- vocab: commitment_status
    fulfilled_by_document_id INT REFERENCES documents(id),
    fulfilled_by_event_id INT REFERENCES events(id),
    source_type         TEXT NOT NULL,
    utterance_id        BIGINT REFERENCES utterances(id),
    document_id         INT REFERENCES documents(id),
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

-- MONEY. A specialization of the quantity idea, kept separate because funding has
-- structure (source, instrument, recurrence) that a generic quantity does not.
CREATE TABLE fiscal_references (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    claim_id            BIGINT REFERENCES claims(id),
    amount_low          NUMERIC(14,2),
    amount_high         NUMERIC(14,2),          -- ranges are common: '$281M to $1.15B'
    currency            CHAR(3) DEFAULT 'USD',
    fiscal_year         TEXT,
    funding_source      TEXT,                   -- vocab: funding_source
    funding_instrument  TEXT,                   -- vocab: funding_instrument
    recurrence          TEXT,                   -- vocab: funding_recurrence
    purpose             TEXT,
    awarding_org_id     INT REFERENCES orgs(id),
    -- AWARDS GET REVERSED, AND THE REVERSAL IS THE FINDING.
    -- One annual report contained four: a returnable-container grant "terminated by
    -- the federal administration", a Heat Resilient Communities award "terminated",
    -- a $1M EPA environmental-justice award "terminated... the City is actively
    -- disputing", and a $10M DOE geothermal grant "(Funding is currently on hold)".
    -- A playbook recommending a clawed-back grant is the spurious-transferability
    -- failure mode in its purest form. v0.1 had no status column at all.
    award_status        TEXT NOT NULL DEFAULT 'unknown',  -- vocab: award_status
    status_as_of        DATE,
    status_change_reason TEXT,
    source_type         TEXT NOT NULL,
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

CREATE INDEX idx_fiscal_status ON fiscal_references(award_status);

-- EVERYTHING THAT IS NOT MONEY.
-- Annual reports are made of MW, tCO2e, percentages, and counts, and numeric drift
-- between sources is a named deliverable — which v0.1 could compute only for
-- dollars. One page of one report carried 5.4 MW (Solarize program), 6.5 MW (all
-- installations since plan adoption), and 11.88 MW (dashboard). Three scopes, one
-- metric, and without scope_note they read as a contradiction.
CREATE TABLE quantities (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT REFERENCES claims(id) ON DELETE CASCADE,
    subject_id          INT REFERENCES subjects(id),
    value_low           NUMERIC,
    value_high          NUMERIC,
    bound               TEXT,                   -- vocab: quantity_bound ("over 6.5 MW")
    unit                TEXT NOT NULL,          -- vocab: quantity_unit
    unit_basis          TEXT,                   -- what a % or count is OF
    measure             TEXT NOT NULL,          -- vocab: quantity_measure
    scope_note          TEXT,                   -- NOT optional in practice; see above
    as_of_date          DATE,
    as_of_precision     TEXT,
    -- "projected to reduce residents' energy costs by 77%" is not a measurement.
    is_projection       BOOLEAN NOT NULL DEFAULT FALSE,
    source_type         TEXT,
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

CREATE INDEX idx_quantities_subject ON quantities(subject_id, measure);

-- Barriers recur across cases; that's the cross-case signal.
CREATE TABLE barriers (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    issue_id            INT REFERENCES issues(id),
    claim_id            BIGINT REFERENCES claims(id),
    barrier_class       TEXT NOT NULL,          -- vocab: barrier_class
    barrier_text        TEXT NOT NULL,
    named_by_person_id  INT REFERENCES persons(id),
    resolution_status   TEXT DEFAULT 'unresolved',
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

CREATE TABLE coalitions (
    id                  SERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    name                TEXT,
    coalition_type      TEXT,                   -- vocab: coalition_type
    formed_date         DATE,
    purpose             TEXT
);

CREATE TABLE coalition_members (
    coalition_id        INT NOT NULL REFERENCES coalitions(id),
    person_id           INT REFERENCES persons(id),
    org_id              INT REFERENCES orgs(id),
    body_id             INT REFERENCES bodies(id),
    role                TEXT,                   -- vocab: involvement_role
    claim_id            BIGINT REFERENCES claims(id),         -- provenance
    PRIMARY KEY (coalition_id, person_id, org_id, body_id)
);

-- REPLICATION ACTUALLY HAPPENING — Grapevine's dependent variable, observed.
-- "Solarize was expanded throughout the State, including in Grand Rapids" is the
-- program operating elsewhere. jurisdiction_citations models a speaker INVOKING
-- another jurisdiction as evidence. Different relation, different table.
CREATE TABLE program_diffusion (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT NOT NULL REFERENCES subjects(id),
    adopting_jurisdiction TEXT NOT NULL,        -- 'Grand Rapids, MI'
    adopting_ocd_id     TEXT,
    relation            TEXT NOT NULL,          -- vocab: diffusion_relation
    occurred_start      DATE,
    occurred_precision  TEXT,
    claim_id            BIGINT REFERENCES claims(id),
    verbatim            TEXT NOT NULL,
    CHECK (length(trim(verbatim)) > 0)
);

-- ============================================================================
-- LAYER 7 — RENDER (wiki pages as materialized views)
-- ============================================================================

CREATE TABLE pages (
    id                  SERIAL PRIMARY KEY,
    page_type           TEXT NOT NULL,          -- vocab: page_type
    entity_id           INT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,
    -- hash over ordered claim ids + each claim's content hash + prompt ver + model ver
    content_hash        TEXT NOT NULL,
    body_markdown       TEXT,
    prompt_version      TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    rendered_at         TIMESTAMPTZ,
    is_dirty            BOOLEAN DEFAULT TRUE,
    embedding           vector(1536),
    embedding_model     TEXT
);

-- Which claims a page was built from. Enables impact analysis on correction,
-- load-bearing detection for review triage, and full provenance chains.
CREATE TABLE page_manifests (
    page_id             INT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, claim_id)
);

CREATE INDEX idx_manifest_claim ON page_manifests(claim_id);

CREATE TABLE page_versions (
    id                  SERIAL PRIMARY KEY,
    page_id             INT NOT NULL REFERENCES pages(id),
    content_hash        TEXT NOT NULL,
    body_markdown       TEXT,
    rendered_at         TIMESTAMPTZ NOT NULL
);

-- A claim may not be rendered until a human has assigned it a Subject. This is
-- where the nullable subject_id is enforced instead of at insert: staging is
-- allowed, publication is not.
CREATE OR REPLACE FUNCTION require_curated_claim() RETURNS trigger AS $$
BEGIN
    IF (SELECT subject_id IS NULL OR curation_state <> 'confirmed'
        FROM claims WHERE id = NEW.claim_id) THEN
        RAISE EXCEPTION
          'Claim % cannot enter a page manifest: subject_id unset or curation_state '
          'is not confirmed.', NEW.claim_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_manifest_curation
    BEFORE INSERT ON page_manifests
    FOR EACH ROW EXECUTE FUNCTION require_curated_claim();

-- ============================================================================
-- LAYER 8 — CURATION, REVIEW, AND CORPUS GROWTH
-- ============================================================================

-- Durable curation decisions, INCLUDING negative ones. a2zero-wiki learned this the
-- hard way: a rejected merge that left no trace re-surfaced on every lint run.
CREATE TABLE curation_decisions (
    id                  BIGSERIAL PRIMARY KEY,
    entity_table        TEXT NOT NULL,
    entity_a_id         BIGINT NOT NULL,
    entity_b_id         BIGINT,
    decision            TEXT NOT NULL,          -- vocab: curation_decision
    decided_by          TEXT NOT NULL,
    decided_at          TIMESTAMPTZ DEFAULT now(),
    rationale           TEXT
);

CREATE INDEX idx_curation_pair ON curation_decisions(entity_table, entity_a_id, entity_b_id);

CREATE TABLE review_queue (
    id                  BIGSERIAL PRIMARY KEY,
    entity_table        TEXT NOT NULL,
    entity_id           BIGINT NOT NULL,
    reason              TEXT NOT NULL,          -- vocab: review_reason
    priority            INT NOT NULL DEFAULT 5,
    assigned_to         TEXT,
    status              TEXT DEFAULT 'pending', -- pending | in_review | resolved | escalated
    resolution          TEXT,
    -- MEASURE THIS, AND MEASURE IT BY REASON. Claim verification and argument
    -- naming are different cost curves; averaging them hides the expensive one.
    minutes_spent       INT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_rq_status ON review_queue(status, priority);
CREATE INDEX idx_rq_reason ON review_queue(reason, status);

-- ---- CORPUS GROWTH (Rule 2) ----
-- These three tables are the INTERFACE to the action-discovery process being built
-- in parallel. This schema owns the emit side only. If that effort's contract
-- differs, these are the negotiation surface.

CREATE TABLE research_questions (
    id                  BIGSERIAL PRIMARY KEY,
    question            TEXT NOT NULL,
    subject_id          INT REFERENCES subjects(id),
    issue_id            INT REFERENCES issues(id),
    origin              TEXT NOT NULL,          -- vocab: research_question_origin
    origin_claim_id     BIGINT REFERENCES claims(id),
    priority            INT NOT NULL DEFAULT 5,
    status              TEXT NOT NULL DEFAULT 'open',
    promoted_by         TEXT,                   -- human gate before it drives acquisition
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE source_targets (
    id                  BIGSERIAL PRIMARY KEY,
    research_question_id BIGINT REFERENCES research_questions(id),
    target_description  TEXT NOT NULL,          -- 'MPSC gas case testimony, U-21297'
    doc_type            TEXT,                   -- vocab: doc_type
    venue               TEXT,
    expected_url        TEXT,
    status              TEXT NOT NULL DEFAULT 'identified',  -- vocab: source_target_status
    resolved_document_id INT REFERENCES documents(id),
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- CRITICAL FOR DARK-MATTER EPISTEMICS.
-- "We looked and it isn't there" is a different statement from "we haven't looked."
-- Without this table every gap is ambiguous, and on a deliberately partial corpus
-- that ambiguity always resolves in the direction that flatters the thesis.
-- Every dark-matter finding must cite a row here or be labelled unsearched.
CREATE TABLE source_search_log (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    doc_type            TEXT NOT NULL,
    venue               TEXT,
    searched_by         TEXT NOT NULL,
    searched_at         TIMESTAMPTZ NOT NULL,
    query_used          TEXT,
    outcome             TEXT NOT NULL,          -- vocab: search_outcome
    coverage_note       TEXT
);

CREATE INDEX idx_search_subject ON source_search_log(subject_id, doc_type);

-- ============================================================================
-- CORE QUERIES — the ones that justify the whole design
-- ============================================================================

-- Argument propagation: how a community argument enters the official record.
-- FIXED vs v0.1, which used COALESCE(e.event_date, d.retrieved_at::date) and so
-- timestamped every document-sourced claim at the moment it was scraped.
CREATE VIEW v_argument_propagation AS
SELECT a.id AS argument_id, a.canonical_name,
       c.source_type, c.actor_capacity, c.speech_act,
       COALESCE(
           c.asserted_start,                    -- world time, when we have it
           e.event_date,                        -- else when it was said aloud
           d.published_date,                    -- else when it was published
           d.covers_period_end                  -- else the end of what it covers
       ) AS occurred_on,
       p.full_name AS actor, c.id AS claim_id, cr.verbatim
FROM arguments a
JOIN claim_reasons cr  ON cr.argument_id = a.id
JOIN claims c          ON c.id = cr.claim_id
LEFT JOIN persons p    ON p.id = c.actor_id
LEFT JOIN utterances u ON u.id = c.utterance_id
LEFT JOIN segments s   ON s.id = u.segment_id
LEFT JOIN events e     ON e.id = s.event_id
LEFT JOIN documents d  ON d.id = c.document_id
ORDER BY a.id, occurred_on;

-- TIMELINE SALIENCE, ranked by INDEPENDENT attestations.
-- Ranking by raw count would put boilerplate on top: an annual report repeating its
-- own framing five years running would outrank a genuinely corroborated event.
CREATE VIEW v_timeline AS
SELECT ae.id AS asserted_event_id,
       ae.subject_id,
       ae.canonical_description,
       ae.event_class,
       ae.occurred_start, ae.occurred_end, ae.occurred_precision,
       COUNT(*) FILTER (
           WHERE ea.attestation_type IN ('primary_record','firsthand','secondhand')
       ) AS independent_attestations,
       COUNT(DISTINCT c.source_type) AS distinct_source_types,
       COUNT(*) AS total_attestations,
       -- date drift: more than one distinct stated date is a Phase 5 finding
       COUNT(DISTINCT ea.stated_start) > 1 AS dates_disagree
FROM asserted_events ae
JOIN event_attestations ea ON ea.asserted_event_id = ae.id
JOIN claims c              ON c.id = ea.claim_id
GROUP BY ae.id
ORDER BY independent_attestations DESC, ae.occurred_start;

-- DARK MATTER, CORPUS LEVEL: issues raised publicly that no official document engages.
-- NOT A FINDING. A ranked lead for human investigation. The searched_types column is
-- what keeps it honest — an empty array means nobody has looked.
CREATE VIEW v_dark_matter_corpus AS
SELECT c.subject_id, c.issue_id, i.canonical_name,
       COUNT(*) AS public_claims,
       (SELECT array_agg(DISTINCT ssl.doc_type)
          FROM source_search_log ssl
         WHERE ssl.subject_id = c.subject_id) AS searched_types
FROM claims c
JOIN issues i ON i.id = c.issue_id
WHERE c.source_type IN ('video_utterance','ecomment','correspondence')
  AND c.actor_capacity IN ('public','organizational_rep')
  AND NOT EXISTS (
      SELECT 1 FROM claims c2
      WHERE c2.issue_id = c.issue_id
        AND c2.subject_id = c.subject_id
        AND c2.source_type IN ('staff_report','memo','minutes','plan','annual_report')
  )
GROUP BY c.subject_id, c.issue_id, i.canonical_name
ORDER BY public_claims DESC;

-- DARK MATTER, RECORD LEVEL: outcomes asserted with no mechanism described.
CREATE VIEW v_dark_matter_record AS
SELECT c.id AS claim_id, c.subject_id, s.name AS subject_name,
       c.position, c.asserted_start, c.source_type, c.verbatim
FROM claims c
LEFT JOIN subjects s ON s.id = c.subject_id
WHERE c.outcome_without_mechanism
ORDER BY c.asserted_start NULLS LAST;

-- Conditions that were stated and never resolved, with how long they have been open.
-- CAP-2020 stated that Community Choice Aggregation required State enabling
-- legislation, targeted 2027, and five years later the city was still drafting the
-- bill. That trajectory is exactly what a replication playbook must not omit.
CREATE VIEW v_unmet_conditions AS
SELECT cc.id, cc.condition_text, cc.condition_origin, cc.target_date,
       c.subject_id, s.name AS subject_name,
       c.asserted_start AS stated_on,
       (CURRENT_DATE - cc.target_date) AS days_past_target,
       cc.verbatim
FROM claim_conditions cc
JOIN claims c        ON c.id = cc.claim_id
LEFT JOIN subjects s ON s.id = c.subject_id
WHERE cc.is_met <> 'true'
ORDER BY cc.target_date NULLS LAST;

-- Open loops: verbal commitments with no written follow-up, ranked by age.
CREATE VIEW v_open_commitments AS
SELECT cm.*, (CURRENT_DATE - cm.due_date) AS days_overdue
FROM commitments cm
WHERE cm.status = 'open'
  AND cm.fulfilled_by_document_id IS NULL
ORDER BY cm.due_date NULLS LAST;

-- Funding that was awarded and then reversed. A playbook citing one of these is
-- actively harmful advice.
CREATE VIEW v_reversed_funding AS
SELECT fr.id, s.name AS subject_name, fr.amount_low, fr.amount_high,
       fr.funding_source, fr.award_status, fr.status_as_of,
       fr.status_change_reason, fr.verbatim
FROM fiscal_references fr
LEFT JOIN subjects s ON s.id = fr.subject_id
WHERE fr.award_status IN ('on_hold','terminated','rescinded','disputed','withdrawn')
ORDER BY fr.status_as_of DESC NULLS LAST;

-- Numeric drift: the same measure on the same subject, reported differently.
-- scope_note is displayed because most apparent conflicts are scope differences.
CREATE VIEW v_numeric_drift AS
SELECT q.subject_id, s.name AS subject_name, q.measure, q.unit,
       COUNT(DISTINCT q.value_low) AS distinct_values,
       array_agg(DISTINCT q.value_low ORDER BY q.value_low) AS values,
       array_agg(DISTINCT q.scope_note) AS scopes,
       array_agg(DISTINCT q.source_type) AS source_types
FROM quantities q
LEFT JOIN subjects s ON s.id = q.subject_id
GROUP BY q.subject_id, s.name, q.measure, q.unit
HAVING COUNT(DISTINCT q.value_low) > 1;

-- Scope conditions harvested from practitioners disputing comparability, plus the
-- reasons alternatives were rejected. Both are free scope-condition generators.
CREATE VIEW v_scope_conditions AS
SELECT 'jurisdiction_citation' AS origin, jc.dispute_basis AS condition_text,
       jc.cited_jurisdiction AS context, jc.verbatim
FROM jurisdiction_citations jc
WHERE jc.dispute_basis IS NOT NULL
UNION ALL
SELECT 'rejected_alternative', ca.rejection_reason,
       ca.alternative_name, ca.verbatim
FROM considered_alternatives ca
WHERE ca.disposition = 'rejected' AND ca.is_scope_condition_candidate;

-- Load-bearing claims: cited by many pages, therefore worth human review.
CREATE VIEW v_load_bearing_claims AS
SELECT c.id, c.position, c.evidence_grade, COUNT(pm.page_id) AS page_count
FROM claims c
JOIN page_manifests pm ON pm.claim_id = c.id
WHERE c.reviewed_by IS NULL
GROUP BY c.id, c.position, c.evidence_grade
HAVING COUNT(pm.page_id) > 1
ORDER BY page_count DESC;

-- BEST AVAILABLE DOCUMENT PER MEETING, with the format made explicit.
--
-- Four document columns exist because two formats do: Legistar publishes a PDF
-- (View.ashx?M=A / M=M) and an "accessible" HTML variant (M=AADA / M=MADA). The HTML
-- parses far more reliably, which matters because minutes are Tier 1 ground truth for
-- the contestation index — but the accessible variants only appear from roughly
-- March 2026 for some bodies, so the PDF is a required fallback, not a legacy path.
--
-- This view resolves the preference once, in one place, so no downstream consumer has
-- to re-implement it — and reports WHICH format it settled on, because a segmentation
-- run over a PDF is a different proposition from one over HTML and should not be
-- silently equivalent.
CREATE VIEW v_event_documents AS
SELECT e.id AS event_id,
       b.name AS body_name,
       e.event_date,
       e.legistar_event_id,
       e.legistar_meeting_id,
       COALESCE(e.agenda_html_url,  e.agenda_url)  AS agenda_best_url,
       CASE WHEN e.agenda_html_url  IS NOT NULL THEN 'html'
            WHEN e.agenda_url       IS NOT NULL THEN 'pdf'  END AS agenda_format,
       COALESCE(e.minutes_html_url, e.minutes_url) AS minutes_best_url,
       CASE WHEN e.minutes_html_url IS NOT NULL THEN 'html'
            WHEN e.minutes_url      IS NOT NULL THEN 'pdf'  END AS minutes_format,
       -- Segmentation needs at least one of the two. Nothing at all means the meeting
       -- cannot be aligned to its agenda and must fall back to transcript-only cues.
       (COALESCE(e.agenda_html_url, e.agenda_url) IS NOT NULL
        OR COALESCE(e.minutes_html_url, e.minutes_url) IS NOT NULL) AS segmentable,
       m.external_id  AS youtube_id,
       m.date_verification
FROM events e
JOIN bodies b ON b.id = e.body_id
LEFT JOIN media_assets m ON m.event_id = e.id;

-- Rows carrying a term that is not yet in an approved vocabulary.
-- Replaces the per-table vocab_pending flag the plan proposed; one source of truth.
CREATE VIEW v_entities_with_pending_vocab AS
SELECT vp.entity_table, vp.entity_id, vp.vocabulary,
       vp.proposed_term, vp.written_as, vp.occurrences, vp.first_seen_at
FROM vocabulary_proposals vp
WHERE vp.status = 'pending'
ORDER BY vp.occurrences DESC;


-- v_person_dossier — the compiled person record. Feeds page_type = 'person'.
--
-- Every piece of this already existed. persons, person_aliases, person_voiceprints,
-- memberships, affiliations, and ELEVEN tables carrying a person_id in some role —
-- speaker, voter, mover, seconder, committer, coalition member, named-by. What did not
-- exist was anything that pulled them together, so "what do we know about this person"
-- was a query you had to write from scratch each time.
--
-- `page_type` already contains 'person', so a person page was always intended. This is
-- the query that feeds it.
--
-- WHY A VIEW AND NOT A TABLE. A dossier is derived — every fact in it lives somewhere
-- with its own provenance. Materialising it would create a second copy that can drift
-- from the claims that justify it, which is the failure mode `pages.is_dirty` and
-- `page_manifests` exist to prevent. If it gets slow, make it MATERIALIZED and refresh
-- on the same signal that dirties a page; do not hand-maintain it.

CREATE OR REPLACE VIEW v_person_dossier AS
SELECT
    p.id                        AS person_id,
    p.full_name,
    p.is_public_figure,
    p.legistar_person_id,

    -- Every spelling this person is known by. THE SPELLING AUTHORITY: ASR and LLM
    -- transcription both mangle proper nouns ('Malik' for Mallek, 'Kathari' for
    -- Kothari, 'Mazlumian' for Mazloomian), and this is what maps them back.
    (SELECT array_agg(a.alias ORDER BY a.alias)
       FROM person_aliases a WHERE a.person_id = p.id)            AS aliases,

    -- Voice identity. NULL for private individuals BY DESIGN — reject_private_voiceprint()
    -- refuses the insert, so absence here is a privacy guarantee, not missing data.
    (SELECT count(*) FROM person_voiceprints v WHERE v.person_id = p.id)
                                                                   AS voiceprint_count,
    (SELECT max(v.sample_count) FROM person_voiceprints v WHERE v.person_id = p.id)
                                                                   AS voiceprint_samples,
    (SELECT array_agg(DISTINCT m) FROM person_voiceprints v,
            unnest(v.enrolled_from_media) m WHERE v.person_id = p.id)
                                                                   AS voiceprint_sources,

    -- Where they sit, over time. Time-bounded: people change seats and a flat mapping
    -- misattributes votes.
    (SELECT array_agg(DISTINCT b.name) FROM memberships ms
       JOIN bodies b ON b.id = ms.body_id WHERE ms.person_id = p.id) AS bodies,
    (SELECT array_agg(DISTINCT o.name) FROM affiliations af
       JOIN orgs o ON o.id = af.org_id WHERE af.person_id = p.id)  AS orgs,

    -- Speaking record.
    (SELECT count(*) FROM utterances u WHERE u.person_id = p.id)    AS utterances,
    (SELECT count(DISTINCT u.media_asset_id) FROM utterances u
       WHERE u.person_id = p.id)                                    AS meetings_spoken_in,
    (SELECT round(sum(u.duration_ms)/60000.0, 1) FROM utterances u
       WHERE u.person_id = p.id)                                    AS minutes_spoken,
    -- How we came to believe this is them, strongest evidence first. A person known
    -- only by 'chair_address' is a weaker record than one with roll_call + human.
    (SELECT array_agg(DISTINCT u.speaker_id_method) FROM utterances u
       WHERE u.person_id = p.id AND u.speaker_id_method IS NOT NULL) AS id_methods,

    -- Legislative record. These arrive pre-identified from Legistar, so they are the
    -- cheapest high-confidence facts we hold about anyone.
    (SELECT count(*) FROM votes v WHERE v.person_id = p.id)         AS votes_cast,
    (SELECT count(*) FROM event_items ei
       WHERE ei.mover_person_id = p.id OR ei.seconder_person_id = p.id)
                                                                    AS motions_moved_or_seconded,

    -- Claim record — what they are on record as having said.
    (SELECT count(*) FROM claims c WHERE c.actor_id = p.id)         AS claims,
    (SELECT count(*) FROM commitments cm WHERE cm.committed_by_person_id = p.id)
                                                                    AS commitments_made,

    -- CROSS-SOURCE PRESENCE. The answer to "where else is this person named?" — the
    -- distinct source types they appear in. One appearance in one source is a mention;
    -- the same person across video, minutes, and a docket is a record.
    (SELECT array_agg(DISTINCT d.doc_type) FROM claims c
       JOIN documents d ON d.id = c.document_id WHERE c.actor_id = p.id)
                                                                    AS claim_source_types
FROM persons p;

COMMENT ON VIEW v_person_dossier IS
  'Compiled record for one person: identity and aliases, voiceprint enrollment, '
  'memberships and affiliations, speaking record with identification methods, '
  'legislative record from Legistar, and cross-source claim presence. Feeds '
  'page_type = ''person''. Derived — never write to it.';

