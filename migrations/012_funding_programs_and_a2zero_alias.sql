-- 012: the named program between a funder and what it funded; and A2ZER0 as A2ZERO.
--
-- ============================================================================
-- PART 1 -- THE MISSING MIDDLE
-- ============================================================================
-- Caleb: "how can we capture this relationship where the Organization (DOE) has a program
-- (EECBG) that's funded a subject (Bryant decarbonization)?"
--
-- Two of the three already have homes: fiscal_references.awarding_org_id is the body, and
-- fiscal_references.subject_id is what the money went to. The PROGRAM has none.
--
-- funding_instrument IS NOT ITS HOME, AND THAT MISTAKE HAS ALREADY BEEN MADE ONCE THIS
-- WEEK. funding_instrument is a category slot -- block grant, formula grant, revolving loan
-- -- exactly parallel to funding_source, which is a category of money. Putting "Energy
-- Efficiency and Conservation Block Grant" in it repeats the bug migration 011 fixed: the
-- store read funding_source, a KIND, as though it named the giver, and asked a human to go
-- find a funder the document had printed. A named thing stuffed into a category field is
-- unqueryable as either.
--
-- So three questions, three columns:
--     awarding_org_id     WHO           U.S. Department of Energy
--     program_id          UNDER WHAT    Energy Efficiency and Conservation Block Grant
--     funding_instrument  WHAT KIND     block_grant
--     subject_id          FOR WHAT      Bryant Neighborhood Decarbonization
--
-- THIS IS NOT ONLY ABOUT EECBG. Four references already in the store name a program and
-- resolve to the administering body, losing the program on the way: "SEMCOG Carbon
-- Reduction Program", "Urban Sustainability Directors Network Emergent Learning Fund",
-- "USDN Mini-Grant", "MI-HOPE". registries/ann_arbor/funder_aliases.json maps each to its
-- org, which answers "who paid" and silently discards "under which program" -- and the
-- program is what a researcher tracks across years and cities.
CREATE TABLE IF NOT EXISTS funding_programs (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    -- THE BODY THAT RUNS IT. Nullable because a document can name a program whose
    -- administering agency it never states -- which is a research question, not a reason
    -- to refuse the row. The whole point of 011 was to stop discarding what we do know.
    administering_org_id INT REFERENCES orgs(id),
    -- A program is often a vehicle of a larger one: EECBG money reaches a city through the
    -- state energy office. Self-referencing rather than a second table.
    parent_program_id   INT REFERENCES funding_programs(id),
    abbreviation        TEXT,
    description         TEXT,
    -- Human, always. A program is a referent; a cluster id never becomes a canonical key.
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now(),
    CHECK (parent_program_id IS NULL OR parent_program_id <> id)
);
CREATE INDEX IF NOT EXISTS idx_funding_programs_org ON funding_programs(administering_org_id);

CREATE TABLE IF NOT EXISTS funding_program_aliases (
    id                  SERIAL PRIMARY KEY,
    program_id          INT NOT NULL REFERENCES funding_programs(id),
    alias               TEXT NOT NULL,
    alias_type          TEXT,                   -- vocab: alias_type
    UNIQUE (program_id, alias)
);

ALTER TABLE fiscal_references
  ADD COLUMN IF NOT EXISTS program_id INT REFERENCES funding_programs(id);

COMMENT ON COLUMN fiscal_references.program_id IS
  'The named program the money came under -- EECBG, SEMCOG Carbon Reduction Program. NOT '
  'the same as awarding_org_id (the body) or funding_instrument (the category). A program '
  'with a NULL administering_org_id is a research question, not a bad row.';

-- funding_instrument was declared TEXT with no vocabulary and no terms, so nothing has
-- ever constrained or proposed a value for it. Declared and seeded now that the column
-- next to it means something different.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('funding_instrument', 'The financial mechanism of an award, not its source or program',
   TRUE, 'other')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, approved_by) VALUES
  ('funding_instrument','block_grant','schema-v0.2'),
  ('funding_instrument','competitive_grant','schema-v0.2'),
  ('funding_instrument','formula_grant','schema-v0.2'),
  ('funding_instrument','mini_grant','schema-v0.2'),
  ('funding_instrument','sponsorship','schema-v0.2'),
  ('funding_instrument','rebate','schema-v0.2'),
  ('funding_instrument','loan','schema-v0.2'),
  ('funding_instrument','revolving_loan','schema-v0.2'),
  ('funding_instrument','appropriation','schema-v0.2'),
  ('funding_instrument','millage_revenue','schema-v0.2'),
  ('funding_instrument','other','schema-v0.2')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- PART 2 -- A2ZER0
-- ============================================================================
-- The Year 5 report's title is "A2ZER0 Annual Report Year Five" -- U+0030 DIGIT ZERO,
-- drawn in CodecCold-ExtraBold at title size. Verified against the PDF's own text layer:
-- the pipeline reproduced it correctly and the SOURCE says it. Caleb: "I looked at the PDF
-- and to human eyes it should read as A2ZERO."
--
-- THE TEXT IS NOT EDITED. Caleb's standing rule -- "We should report dates as we recieve
-- them" -- is about faithfulness generally, and rewriting a published title inside the
-- canonical text would move every span after it and destroy the round-trip that makes a
-- claim citable. The document keeps saying A2ZER0. The ALIAS is what makes it findable.
INSERT INTO subjects (name, description, jurisdiction_id, created_by)
SELECT 'A2ZERO',
       'Ann Arbor''s community-wide carbon neutrality plan, adopted 2020. The subject the '
       'annual report corpus is about; individual initiatives are child subjects.',
       1, 'caleb (2026-08-21)'
WHERE NOT EXISTS (SELECT 1 FROM subjects WHERE name = 'A2ZERO');

INSERT INTO subject_aliases (subject_id, alias, alias_type)
SELECT s.id, v.alias, v.t
FROM subjects s, (VALUES
      ('A2ZER0',  'misspelling'),   -- Year 5 title: digit zero for the letter O
      ('A²ZERO',  'other'),         -- superscript-2 rendering; org 1 already carries it
      ('A2 ZERO', 'other'),         -- the split the Year 1 conversion kept hitting
      ('A2Zero',  'other')
   ) AS v(alias, t)
WHERE s.name = 'A2ZERO'
  AND NOT EXISTS (SELECT 1 FROM subject_aliases a
                  WHERE a.subject_id = s.id AND a.alias = v.alias);
