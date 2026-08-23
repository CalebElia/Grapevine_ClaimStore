-- 018: the subject a section is about, so 903 claims stop being untopiced.
--
-- THE GAP. Every claim carried a date and none carried a subject. The store could say what
-- was asserted and when, and could not group it by WHAT IT IS ABOUT -- which is the join the
-- corpus exists for. "How has Ann Arbor's solar programme progressed across five years" had
-- no answer.
--
-- ON THE SECTION, NOT ON EACH CLAIM. There are 59 sections and 903 claims. Recording the
-- decision once per section makes it auditable and reversible in 59 places; writing it 903
-- times makes it 903 things to re-derive when the mapping changes. Claims inherit, and a
-- later pass may overrule any individual one.
--
-- WHY THIS IS NOT A MODEL'S JOB. All five reports organise themselves into the same seven
-- A2ZERO strategies and title them differently every year -- "Strategy 1: Power our
-- electrical grid with 100% renewable energy", "STRATEGY ONE: POWER OUR ELECTRICAL GRID...",
-- "STRATEGY 1: 100% RENEWABLES". A section's placement is the document SAYING what it is
-- about. That is evidence, so it is decided by string matching anchored on the word
-- "STRATEGY" -- never on a digit anywhere in the heading, because "YEAR 5 PRIORITIES"
-- contains a 5 and is not Strategy 5.
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS subject_id INT REFERENCES subjects(id);

COMMENT ON COLUMN document_sections.subject_id IS
  'What this section is about, from the report''s own structure. Claims inherit it. NULL on '
  'navigational sections -- a table of contents is not about anything, and giving it a '
  'subject would put structural furniture into topic aggregates.';

CREATE INDEX IF NOT EXISTS idx_sections_subject ON document_sections(subject_id);
