-- 028: three vocabulary columns that no trigger has ever guarded.
--
-- Both were annotated `-- vocab: X` and both have a seeded vocabulary. Neither had a trigger,
-- so both have silently accepted any string since the migration that created them.
--
-- `document_sections.parse_confidence` is the worse of the two by some distance. The whole
-- fail-closed rule rests on it: parse_confidence defaults to 'unaudited' and extraction
-- accepts only 'clean'. A typo'd or invented value was never rejected, never folded to the
-- fallback, and never logged a proposal -- it was simply stored, and any query comparing
-- against 'clean' would have quietly excluded the section rather than flagging it.
--
-- WHY NOW. document_sections and funding_program_aliases lived only in migrations/, so the
-- structure test that reads claim_store.sql for `-- vocab:` annotations never saw either
-- column to check it. Folding the migrations in is what made both visible.

CREATE OR REPLACE TRIGGER trg_vocab_parse_confidence
    BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('parse_confidence','parse_confidence');

CREATE OR REPLACE TRIGGER trg_vocab_funding_alias_type
    BEFORE INSERT OR UPDATE ON funding_program_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type','alias_type');

-- `documents.covers_period_source` was found the same way, and its annotation was written
-- `-- vocab` with no name -- so nothing could bind the column to the vocabulary seeded for
-- it, and the structure test had nothing to check. A CHECK constraint from migration 007
-- guards one specific value ('human_estimate' requires a note); it says nothing about the
-- rest of the vocabulary.
CREATE OR REPLACE TRIGGER trg_vocab_covers_period_source
    BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION
        enforce_vocabulary('covers_period_source','covers_period_source');
