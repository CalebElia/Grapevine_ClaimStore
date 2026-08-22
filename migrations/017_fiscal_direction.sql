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

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('fiscal_direction', 'Which way money moved relative to the actor making the claim',
   TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('fiscal_direction','received','money awarded to or won by the actor','schema-v0.2'),
  ('fiscal_direction','disbursed','money the actor granted or paid out to others','schema-v0.2'),
  ('fiscal_direction','saved','costs avoided or savings claimed; no money changed hands','schema-v0.2'),
  ('fiscal_direction','spent','money the actor spent on its own activity','schema-v0.2'),
  ('fiscal_direction','authorized','committed or appropriated, not yet moved','schema-v0.2'),
  ('fiscal_direction','unknown','the text does not say','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_fiscal_direction BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('fiscal_direction','direction');

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
