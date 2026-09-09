-- 022: give section_topic a vocabulary, and a first member that the CAP needs.
--
-- WHY NOW. document_sections.section_topic has existed since migration 006, carries the
-- comment "vocab: section_topic", and had NO vocabulary and ZERO populated rows. It was a
-- column waiting for a purpose, and the CAP supplies one.
--
-- THE PROBLEM IT SOLVES, MEASURED. Appendix 5, "List of Ideas Considered for A2ZERO", is 35
-- sections of ideas the City received and did NOT adopt as Actions -- among them "Geothermal
-- districts", which later became a central municipal venture. Extraction treated them
-- arbitrarily: 29 sections produced nothing at all, 7 produced 69 claims, from identical
-- content. Worse, the 69 came out `hypothetical`, which is the same modality an Action's
-- Vision block carries. So "It is 2030, and we have reached carbon neutrality" -- a
-- commitment the Plan makes -- and "Create carbon tax" -- an idea the Plan declined -- are
-- indistinguishable in the store.
--
-- MODALITY IS THE WRONG PLACE TO FIX IT. modality is assertion STRENGTH: asserted, hedged,
-- hypothetical, attributed_to_other. Whether an idea was adopted is not a property of how
-- strongly the City said it; adding a `considered` modality would conflate two axes and
-- corrupt every query that already groups by it. The frame belongs to the SECTION, which is
-- what section_topic is for.
--
-- The document says this about itself, and the claim is already stored on the appendix's
-- own intro section: "The Plan presented above includes the ideas evaluated to be the most
-- impactful ... The list of ideas below serves to document the full list of ideas received,
-- and to be turned to should adjustments to the Plan be required."

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('section_topic',
   'What a section IS, where that changes how its claims should be read. Not what the '
   'section is ABOUT -- that is subject_id. A section with no topic is the normal case.',
   TRUE, NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('section_topic', 'ideas_considered',
   'Options the author received and recorded but did not adopt. The claims inside are real '
   'assertions that these ideas were RAISED -- never that they were planned or funded. '
   'CAP-2020 Appendix 5 is the founding case: 35 sections, including the district geothermal '
   'idea that was not made an Action in 2020.',
   'schema-v0.4'),
  ('section_topic', 'timeline',
   'A dated sequence of steps. Its claims are commitments with a year attached, and the year '
   'is the claim''s world time rather than the document''s.',
   'schema-v0.4'),
  ('section_topic', 'assumptions',
   'What a projection depends on. Its claims are conditions, not outcomes -- which is why '
   'they extract as hypothetical and must not be read as commitments.',
   'schema-v0.4'),
  ('section_topic', 'roster',
   'A list of people, bodies or organisations. Names to resolve, not assertions about the '
   'world; the annual reports'' staff footers are the existing example.',
   'schema-v0.4'),
  ('section_topic', 'engagement_log',
   'A dated record of meetings, events or outreach. Each entry asserts that a thing happened '
   'on a date, which is exactly a claim, but none of them asserts a policy.',
   'schema-v0.4')
ON CONFLICT DO NOTHING;

-- NEVER REJECTS, like every other vocabulary here. fallback_term is NULL deliberately: an
-- unrecognised topic is stored as given and raises a proposal, because guessing a frame is
-- worse than recording an unknown one.
CREATE OR REPLACE TRIGGER trg_vocab_section_topic
    BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('section_topic', 'section_topic');

CREATE INDEX IF NOT EXISTS document_sections_topic_idx
    ON document_sections (section_topic) WHERE section_topic IS NOT NULL;
