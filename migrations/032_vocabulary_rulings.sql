-- 032: three rulings on pending vocabulary proposals, and the reason two are approvals and
-- one is not.
--
-- WHY THIS FILE EXISTS AT ALL. The rulings were made with scripts/vocab.sh, which writes to
-- the live database. `scripts/db.sh reset` applies only schema/, and
-- tests/test_schema_drift.py compares canonical against canonical-plus-migrations and never
-- against live -- deliberately, so it does not depend on a mutable thing someone may have
-- hand-edited. A term added by hand is therefore invisible to every check in the repo and
-- vanishes on the next rebuild, taking the rows with it. That is exactly how
-- mention_method.core_phrase_verified came to be live, used by the code, and claimed by no
-- file. review_vocab.py now emits one of these for every ruling.

-- ── recused -> recuse: a SPELLING, so no new term ─────────────────────────────────────────
--
-- `recuse` was already approved. vote_value has a NULL fallback_term, which deliberately
-- disables silent rewriting, so the trigger stored the raw 'recused' rather than folding it --
-- keeping the truth and flagging it. That left 9 vote rows holding a value the vocabulary does
-- not contain. Approving 'recused' would have given the store two words for one act.
UPDATE votes SET vote_value = 'recuse' WHERE vote_value = 'recused';

-- ── annual_report: a real source the vocabulary lacked ────────────────────────────────────
--
-- Distinct from `roster` (a membership list published by the body being joined) and from
-- `self_id` (a claim made in passing rather than in a periodic filing). All 7 affiliations
-- were sitting on 'other'; the proposal had been seen exactly 7 times, so every fallback row
-- came from this term and none meant "none of the above" genuinely.
INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('affiliation_source', 'annual_report',
   'The organisation''s own annual report states the affiliation. Distinct from a roster, '
   'which the body being joined publishes, and from self_id, which is a claim made in passing '
   'rather than in a periodic filing.', 'caleb')
ON CONFLICT DO NOTHING;
UPDATE affiliations SET source = 'annual_report' WHERE source = 'other';

-- ── weeks: APPROVED, never mapped ─────────────────────────────────────────────────────────
--
-- A peer of days, months and years. Mapping it to `days` would rewrite the unit and leave the
-- number, so "48 weeks" becomes 48 days -- a bug this store has already had once. review_vocab
-- refuses to map any *_unit vocabulary for that reason.
INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('quantity_unit', 'weeks',
   'A duration in weeks. A peer of days, months and years, approved rather than mapped '
   'because mapping a unit rewrites the unit and leaves the value unconverted.', 'caleb')
ON CONFLICT DO NOTHING;
UPDATE quantities SET unit = 'weeks' WHERE unit = 'other' AND verbatim ILIKE '%week%';

UPDATE vocabulary_proposals
   SET status = 'ruled', resolved_by = 'migration-032', resolved_at = now()
 WHERE status = 'pending'
   AND (vocabulary, proposed_term) IN
       (('vote_value','recused'), ('affiliation_source','annual_report'),
        ('quantity_unit','weeks'));
