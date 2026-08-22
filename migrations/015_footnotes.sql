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

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('marker_evidence', 'How a footnote marker was established', TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('marker_evidence','printed','the marker survived the conversion as a legible numeral','schema-v0.2'),
  ('marker_evidence','second_read','recovered from a second independent read of the pixels','schema-v0.2'),
  ('marker_evidence','text_layer','recovered from the PDF text layer','schema-v0.2'),
  ('marker_evidence','human','a person read the page and said so','schema-v0.2'),
  ('marker_evidence','unknown','not established','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_footnote_ref BEFORE INSERT OR UPDATE ON footnote_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('marker_evidence','marker_evidence');
