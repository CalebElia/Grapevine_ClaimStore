-- 011: keep the funder's name as the document wrote it, whether or not it resolves.
--
-- WHAT WAS LOST. The extraction asked the model for `funder_name` -- "U.S Department of
-- Energy", "MI-HOPE", exactly as the text names them -- passed it to a registry lookup, and
-- kept only the resulting org id. When the registry had no such org the NAME WENT WITH IT.
-- The store then held a $500,000 award with no funder, from a sentence that names one.
--
-- WHY THAT IS WORSE THAN IT SOUNDS. research_questions asks "who funded this?" for money
-- with no source. Discarding an unresolved name turns a registry gap into a fabricated
-- research question: a human gets sent hunting for a fact printed in the document they
-- already have. Dark matter is supposed to be what the corpus cannot answer.
--
-- WHY A TEXT COLUMN AND NOT ONLY A FOREIGN KEY. They answer different questions.
-- awarding_org_id is the join -- "every award SEMCOG made" -- and must stay exact, because
-- a funder attributed to the wrong body says something false about who paid. This column
-- is EVIDENCE: what the document actually said, unresolved, still true if the registry is
-- wrong and still there if the registry later grows. A name here with a NULL id is a
-- registry gap, and now a findable one.
ALTER TABLE fiscal_references ADD COLUMN IF NOT EXISTS funder_name_text text;

COMMENT ON COLUMN fiscal_references.funder_name_text IS
  'The funder as the document names it, verbatim. Never normalised. Populated even when '
  'awarding_org_id could not be resolved -- a name here with a NULL id is a registry gap, '
  'not an unfunded award.';
