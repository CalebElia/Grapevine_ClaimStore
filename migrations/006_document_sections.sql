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

-- Vocabulary seeds. Semi-open per Rule 1: the trigger proposes rather than rejects, so
-- these are a starting set and not a closed list.
--
-- The VOCABULARY has to be registered before its terms: vocabulary_terms.vocabulary is a
-- foreign key into vocabularies, and inserting terms alone fails with a constraint error
-- rather than quietly creating the vocabulary. is_open FALSE because a parse verdict is a
-- gate -- a new severity should be a deliberate decision, not something extraction invents
-- and the trigger accepts. fallback_term is the value that BLOCKS, so an unrecognised
-- verdict fails closed like every other unknown in this pipeline.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
    ('parse_confidence',
     'Whether a document section has been checked well enough to extract claims from. '
     'Extraction accepts only ''clean''.',
     FALSE, 'unaudited')
ON CONFLICT DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('parse_confidence', 'unaudited',
     'Default. Nothing has checked this section; extraction refuses it.', 'migration-006'),
    ('parse_confidence', 'clean',
     'Checked and readable. The only value extraction accepts.', 'migration-006'),
    ('parse_confidence', 'suspect',
     'A check fired. May be staged but cannot publish.', 'migration-006'),
    ('parse_confidence', 'known_incomplete',
     'Content is provably missing and is not machine-recoverable -- Year 2''s grant table. '
     'Marked permanently so a partial list is never presented as a whole one.',
     'migration-006')
ON CONFLICT DO NOTHING;

COMMIT;
