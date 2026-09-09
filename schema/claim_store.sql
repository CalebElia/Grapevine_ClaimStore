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
    notes               TEXT,
    -- SAME PROGRAMME OR BODY, RECORDED TWICE. The wiki's actor files hold both `Ann Arbor
    -- SPARK` and `SPARK Ann Arbor`. Rows are repointed to the survivor and this one is kept
    -- as a TOMBSTONE, never deleted: resolve_orgs seeds from ../a2zero-wiki (read-only), so a
    -- deleted duplicate is recreated on the next re-seed. See migrations/026.
    merged_into_id      INT REFERENCES orgs(id),
    merged_by           TEXT,                   -- human; merging is an identity decision
    merged_at           TIMESTAMPTZ,
    merge_note          TEXT,
    CONSTRAINT orgs_merge_not_self
        CHECK (merged_into_id IS NULL OR merged_into_id <> id),
    CONSTRAINT orgs_merge_attributed
        CHECK (merged_into_id IS NULL OR (merged_by IS NOT NULL AND merged_at IS NOT NULL))
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
    -- What sort of thing this is. Without it, telling a strategy from an initiative from a
    -- neighbourhood needs a walk up parent_subject_id or a guess from wiki_slug.
    subject_kind        TEXT,                   -- vocab: subject_kind
    -- Cross-case classification. NOT BINARY — Boulder failed its formal objective
    -- while extracting major concessions. A single success/failure field erases that.
    formal_objective_achieved TEXT,             -- vocab: objective_outcome
    collateral_gains    TEXT,                   -- vocab: collateral_gains
    outcome_notes       TEXT,
    created_by          TEXT NOT NULL,          -- human; a cluster id is never a key
    created_at          TIMESTAMPTZ DEFAULT now(),
    -- SAME PROGRAMME, RECORDED TWICE. The wiki holds separate pages for 'Electrify City
    -- Fleet', 'City Fleet Electrification' and 'City EV Fleet'. Rows are repointed to the
    -- survivor and this one is kept as a TOMBSTONE, never deleted: initiatives.py resolves a
    -- wiki page by slug or name, ../a2zero-wiki is read-only and still holds every page, so a
    -- deleted duplicate is silently recreated on the next re-seed. See migrations/024.
    merged_into_id      INT REFERENCES subjects(id),
    merged_by           TEXT,                   -- human; merging is an identity decision
    merged_at           TIMESTAMPTZ,
    merge_note          TEXT,
    CHECK (parent_subject_id IS NULL OR parent_subject_id <> id),
    CONSTRAINT subjects_merge_not_self
        CHECK (merged_into_id IS NULL OR merged_into_id <> id),
    CONSTRAINT subjects_merge_attributed
        CHECK (merged_into_id IS NULL OR (merged_by IS NOT NULL AND merged_at IS NOT NULL))
);

CREATE INDEX idx_subjects_parent ON subjects(parent_subject_id);
CREATE INDEX idx_subjects_topic  ON subjects(topic_id);
CREATE INDEX idx_subjects_merged_into ON subjects(merged_into_id)
    WHERE merged_into_id IS NOT NULL;

-- ONE HOP, ALWAYS. If A merges into B and B into C, a reader following one hop lands on a
-- tombstone. Refusing chains means no consumer needs a recursive CTE to resolve a subject.
-- ONE HOP, ALWAYS, and one definition for both tables. Generic over TG_TABLE_NAME so
-- subjects and orgs cannot drift into two different ideas of what a legal merge is.
CREATE OR REPLACE FUNCTION enforce_merge_target() RETURNS TRIGGER AS $$
DECLARE
    target_is_merged BOOLEAN;
    is_a_survivor    BOOLEAN;
BEGIN
    IF NEW.merged_into_id IS NULL THEN
        RETURN NEW;
    END IF;
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND merged_into_id IS NOT NULL)',
        TG_TABLE_NAME) INTO target_is_merged USING NEW.merged_into_id;
    IF target_is_merged THEN
        RAISE EXCEPTION '% % is itself merged; point the merge at its survivor instead',
            TG_TABLE_NAME, NEW.merged_into_id;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE merged_into_id = $1)',
        TG_TABLE_NAME) INTO is_a_survivor USING NEW.id;
    IF is_a_survivor THEN
        RAISE EXCEPTION '% % is the survivor of another merge and cannot itself be merged',
            TG_TABLE_NAME, NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_subject_merge_target
    BEFORE INSERT OR UPDATE OF merged_into_id ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_merge_target();

-- What every candidate list should read. Selecting from `subjects` puts tombstones back into
-- review queues, which is the problem this was built to end.
CREATE OR REPLACE VIEW live_subjects AS
    SELECT * FROM subjects WHERE merged_into_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_orgs_merged_into ON orgs(merged_into_id)
    WHERE merged_into_id IS NOT NULL;

CREATE OR REPLACE TRIGGER trg_org_merge_target
    BEFORE INSERT OR UPDATE OF merged_into_id ON orgs
    FOR EACH ROW EXECUTE FUNCTION enforce_merge_target();

CREATE OR REPLACE VIEW live_orgs AS
    SELECT * FROM orgs WHERE merged_into_id IS NULL;

-- Other names an org is published under, including the name of any org merged into it.
-- resolve_orgs matches claim text against these as well as the canonical name, so a merge
-- never costs the store a name that documents actually print.
CREATE TABLE org_aliases (
    id          SERIAL PRIMARY KEY,
    org_id      INT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    alias       TEXT NOT NULL,
    alias_type  TEXT,                        -- vocab: alias_type
    UNIQUE (org_id, alias)
);
CREATE INDEX idx_org_aliases_org ON org_aliases(org_id);

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

-- ── what a claim NAMES (see migrations/023) ───────────────────────────────────────────
CREATE TABLE claim_subject_mentions (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    subject_id          INT NOT NULL REFERENCES subjects(id),

    -- Offsets into claims.verbatim, not into the canonical text: a mention is a property of
    -- the quoted sentence, and stays valid however the document is later re-rendered.
    span_start          INT NOT NULL,
    span_end            INT NOT NULL,
    -- What actually appeared, in the document's own casing. "community choice aggregation"
    -- and "Community Choice Aggregation" are the same subject and different text, and which
    -- one the page printed is worth keeping.
    matched_text        TEXT NOT NULL,

    method              TEXT NOT NULL,          -- vocab: mention_method
    detected_by         TEXT NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A human ruling on a machine's proposal. NULL means nobody has looked, which is not the
    -- same as nobody agreeing.
    confirmed_by        TEXT,
    confirmed_at        TIMESTAMPTZ,

    -- One row per (claim, subject, position). A sentence naming an initiative twice records
    -- both, because the second occurrence is also evidence.
    UNIQUE (claim_id, subject_id, span_start),
    CHECK (span_end > span_start),
    CHECK (confirmed_by IS NULL OR confirmed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS claim_mentions_claim_idx   ON claim_subject_mentions (claim_id);
CREATE INDEX IF NOT EXISTS claim_mentions_subject_idx ON claim_subject_mentions (subject_id);


-- ══════════════════════════════════════════════════════════════════════════════════════
-- FOLDED-IN MIGRATIONS
--
-- `scripts/db.sh reset` applies THIS FILE and vocabularies.sql, and never replays
-- migrations/. Everything below therefore has to live here, or a rebuilt database silently
-- lacks it -- which it did: seventeen migrations were unfolded and nine tables existed only
-- in migrations/, `document_sections` among them, the table every claim joins through.
--
-- Kept as the migrations' own text rather than woven into the CREATE TABLE statements above,
-- because each carries the reasoning for the change it makes and that reasoning is the most
-- valuable part of it. tests/test_schema_drift.py builds one database from the canonical
-- files and another from canonical-plus-migrations and fails on any difference, so this
-- section cannot silently fall behind again.
--
-- DATA STATEMENTS ARE DELIBERATELY NOT HERE. A few migrations seed rows -- 012 inserts an
-- A2ZERO alias -- which need referents a freshly-built schema has none of. A canonical schema
-- describes shape; contents come from the pipeline. Those statements stay in migrations/ and
-- are listed at the end of this section.
-- ══════════════════════════════════════════════════════════════════════════════════════

-- ─── 004_media_linkage.sql ───
-- 004 — media linkage: fix two defects, and remove the class they belong to.
--
-- Both were found while selecting a starting corpus, and both would have corrupted the
-- transcript layer at ingest — the layer every later claim rests on.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- DEFECT 1 — a video linked to the wrong meeting, by the STRONGER evidence chain.
--
-- IkZ4APPWNgY was attached to the 2026-05-12 Sustainability Commission meeting by
-- `legistar_calendar` — the city asserted the link by publishing it. Ground truth from
-- the host says otherwise:
--
--     IkZ4APPWNgY   7,426s   uploaded 2026-04-24   "…Sustainability Commission Meeting 4/14/26"
--
-- It is the April 14 meeting, and it was ALSO correctly attached to 2026-04-14 by
-- `host_channel` — the weaker chain, the one we inferred ourselves. Ingested naively,
-- every claim from that meeting is misdated by four weeks, and chronology is the join key
-- across every source in this store.
--
-- The real 2026-05-12 recording exists and was never ingested:
--
--     bc7m_xHhLSo   4,860s   uploaded 2026-05-13   "…Sustainability Commission Meeting - May 12, 2026"
--
-- ROOT CAUSE, and the reason this is not a one-off: the host_channel matcher recognises
-- titles in `M/D/YY` form. CTN publishes some meetings as `- Month D, YYYY` instead, and
-- those are invisible to it. The 2026-05-12 recording sat unlinked for that reason alone
-- while a wrong video occupied its row. Any future discovery pass must accept both forms.
-- (Checked: 2025-08-12 has genuinely no published recording — a real absence, not this bug.)
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- DEFECT 2 — four videos on one event, three of them seconds long.
--
-- 2026-01-13 carries four YouTube ids with IDENTICAL titles, all uploaded 2026-01-14:
--
--     2sc4VfsrS8g   6,398s   the meeting
--     BM-w9MgRNik      59s   aborted stream
--     bLpyad0up9o      42s   aborted stream
--     PHet5NGdPG0       1s   aborted stream
--
-- Not a multi-part recording — CTN restarted the stream three times. This is live and
-- dangerous: a `string_agg`-based pick of "the video for this event" selected the
-- 42-SECOND CLIP for the recommended starting set.
--
-- The stubs are NOT deleted. They exist, they are real uploads, and a row silently
-- removed is a fact nobody can re-check later. Instead durations are recorded and
-- `v_meeting_recordings` picks the longest recording per event, so every consumer gets
-- the meeting without needing to know this happened.
--
-- Deliberately NO minimum-duration threshold in that view: a threshold is a magic number
-- that fails the first time a commission adjourns in six minutes. Longest-per-event needs
-- no such constant and cannot be wrong for the reason a threshold would be.
--
-- Idempotent: keyed on external_id and event_date, never on serial ids. Safe to re-run.

BEGIN;

-- ── The structural fix ──────────────────────────────────────────────────────────────
-- One row per event: the longest recording. Everything downstream — transcription,
-- span selection, review-budget estimates — should read this, never media_assets
-- directly, so that a duplicate or a stub upload can never again be mistaken for a
-- meeting. `others` is exposed rather than hidden: a non-zero value is a linkage
-- question worth someone's attention, not noise to be suppressed.
CREATE OR REPLACE VIEW v_meeting_recordings AS
SELECT DISTINCT ON (m.event_id)
       m.event_id,
       e.event_date,
       b.name              AS body_name,
       m.id                AS media_asset_id,
       m.host,
       m.external_id,
       m.url,
       m.duration_seconds,
       m.host_title,
       m.host_upload_date,
       m.title_stated_date,
       m.date_verification,
       m.discovered_via,
       m.asr_model,
       m.asr_completed_at,
       count(*) OVER (PARTITION BY m.event_id) - 1 AS others
  FROM media_assets m
  JOIN events e ON e.id = m.event_id
  JOIN bodies b ON b.id = e.body_id
 ORDER BY m.event_id, m.duration_seconds DESC NULLS LAST, m.id;

COMMENT ON VIEW v_meeting_recordings IS
  'One recording per event: the longest. Guards against duplicate and aborted-stream '
  'uploads (2026-01-13 has four, three of them under a minute). Read this, not '
  'media_assets, when you need "the video for this meeting". `others` counts the '
  'additional assets on the same event.';

COMMIT;

-- ─── 005_document_figures.sql ───
-- 005 — Figure extractions and harvested links.
--
-- ANSWERS A QUESTION THAT HAD NOT BEEN ANSWERED. The vision pass reads real numbers off
-- charts (31 cross-validated data points from the Year 5 dashboard alone), but nothing
-- said where they LAND. Saying only "model output stays out of the citation spine" left
-- the implication that the extraction was wasted. It is not — it lands here.
--
-- WHY FIGURE DATA IS NOT A `claims` ROW.
-- The load-bearing guarantee on `claims` is that `verbatim` can be located
-- character-for-character in the source conversion; a claim that fails that check is
-- discarded rather than stored. A chart value has no such source text. "2.33M" was never
-- in the PDF's text layer — it is a model's transcription of pixels. Writing it into
-- `claims` would mean either a NULL span (a claim exempt from the one check that makes
-- claims trustworthy) or a fabricated one. Both defeat the guard.
--
-- So chart readings are typed EVIDENCE, not claims. They reach the timeline the same way
-- any other source does — by attesting to an `asserted_event` — but with their own
-- attestation_type, so corroboration logic can tell "the report SAID this in prose" from
-- "a model READ this off a chart."
--
-- AND THAT DISTINCTION IS AN ASSET, NOT A CONCESSION. Year 5's GHG prose says emissions
-- must fall "from over 2.1 million metric tons"; the chart on the same page reads 2.33M
-- for 2015 and 1.97M for 2023. Those are independent extraction paths over the same
-- underlying fact — exactly the independence `event_attestations` exists to measure, and
-- exactly the input a numeric-drift check needs. Collapsing them into one claim would
-- destroy the disagreement that makes them useful.

BEGIN;

-- ── figures ────────────────────────────────────────────────────────────────────────────
CREATE TABLE document_figures (
    id                  SERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no             INT NOT NULL,
    -- Docling's own bbox, PDF points, BOTTOMLEFT origin. Kept so a crop is reproducible
    -- from the source rather than trusted from a PNG that may drift out of sync.
    bbox                JSONB NOT NULL,
    -- What made this image worth a paid vision call in the first place.
    classifier_label    TEXT NOT NULL,
    classifier_conf     NUMERIC(4,3),
    caption             TEXT,                   -- the document's own caption, when linked
    crop_path           TEXT,
    crop_dpi            INT,
    -- 600dpi cropping is what moved this model from "correctly declines to guess" to 31
    -- real data points, so the resolution is provenance, not a tuning detail.
    extracted_by        TEXT NOT NULL,          -- deployment id, e.g. 'gpt-5.6-sol'
    prompt_version      TEXT NOT NULL,          -- which prompt produced this reading
    raw_xml             TEXT NOT NULL,          -- the model's full response, unedited
    extracted_at        TIMESTAMPTZ NOT NULL,
    -- Ties the figure to the exact conversion whose page numbering it refers to.
    source_content_hash TEXT,
    CHECK (length(trim(raw_xml)) > 0)
);
CREATE INDEX idx_figures_document ON document_figures(document_id);

-- ── one row per <point> ────────────────────────────────────────────────────────────────
CREATE TABLE figure_data_points (
    id                  BIGSERIAL PRIMARY KEY,
    figure_id           INT NOT NULL REFERENCES document_figures(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,
    value_text          TEXT NOT NULL,          -- as printed/read: '2.33M', '711'
    value_numeric       NUMERIC,                -- parsed when unambiguous, else NULL
    unit                TEXT,
    -- MODEL SELF-REPORT, NEVER A PROPERTY OF THE DATUM. Flagged in review: a
    -- confidence="high" attribute sitting beside a value invites a reader (or a later
    -- extraction step) to treat it as something the chart asserted. The chart asserts a
    -- number; the model asserts how well it could read it. Separate column, separate
    -- meaning, and it can never travel inside a value.
    model_confidence    TEXT,                   -- vocab: model_confidence (high|medium|low)
    -- A CHART AXIS IS A TIME INTERVAL, AND A PARTIAL YEAR IS NOT A FULL ONE. Raised in
    -- review: Year 5 reports 20 solar installations for 2025 against 250 for 2024 —
    -- because the report was written mid-2025. Without an explicit interval that reads
    -- as a 92% collapse. Per CLAUDE.md, timeline queries compare intervals, never a
    -- start alone.
    period_start        DATE,
    period_end          DATE,
    period_is_partial   BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (length(trim(value_text)) > 0)
);
CREATE INDEX idx_figpoints_figure ON figure_data_points(figure_id);

-- Chart readings attest to events like any other source; only the TYPE differs.
ALTER TABLE event_attestations
    ADD COLUMN figure_data_point_id BIGINT REFERENCES figure_data_points(id);

-- ── harvested links: the source-discovery queue ────────────────────────────────────────
-- The corpus names its own next sources. Year 5 carries 81 links across 23 hosts, 77 of
-- them anchored to the exact character span of the sentence citing them. Storing the
-- anchor and its sentence is what keeps this answerable a year later: "which claim relied
-- on this URL, in which reading of which document."
CREATE TABLE document_links (
    id                  BIGSERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    uri                 TEXT NOT NULL,
    anchor_text         TEXT NOT NULL,
    context_sentence    TEXT,
    page_no             INT,
    char_start          INT,
    char_end            INT,
    -- FALSE means the anchor could not be tied to a text span (4 of 81 on Year 5, all
    -- from link rects spanning a column gutter). Recorded rather than dropped, and never
    -- guessed: a wrong span would attribute a URL to a sentence that never cited it.
    located             BOOLEAN NOT NULL DEFAULT FALSE,
    source_content_hash TEXT,
    harvested_at        TIMESTAMPTZ NOT NULL,
    -- HARVESTED IS NOT VISITED. Whether this URL was ever retrieved, and what it said, is
    -- a separate decision with separate provenance. Dark matter is a lead queue, never a
    -- finding.
    fetched_at          TIMESTAMPTZ,
    CHECK (length(trim(uri)) > 0),
    CHECK (length(trim(anchor_text)) > 0)
);
CREATE INDEX idx_doclinks_document ON document_links(document_id);
CREATE INDEX idx_doclinks_uri      ON document_links(uri);

COMMIT;

-- ─── 006_document_sections.sql ───
-- 006 — document_sections, and the columns that pin a span to the conversion it came from.
--
-- 005 built document_figures and document_links. document_sections was designed in PLAN.md
-- and never created, so there is currently nowhere to record WHERE in a document a claim
-- came from, only which document. This is that table, plus four columns on `documents`
-- that exist for one reason: a conversion is a moving target, and a span is only meaningful
-- against the exact text it was measured in.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- WHY document_sections IS A NEW TABLE AND NOT A GENERALISED `segments`.
--
-- `segments` is event_id NOT NULL with start_ms/end_ms NOT NULL. Making four columns
-- nullable to admit documents would weaken the video path's constraints to serve the text
-- path -- the two share a shape, not a set of guarantees. What earns its place is mirrored
-- (sequence, section_topic, extraction_tier, summary) on char offsets plus a page range.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- WHY parse_confidence DEFAULTS TO 'unaudited' AND NOT 'clean'.
--
-- Every failure this corpus produced was silent. pdfplumber returned 234 words for Year 2
-- and exited cleanly. Two sentences shipped woven together through a gate whose every check
-- was quantitative. A section that has not been checked must not behave like one that has,
-- so the default is the value that BLOCKS extraction, and something has to actively earn
-- 'clean'. Silence is never evidence of a clean parse.
--
-- It is TEXT against the vocabulary system rather than a CHECK constraint, per Rule 1: a
-- new severity should be an INSERT, not a migration.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- WHY documents GAINS converter, converter_version AND content_hash SEMANTICS.
--
-- claims.span_start/span_end are offsets into a string. Which string is not currently
-- recorded anywhere, and the answer changes: this pipeline altered its output on nine
-- separate commits in one working session. Without naming the conversion, a converter
-- upgrade does not invalidate old spans -- it silently repoints them at different words,
-- and every one still round-trips against the text it was written from.
--
-- documents.content_hash already exists. What 006 adds is the ability to say WHICH
-- CONVERTER produced the text that hash covers, so an upgrade is detectable rather than
-- merely survivable.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- WHY parse_verdict IS STORED ON THE DOCUMENT.
--
-- quality_gate returns PASS, REVIEW or REFUSE, and REFUSE means the conversion is not
-- readable enough to anchor claims against. Today that verdict lives only in a comment in
-- the markdown, so nothing downstream can act on it. Stored here, extraction can refuse a
-- document the gate refused, and an override has to be recorded rather than implied.

BEGIN;

CREATE TABLE document_sections (
    id                  SERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence            INT NOT NULL,           -- 0-based, in reading order
    heading             TEXT,                   -- the ## text; NULL for front matter
    section_topic       TEXT,                   -- vocab: section_topic
    char_start          INT NOT NULL,           -- into the CANONICAL text (see below)
    char_end            INT NOT NULL,
    page_start          INT,
    page_end            INT,
    extraction_tier     CHAR(1) NOT NULL DEFAULT 'C'
                        CHECK (extraction_tier IN ('A', 'B', 'C')),
    tier_assigned_by    TEXT,
    summary             TEXT,

    -- Hash of THIS section's canonical text. A document-level hash cannot tell you that
    -- section 7 changed while the rest did not, which is what a re-conversion needs.
    content_hash        TEXT NOT NULL,

    -- Fail-closed. Extraction refuses anything that is not 'clean'.
    parse_confidence    TEXT NOT NULL DEFAULT 'unaudited',   -- vocab: parse_confidence
    parse_flags         JSONB,                  -- every check that fired, with its evidence
    parse_reviewed_by   TEXT,
    parse_reviewed_at   TIMESTAMPTZ,

    UNIQUE (document_id, sequence),
    CHECK (char_end > char_start)
);

CREATE INDEX document_sections_document_idx ON document_sections (document_id, sequence);
CREATE INDEX document_sections_tier_idx     ON document_sections (extraction_tier)
    WHERE extraction_tier IN ('A', 'B');

-- Which section a claim came from. Nullable, because a claim from the video path has none.
ALTER TABLE claims ADD COLUMN document_section_id INT REFERENCES document_sections(id);
CREATE INDEX claims_document_section_idx ON claims (document_section_id)
    WHERE document_section_id IS NOT NULL;

-- THE SPAN'S COORDINATE SPACE, NAMED. Without these a span is an integer with no frame of
-- reference, and the failure mode is not an error -- it is a citation that points at the
-- wrong words while round-tripping perfectly against the text it was written from.
ALTER TABLE documents ADD COLUMN converter             TEXT;   -- 'docling+pdfplumber'
ALTER TABLE documents ADD COLUMN converter_version     TEXT;   -- git sha of the pipeline
ALTER TABLE documents ADD COLUMN parse_verdict         TEXT;   -- PASS | REVIEW | REFUSE
ALTER TABLE documents ADD COLUMN parse_override_reason TEXT;   -- required if REFUSE

-- A refused conversion may only be stored with a reason on the record. This is the schema
-- half of orchestrate_blocks' --allow-refused: the override survives into the store rather
-- than living in one operator's shell history.
ALTER TABLE documents ADD CONSTRAINT documents_refusal_needs_a_reason
    CHECK (parse_verdict IS DISTINCT FROM 'REFUSE' OR parse_override_reason IS NOT NULL);

COMMIT;

-- ─── 007_period_provenance.sql ───
-- 007 — where a document's coverage period came from.
--
-- documents.covers_period_start/end exist because the wiki lost a report's period and
-- dated every claim in it by publication instead. What was missing is how the value was
-- ARRIVED AT, and on this corpus that varies more than expected across five documents of
-- one series by one publisher:
--
--   Year 3, 4   the document prints a range           -> 'stated'
--   Year 5      the document prints a range           -> 'stated'
--   Year 2      the document prints "2021 - 2022"     -> a year pair, not two dates
--   Year 1      the document prints nothing at all
--
-- Years 1 and 2 were resolved by a human to FY2021 and FY2022 -- Ann Arbor's fiscal year
-- is July 1 to June 30 per the City Charter, and A2ZERO was adopted in June 2020, so the
-- first report covers 2020-07-01 to 2021-06-30. That is a good inference and it is still
-- an inference, so it must not be queryable as though the report said it.
--
-- A period that a human supplied is evidence of a DIFFERENT KIND from one the document
-- printed, and the store's whole argument is that the difference is recorded rather than
-- averaged away. Without this column, a timeline query cannot tell a date the City
-- published from a date we decided was probably right.
--
-- Years 3 and 4 stay 'stated' even though both print "June 3" where the page means
-- June 30. That is the source's typo, faithfully carried; correcting it would be a third
-- provenance ('corrected'), and nobody has made that ruling.

BEGIN;

ALTER TABLE documents ADD COLUMN covers_period_source TEXT;  -- vocab: covers_period_source
ALTER TABLE documents ADD COLUMN covers_period_note   TEXT;  -- who decided, and why

-- A human estimate must say who made it. A stated period needs no such defence.
ALTER TABLE documents ADD CONSTRAINT documents_estimate_needs_a_note
    CHECK (covers_period_source IS DISTINCT FROM 'human_estimate'
           OR covers_period_note IS NOT NULL);

COMMIT;

-- ─── 009_measure_vocab_and_dates.sql ───
-- 009 — make `measure` mean something, let a claim be dated, and fix coalition_members.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- quantity_measure HAD NO TERMS AT ALL.
--
-- quantities.measure is declared `vocab: quantity_measure` and that vocabulary was never
-- created, so the first extraction run wrote 'other' five times against a term that does
-- not exist. unit and measure answer different questions and the difference is what makes
-- numbers comparable:
--
--     unit     what you count IN        metric tons · households · MW · USD
--     measure  what is being COUNTED    emissions_reduced · households_served
--
-- "113 metric tons of carbon emissions reduced" and "2.1 million metric tons of
-- community-wide emissions" share a unit and must never be summed, because one is a
-- reduction and the other is a total. Only `measure` can say so.
--
-- Open, because this list will not survive contact with a docket or a minutes corpus and
-- the trigger should propose rather than reject.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- A CLAIM WITH NO DATE IS INVISIBLE FOREVER.
--
-- All 17 claims from Year 3 section 5 carried asserted_start IS NULL, because no sentence
-- states when it happened -- and the report covers July 2022 to June 2023, so every one of
-- them IS dated, at the document's precision. Chronology is the join key across every
-- source in this store and timeline queries compare INTERVALS, so a NULL start cannot
-- participate in any of them: not wrong, simply absent.
--
-- `reporting_period` is added to date_precision to say exactly what such a date is. It is
-- not `year` (Year 5 runs June to May) and not `fiscal_year` (only Years 1-4 do), and
-- pretending otherwise would make a claim look more precisely dated than it is.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- coalition_members COULD NAME A PERSON AND AN ORG AT ONCE.
--
-- Flagged in Collin's review and deferred. A membership row is one member: a person, an
-- org or a body. Permitting two makes "who was in this coalition" ambiguous per row, and
-- this table is about to matter -- it is how a claim naming both the City and the Ann
-- Arbor Housing Commission records the second actor.

BEGIN;
ALTER TABLE coalition_members ADD CONSTRAINT coalition_members_one_member
    CHECK (num_nonnulls(person_id, org_id, body_id) = 1);

COMMIT;

-- ─── 011_funder_name_text.sql ───
-- 011: keep the funder's name as the document wrote it, whether or not it resolves.
--
-- WHAT WAS LOST. The extraction asked the model for `funder_name` -- "U.S Department of
-- Energy", "MI-HOPE", exactly as the text names them -- passed it to a registry lookup, and
-- kept only the resulting org id. When the registry had no such org the NAME WENT WITH IT.
-- The store then held a $500,000 award with no funder, from a sentence that names one.
--
-- WHY THAT IS WORSE THAN IT SOUNDS. research_questions asks "who funded this?" for money
-- with no source. Discarding an unresolved name turns a registry gap into a fabricated
-- research question: a human gets sent hunting for a fact printed in the document they
-- already have. Dark matter is supposed to be what the corpus cannot answer.
--
-- WHY A TEXT COLUMN AND NOT ONLY A FOREIGN KEY. They answer different questions.
-- awarding_org_id is the join -- "every award SEMCOG made" -- and must stay exact, because
-- a funder attributed to the wrong body says something false about who paid. This column
-- is EVIDENCE: what the document actually said, unresolved, still true if the registry is
-- wrong and still there if the registry later grows. A name here with a NULL id is a
-- registry gap, and now a findable one.
ALTER TABLE fiscal_references ADD COLUMN IF NOT EXISTS funder_name_text text;

COMMENT ON COLUMN fiscal_references.funder_name_text IS
  'The funder as the document names it, verbatim. Never normalised. Populated even when '
  'awarding_org_id could not be resolved -- a name here with a NULL id is a registry gap, '
  'not an unfunded award.';

-- ─── 012_funding_programs_and_a2zero_alias.sql ───
-- 012: the named program between a funder and what it funded; and A2ZER0 as A2ZERO.
--
-- ============================================================================
-- PART 1 -- THE MISSING MIDDLE
-- ============================================================================
-- Caleb: "how can we capture this relationship where the Organization (DOE) has a program
-- (EECBG) that's funded a subject (Bryant decarbonization)?"
--
-- Two of the three already have homes: fiscal_references.awarding_org_id is the body, and
-- fiscal_references.subject_id is what the money went to. The PROGRAM has none.
--
-- funding_instrument IS NOT ITS HOME, AND THAT MISTAKE HAS ALREADY BEEN MADE ONCE THIS
-- WEEK. funding_instrument is a category slot -- block grant, formula grant, revolving loan
-- -- exactly parallel to funding_source, which is a category of money. Putting "Energy
-- Efficiency and Conservation Block Grant" in it repeats the bug migration 011 fixed: the
-- store read funding_source, a KIND, as though it named the giver, and asked a human to go
-- find a funder the document had printed. A named thing stuffed into a category field is
-- unqueryable as either.
--
-- So three questions, three columns:
--     awarding_org_id     WHO           U.S. Department of Energy
--     program_id          UNDER WHAT    Energy Efficiency and Conservation Block Grant
--     funding_instrument  WHAT KIND     block_grant
--     subject_id          FOR WHAT      Bryant Neighborhood Decarbonization
--
-- THIS IS NOT ONLY ABOUT EECBG. Four references already in the store name a program and
-- resolve to the administering body, losing the program on the way: "SEMCOG Carbon
-- Reduction Program", "Urban Sustainability Directors Network Emergent Learning Fund",
-- "USDN Mini-Grant", "MI-HOPE". registries/ann_arbor/funder_aliases.json maps each to its
-- org, which answers "who paid" and silently discards "under which program" -- and the
-- program is what a researcher tracks across years and cities.
CREATE TABLE IF NOT EXISTS funding_programs (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    -- THE BODY THAT RUNS IT. Nullable because a document can name a program whose
    -- administering agency it never states -- which is a research question, not a reason
    -- to refuse the row. The whole point of 011 was to stop discarding what we do know.
    administering_org_id INT REFERENCES orgs(id),
    -- A program is often a vehicle of a larger one: EECBG money reaches a city through the
    -- state energy office. Self-referencing rather than a second table.
    parent_program_id   INT REFERENCES funding_programs(id),
    abbreviation        TEXT,
    description         TEXT,
    -- Human, always. A program is a referent; a cluster id never becomes a canonical key.
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now(),
    CHECK (parent_program_id IS NULL OR parent_program_id <> id)
);
CREATE INDEX IF NOT EXISTS idx_funding_programs_org ON funding_programs(administering_org_id);

CREATE TABLE IF NOT EXISTS funding_program_aliases (
    id                  SERIAL PRIMARY KEY,
    program_id          INT NOT NULL REFERENCES funding_programs(id),
    alias               TEXT NOT NULL,
    alias_type          TEXT,                   -- vocab: alias_type
    UNIQUE (program_id, alias)
);

ALTER TABLE fiscal_references
  ADD COLUMN IF NOT EXISTS program_id INT REFERENCES funding_programs(id);

COMMENT ON COLUMN fiscal_references.program_id IS
  'The named program the money came under -- EECBG, SEMCOG Carbon Reduction Program. NOT '
  'the same as awarding_org_id (the body) or funding_instrument (the category). A program '
  'with a NULL administering_org_id is a research question, not a bad row.';

-- ─── 013_snapshot_provenance.sql ───
-- 013: say where the bytes came from, and how much we actually know about that.
--
-- THE HOLE. All five documents carried source_url, snapshot_path, snapshot_hash and
-- retrieved_at NULL. Every claim in the store cites a character offset into a markdown
-- file, converted from a PDF the store could not identify. Two copies of each PDF exist on
-- this machine -- Coding_Projects/docling-test/source_pdfs and a "docling-test copy" inside
-- Shared Repo_Cloan -- and they are byte-identical today. Nothing in the store would have
-- said so if they were not, and nothing would say so tomorrow.
--
-- A hash is what makes "this is the document we read" checkable instead of asserted.
--
-- WHY A SOURCE AND A NOTE, NOT JUST THE FIELDS. Migration 007 set the precedent for
-- covers_period: a value that was inferred must say it was inferred, or a later reader
-- cannot tell a recorded fact from a good guess. The same applies harder here. We know the
-- file we hashed. We do NOT know when it was downloaded -- the filesystem mtime is when it
-- was COPIED to this machine -- and we do not know the URL it came from. Writing an mtime
-- into retrieved_at unlabelled would manufacture a retrieval date out of a copy operation.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS snapshot_source TEXT;   -- vocab: provenance_source
ALTER TABLE documents ADD COLUMN IF NOT EXISTS snapshot_note   TEXT;

COMMENT ON COLUMN documents.snapshot_source IS
  'How snapshot_path/snapshot_hash/retrieved_at were established. local_file means the '
  'bytes were hashed from a file already on disk with no retrieval record -- the hash is '
  'trustworthy, the provenance before it is not.';

-- A snapshot hash with no path cannot be re-checked, and a path with no hash proves
-- nothing. They travel together or not at all.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_snapshot_pair;
ALTER TABLE documents ADD CONSTRAINT documents_snapshot_pair
  CHECK ((snapshot_path IS NULL) = (snapshot_hash IS NULL));

-- ─── 014_human_verdict.sql ───
-- 014: a human's judgement, kept separately from the machine's, and made to lapse.
--
-- WHAT BROKE. document_sections had ONE review slot -- parse_reviewed_by -- and both a
-- human review and a machine re-audit wrote into it. Running section_audit during the OCR
-- provenance repair replaced 'caleb' with 'machine (OCR provenance repair)' on all ten
-- Year 2 sections. A human's review of a document was destroyed by a maintenance run, and
-- nothing objected, because the column cannot tell the two apart.
--
-- WHY IT CANNOT JUST BE A BETTER STRING. The machine verdict and the human verdict answer
-- different questions and disagree ON PURPOSE:
--     parse_confidence   what the FLAGS say   Year 2 is 96% OCR, so: suspect
--     human_verdict      what a PERSON checked  "I read it against the PDF": approved
-- Collapsing them means either the machine silently overrides a person, or a person's
-- approval hides a real flag from every later reader. Both are worse than a disagreement
-- you can see.
--
-- APPROVAL LAPSES WHEN THE TEXT MOVES. human_verdict_hash records the content_hash the
-- person actually read. section_audit already refuses to audit a document whose conversion
-- moved, with the reason "a review of text that moved is not a review of the text in the
-- store" -- and that applies with more force to an approval, which is the thing that lets
-- extraction proceed. A stale approval is an unearned gate pass.
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS human_verdict      TEXT;  -- vocab: human_verdict
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS human_verdict_by   TEXT;
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS human_verdict_at   TIMESTAMPTZ;
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS human_verdict_note TEXT;
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS human_verdict_hash TEXT;

COMMENT ON COLUMN document_sections.human_verdict IS
  'A person''s judgement, independent of parse_confidence. Only valid while '
  'human_verdict_hash equals content_hash -- an approval of text that has since changed is '
  'not an approval of the text in the store.';

-- A verdict with nobody behind it is an anonymous gate pass. Same rule subjects.created_by
-- and asserted_events.named_by already enforce: a judgement names its author.
ALTER TABLE document_sections DROP CONSTRAINT IF EXISTS sections_human_verdict_attributed;
ALTER TABLE document_sections ADD CONSTRAINT sections_human_verdict_attributed
  CHECK (human_verdict IS NULL
         OR human_verdict = 'not_reviewed'
         OR (human_verdict_by IS NOT NULL AND human_verdict_hash IS NOT NULL));

-- ─── 015_footnotes.sql ───
-- 015: footnotes as structure -- a numbered body, and the prose that points at it.
--
-- WHAT THIS REPLACES. All seven Year 2 footnote bodies were captured and tagged FURNITURE,
-- which kept them out of claim-bearing prose and was right as far as it went. FURNITURE is
-- a catch-all: it cannot say that a block is a footnote, which number it carries, or which
-- section's prose refers to it. These seven name the City officer responsible for each
-- A2ZERO strategy, with an email address, so the link is the whole value.
--
-- WHY A REFERENCE TABLE AND NOT A COLUMN. A footnote body sits at the foot of one page; the
-- marker pointing at it sits in the prose above, and in general a single footnote can be
-- cited from more than one place. Recording the reference separately also lets it carry its
-- own provenance, which matters here because the markers were not READ -- they were
-- RECOVERED. Docling's OCR rendered the superscripts as a letter ("wet"), as an apostrophe
-- ("we'"), or dropped them entirely; the digit came from a second independent read. A
-- reference the store cannot explain is a reference nobody should trust.
CREATE TABLE IF NOT EXISTS footnotes (
    id                  SERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id),
    number              INT NOT NULL,
    body_text           TEXT NOT NULL,
    page_no             INT,
    -- The body's own span in the canonical text, so it is quotable like anything else.
    char_start          INT NOT NULL,
    char_end            INT NOT NULL,
    document_section_id INT REFERENCES document_sections(id),
    UNIQUE (document_id, number)
);
CREATE INDEX IF NOT EXISTS idx_footnotes_document ON footnotes(document_id);

CREATE TABLE IF NOT EXISTS footnote_references (
    id                  SERIAL PRIMARY KEY,
    footnote_id         INT NOT NULL REFERENCES footnotes(id),
    document_section_id INT NOT NULL REFERENCES document_sections(id),
    -- Where the marker stood before it was removed from the prose. The character is gone --
    -- it was OCR noise, not a word -- but the position is where the citation was made.
    char_at             INT,
    marker_evidence     TEXT,                   -- vocab: marker_evidence
    UNIQUE (footnote_id, document_section_id)
);

COMMENT ON COLUMN footnote_references.marker_evidence IS
  'How the marker was established. On an OCR-only document it is never simply READ: the '
  'superscript arrives as a letter, an apostrophe, or nothing, and the digit comes from a '
  'second independent read.';

-- ─── 016_footnote_contact.sql ───
-- 016: the person a footnote names, resolved to the person registry.
--
-- These seven footnotes each name the City officer responsible for one A2ZERO strategy.
-- Resolving the name to a persons row is what turns "a string in a page-foot block" into a
-- fact you can join: every strategy Missy Stults is accountable for, across every document
-- that says so.
--
-- SIX OF THE SEVEN WERE ALREADY IN THE STORE, imported from Legistar. Creating fresh rows
-- for them would have split each officer into two identities -- one that votes in council
-- records and one that answers questions about a strategy -- which is precisely the harm
-- `persons` exists to prevent. The seventh is the interesting one: the report writes "Missy
-- Stults" where Legistar holds "Melissa Stults". That is a nickname, and person_aliases is
-- where nicknames go; it is not a reason for a second row.
ALTER TABLE footnotes ADD COLUMN IF NOT EXISTS contact_person_id INT REFERENCES persons(id);

COMMENT ON COLUMN footnotes.contact_person_id IS
  'The person this footnote names as a contact, resolved to the person registry. NULL when '
  'the name could not be resolved to exactly one person -- a footnote attributed to the '
  'wrong officer is worse than one attributed to nobody.';

-- ─── 017_fiscal_direction.sql ───
-- 017: which way the money moved. Without it, SUM(amount_low) is not a number about anything.
--
-- MEASURED, NOT HYPOTHETICAL. After extracting 21 sections the largest fiscal_reference in
-- the store is $1,000,000,000 -- "the City has saved rate payers more than $1,000,000,000
-- through testimony and advocacy". It is not an award. Summed with grants received it turns
-- a $110M funding picture into a $1.1B one, and nothing errors, because the table had no way
-- to say that awards, savings, disbursements and authorisations are different facts.
--
-- WHAT THIS DOES NOT SOLVE, STATED PLAINLY. Direction is relative to somebody, and this
-- table has no recipient. "OSI supported TheRide in their successful grant application for
-- $25 MILLION" is money RECEIVED -- by TheRide, not by the City. Direction stops savings
-- being added to awards; it does not yet say whose award it was. A recipient_org_id is the
-- next honest step and is deliberately not smuggled in here.
ALTER TABLE fiscal_references ADD COLUMN IF NOT EXISTS direction TEXT;  -- vocab: fiscal_direction

COMMENT ON COLUMN fiscal_references.direction IS
  'Which way the money moved, from the claim''s own words. NEVER sum across directions. '
  '`unknown` means the text did not say and is not a synonym for `received`.';

-- A VIEW THAT CANNOT BE SUMMED WRONG. Any total anyone reaches for should be grouped, so
-- the grouping is provided rather than left as a thing to remember.
CREATE OR REPLACE VIEW v_money_by_direction AS
SELECT coalesce(f.direction, 'unknown') AS direction,
       count(*)        AS refs,
       sum(f.amount_low) AS total_low,
       min(f.amount_low) AS smallest,
       max(f.amount_low) AS largest
FROM fiscal_references f
GROUP BY 1;

-- ─── 018_section_subject.sql ───
-- 018: the subject a section is about, so 903 claims stop being untopiced.
--
-- THE GAP. Every claim carried a date and none carried a subject. The store could say what
-- was asserted and when, and could not group it by WHAT IT IS ABOUT -- which is the join the
-- corpus exists for. "How has Ann Arbor's solar programme progressed across five years" had
-- no answer.
--
-- ON THE SECTION, NOT ON EACH CLAIM. There are 59 sections and 903 claims. Recording the
-- decision once per section makes it auditable and reversible in 59 places; writing it 903
-- times makes it 903 things to re-derive when the mapping changes. Claims inherit, and a
-- later pass may overrule any individual one.
--
-- WHY THIS IS NOT A MODEL'S JOB. All five reports organise themselves into the same seven
-- A2ZERO strategies and title them differently every year -- "Strategy 1: Power our
-- electrical grid with 100% renewable energy", "STRATEGY ONE: POWER OUR ELECTRICAL GRID...",
-- "STRATEGY 1: 100% RENEWABLES". A section's placement is the document SAYING what it is
-- about. That is evidence, so it is decided by string matching anchored on the word
-- "STRATEGY" -- never on a digit anywhere in the heading, because "YEAR 5 PRIORITIES"
-- contains a 5 and is not Strategy 5.
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS subject_id INT REFERENCES subjects(id);

COMMENT ON COLUMN document_sections.subject_id IS
  'What this section is about, from the report''s own structure. Claims inherit it. NULL on '
  'navigational sections -- a table of contents is not about anything, and giving it a '
  'subject would put structural furniture into topic aggregates.';

CREATE INDEX IF NOT EXISTS idx_sections_subject ON document_sections(subject_id);

-- ─── 019_quantity_unit_vocabulary.sql ───

COMMENT ON COLUMN quantities.unit IS
  'The DIMENSION of the measurement, from a closed vocabulary. This is what you GROUP BY. '
  'It must never assert more than the text: bare tons stay metric_tons, because "tons of '
  'material" diverted from landfill is not CO2e.';
COMMENT ON COLUMN quantities.unit_basis IS
  'WHAT WAS COUNTED or what a percentage is OF, verbatim and never normalised: "air quality '
  'monitors", "Direct Current Fast Chargers (DCFCs)". This is the detail that makes a row '
  'worth reading; `unit` is the part that makes rows comparable.';

-- ─── 020_initiative_layer.sql ───
-- 020: the initiative layer -- kind, place, many-to-many strategy, and actors with roles.
--
-- WHAT THE SCHEMA ALREADY DECIDED, AND I NEARLY RE-DECIDED WRONGLY. I built the seven A2ZERO
-- strategies as SUBJECTS with parent_subject_id, which is a strict tree. The schema had
-- already rejected that in a comment on subject_framework_categories: "a Subject can sit in
-- A2Zero Strategy 2 AND the Comprehensive Plan's Land Use chapter AND the FY26 budget's
-- capital line simultaneously -- which a strict tree forbade." frameworks even names the
-- example: 'A2Zero CAP-2020 strategies', with code 'strategy-1'.
--
-- Caleb's framing is the same one: an initiative "pushes forward a Strategy or sometimes
-- two". A tree cannot hold that. A framework tag can.
--
-- So the division is:
--   SUBJECT             a thing in the world -- an initiative, a place, the plan itself
--   FRAMEWORK CATEGORY  one document's way of organising things -- the CAP's 7 strategies
--   subject_framework_categories   many-to-many, which is the two-strategy link
--
-- The seven strategy SUBJECTS created earlier stay for now: 903 claims point at them and
-- they are the working cross-year aggregate. They carry kind='strategy' and their alias
-- 'strategy-N' matches framework_categories.code, so the two layers join. Re-pointing claims
-- at initiatives is a later pass and needs alias matching that does not exist yet.

-- ---------------------------------------------------------------- what a subject IS
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS subject_kind TEXT;  -- vocab: subject_kind

COMMENT ON COLUMN subjects.subject_kind IS
  'What sort of thing this is. Without it, telling a strategy from an initiative from a '
  'neighbourhood needs a walk up parent_subject_id or a guess from wiki_slug.';

-- ---------------------------------------------------------------- where it happens
-- WHY NOT parent_subject_id. Bryant is where the decarbonization project HAPPENS, not what
-- it is a kind of. Putting a place in the hierarchy would make "every initiative under
-- A2ZERO" and "every initiative in Bryant" the same query shape, and they are different
-- questions -- one taxonomic, one geographic. A project can also span two places.
CREATE TABLE IF NOT EXISTS subject_places (
    subject_id          INT NOT NULL REFERENCES subjects(id),
    place_subject_id    INT NOT NULL REFERENCES subjects(id),
    -- Which claim says so, when a document rather than a registry is the source.
    claim_id            BIGINT REFERENCES claims(id),
    assigned_by         TEXT NOT NULL,
    assigned_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (subject_id, place_subject_id),
    CHECK (subject_id <> place_subject_id)
);
CREATE INDEX IF NOT EXISTS idx_subject_places_place ON subject_places(place_subject_id);

COMMENT ON COLUMN coalition_members.role IS
  'How this actor is involved -- lead, community_partner, funder. Controlled, because an '
  'uncontrolled role field cannot answer "who leads this" across a corpus.';

-- ─── 021_coalition_members_unusable.sql ───
-- 021: coalition_members could never accept a row.
--
-- THE CONTRADICTION. Its primary key is (coalition_id, person_id, org_id, body_id), and a
-- primary key forces NOT NULL on every column in it. Its CHECK requires
-- num_nonnulls(person_id, org_id, body_id) = 1 -- exactly one actor, the other two NULL.
-- Both cannot hold. Every insert fails, whichever actor you name.
--
-- The table has 0 rows, which is why nobody found it: it is the first table in this schema
-- that nothing had yet tried to write. The CHECK is the correct intent -- a member is a
-- person OR an org OR a body, never two -- so the primary key is what changes.
--
-- A surrogate key, and uniqueness expressed where NULLs are allowed. NULLS NOT DISTINCT
-- keeps the original guarantee: the same org cannot join one coalition twice, and under the
-- default NULLS DISTINCT it could, because (1, NULL, 91, NULL) never equals itself.
ALTER TABLE coalition_members DROP CONSTRAINT IF EXISTS coalition_members_pkey;

ALTER TABLE coalition_members ALTER COLUMN person_id DROP NOT NULL;
ALTER TABLE coalition_members ALTER COLUMN org_id    DROP NOT NULL;
ALTER TABLE coalition_members ALTER COLUMN body_id   DROP NOT NULL;

ALTER TABLE coalition_members ADD COLUMN IF NOT EXISTS id BIGSERIAL PRIMARY KEY;

ALTER TABLE coalition_members DROP CONSTRAINT IF EXISTS coalition_members_unique_member;
ALTER TABLE coalition_members ADD CONSTRAINT coalition_members_unique_member
  UNIQUE NULLS NOT DISTINCT (coalition_id, person_id, org_id, body_id);

-- ─── 022_section_topic.sql ───

CREATE INDEX IF NOT EXISTS document_sections_topic_idx
    ON document_sections (section_topic) WHERE section_topic IS NOT NULL;


-- Data statements left in migrations/ rather than folded in:
--   004_media_linkage.sql: UPDATE media_assets m
--   004_media_linkage.sql: UPDATE media_assets m
--   004_media_linkage.sql: UPDATE media_assets SET duration_seconds = v.dur,
--   009_measure_vocab_and_dates.sql: DELETE FROM coalition_members
--   012_funding_programs_and_a2zero_alias.sql: INSERT INTO subjects (name, description, jurisdiction_id, create
--   012_funding_programs_and_a2zero_alias.sql: INSERT INTO subject_aliases (subject_id, alias, alias_type)
--   014_human_verdict.sql: UPDATE document_sections SET parse_reviewed_by = 'caleb'
--   019_quantity_unit_vocabulary.sql: UPDATE vocabularies SET fallback_term = 'other' WHERE name = 'qu
--   019_quantity_unit_vocabulary.sql: UPDATE vocabulary_terms SET deprecated_by_term = 'percent'
--   019_quantity_unit_vocabulary.sql: UPDATE vocabulary_terms SET deprecated_by_term = 'metric_tons_co
--   019_quantity_unit_vocabulary.sql: UPDATE vocabulary_terms SET deprecated_by_term = 'metric_tons'
--   019_quantity_unit_vocabulary.sql: UPDATE vocabulary_terms SET deprecated_by_term = 'count'
