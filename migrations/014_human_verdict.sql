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

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('human_verdict', 'A person''s judgement on whether a section may be extracted',
   TRUE, 'not_reviewed')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('human_verdict','approved','a person read this against the source and vouches for it','schema-v0.2'),
  ('human_verdict','approved_with_caveats','usable, but the note states a known limit','schema-v0.2'),
  ('human_verdict','rejected','a person read it and it is not usable','schema-v0.2'),
  ('human_verdict','not_reviewed','no person has looked','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_sections_human BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('human_verdict','human_verdict');

-- A verdict with nobody behind it is an anonymous gate pass. Same rule subjects.created_by
-- and asserted_events.named_by already enforce: a judgement names its author.
ALTER TABLE document_sections DROP CONSTRAINT IF EXISTS sections_human_verdict_attributed;
ALTER TABLE document_sections ADD CONSTRAINT sections_human_verdict_attributed
  CHECK (human_verdict IS NULL
         OR human_verdict = 'not_reviewed'
         OR (human_verdict_by IS NOT NULL AND human_verdict_hash IS NOT NULL));

-- RESTORE WHAT THE REPAIR RUN DESTROYED. Years 1, 3, 4 and 5 still read 'caleb'; Year 2's
-- ten sections were overwritten on 2026-08-21 and are put back. This restores the record
-- that a review HAPPENED. It deliberately does NOT grant approval -- that is a decision for
-- the reviewer, not for the migration that noticed the loss.
UPDATE document_sections SET parse_reviewed_by = 'caleb'
WHERE document_id = 9 AND parse_reviewed_by = 'machine (OCR provenance repair)';
