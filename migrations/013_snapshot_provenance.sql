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

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('provenance_source', 'How a provenance value was established', TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('provenance_source','retrieved','downloaded by the pipeline; URL and timestamp are recorded','schema-v0.2'),
  ('provenance_source','local_file','hashed from a file on disk with no retrieval record','schema-v0.2'),
  ('provenance_source','human_supplied','a person stated it','schema-v0.2'),
  ('provenance_source','unknown','not established','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_documents_snapshot BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('provenance_source','snapshot_source');

-- A snapshot hash with no path cannot be re-checked, and a path with no hash proves
-- nothing. They travel together or not at all.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_snapshot_pair;
ALTER TABLE documents ADD CONSTRAINT documents_snapshot_pair
  CHECK ((snapshot_path IS NULL) = (snapshot_hash IS NULL));
