-- 027: the vocabulary that figure_data_points.model_confidence has always named.
--
-- Migration 005 created the column annotated `-- vocab: model_confidence` and never created
-- the vocabulary, so no trigger was ever attached and the column has accepted anything since.
-- 45 rows are populated, all `high` or `medium`, so nothing invalid got in -- but nothing was
-- stopping it, and the annotation asserted otherwise.
--
-- IT SURFACED ONLY WHEN THE SCHEMA WAS MADE WHOLE. document_figures and figure_data_points
-- lived solely in migrations/, so the structure tests that read claim_store.sql never saw the
-- annotation to check it. Folding the migrations in is what made the gap visible -- which is
-- the argument for tests/test_schema_drift.py in one example.

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('model_confidence',
   'How confident a model was reading a value off a figure. Open, because a later extractor '
   'may report a granularity these four do not cover.',
   TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('model_confidence','high','the value is legible in the figure','schema-v0.5'),
  ('model_confidence','medium','the value was inferred from axis and position','schema-v0.5'),
  ('model_confidence','low','a reading worth re-checking against the page','schema-v0.5'),
  ('model_confidence','unknown','not recorded','schema-v0.5')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_model_confidence
    BEFORE INSERT OR UPDATE ON figure_data_points
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('model_confidence','model_confidence');
