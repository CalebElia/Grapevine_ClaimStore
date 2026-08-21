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

ALTER TABLE documents ADD COLUMN covers_period_source TEXT;  -- vocab
ALTER TABLE documents ADD COLUMN covers_period_note   TEXT;  -- who decided, and why

-- A human estimate must say who made it. A stated period needs no such defence.
ALTER TABLE documents ADD CONSTRAINT documents_estimate_needs_a_note
    CHECK (covers_period_source IS DISTINCT FROM 'human_estimate'
           OR covers_period_note IS NOT NULL);

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
    ('covers_period_source',
     'How a document''s coverage period was arrived at. A period a human inferred is '
     'evidence of a different kind from one the document printed.',
     FALSE, 'unknown')
ON CONFLICT DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('covers_period_source', 'stated',
     'The document prints the range. Carried verbatim, typos included.', 'migration-007'),
    ('covers_period_source', 'human_estimate',
     'A person inferred it from context. Requires covers_period_note.', 'migration-007'),
    ('covers_period_source', 'unknown',
     'Default. Nothing has established where the period came from.', 'migration-007')
ON CONFLICT DO NOTHING;

COMMIT;
