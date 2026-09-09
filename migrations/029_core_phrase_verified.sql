-- 029: the mention_method term the code has been writing all along.
--
-- `core_phrase_verified` exists in the live database with approved_by='schema-v0.4' and in NO
-- file -- not schema/, not migrations/. Migration 023 created the mention_method vocabulary
-- and seeded three terms; this was never one of them. It was inserted by hand.
--
-- WHY THAT MATTERS MORE THAN IT LOOKS. pipeline/detect_mentions.py writes it as the `method`
-- on every mention found by the core-phrase tier -- 46 rows today. The vocabulary is OPEN, so
-- a database rebuilt from the canonical files would not reject those inserts loudly: the
-- trigger would fold each one to the fallback and file a proposal nobody reads, and an entire
-- evidence tier would quietly stop being distinguishable from the others.
--
-- The term is legitimate; only its provenance was missing. This is that provenance.

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('mention_method', 'core_phrase_verified',
   'The subject''s name minus its leading verb occurs in the verbatim, or its identity words '
   'occur together within a short window in any order, and a model confirmed the sentence '
   'refers to the subject. String matching located it; the model only ruled on meaning.',
   'schema-v0.4')
ON CONFLICT DO NOTHING;
