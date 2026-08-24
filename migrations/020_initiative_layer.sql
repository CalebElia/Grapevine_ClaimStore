-- 020: the initiative layer -- kind, place, many-to-many strategy, and actors with roles.
--
-- WHAT THE SCHEMA ALREADY DECIDED, AND I NEARLY RE-DECIDED WRONGLY. I built the seven A2ZERO
-- strategies as SUBJECTS with parent_subject_id, which is a strict tree. The schema had
-- already rejected that in a comment on subject_framework_categories: "a Subject can sit in
-- A2Zero Strategy 2 AND the Comprehensive Plan's Land Use chapter AND the FY26 budget's
-- capital line simultaneously -- which a strict tree forbade." frameworks even names the
-- example: 'A2Zero CAP-2020 strategies', with code 'strategy-1'.
--
-- Caleb's framing is the same one: an initiative "pushes forward a Strategy or sometimes
-- two". A tree cannot hold that. A framework tag can.
--
-- So the division is:
--   SUBJECT             a thing in the world -- an initiative, a place, the plan itself
--   FRAMEWORK CATEGORY  one document's way of organising things -- the CAP's 7 strategies
--   subject_framework_categories   many-to-many, which is the two-strategy link
--
-- The seven strategy SUBJECTS created earlier stay for now: 903 claims point at them and
-- they are the working cross-year aggregate. They carry kind='strategy' and their alias
-- 'strategy-N' matches framework_categories.code, so the two layers join. Re-pointing claims
-- at initiatives is a later pass and needs alias matching that does not exist yet.

-- ---------------------------------------------------------------- what a subject IS
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS subject_kind TEXT;  -- vocab: subject_kind

COMMENT ON COLUMN subjects.subject_kind IS
  'What sort of thing this is. Without it, telling a strategy from an initiative from a '
  'neighbourhood needs a walk up parent_subject_id or a guess from wiki_slug.';

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('subject_kind', 'What sort of thing a subject is', TRUE, 'other')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('subject_kind','plan','a whole plan or programme, e.g. A2ZERO','schema-v0.3'),
  ('subject_kind','strategy','a top-level division of a plan','schema-v0.3'),
  ('subject_kind','initiative','a named project or programme that delivers work','schema-v0.3'),
  ('subject_kind','place','a geography: a neighbourhood, corridor, facility','schema-v0.3'),
  ('subject_kind','topic','a subject of discussion that is not a project','schema-v0.3'),
  ('subject_kind','other','none of the above','schema-v0.3')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_subject_kind BEFORE INSERT OR UPDATE ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('subject_kind','subject_kind');

-- ---------------------------------------------------------------- where it happens
-- WHY NOT parent_subject_id. Bryant is where the decarbonization project HAPPENS, not what
-- it is a kind of. Putting a place in the hierarchy would make "every initiative under
-- A2ZERO" and "every initiative in Bryant" the same query shape, and they are different
-- questions -- one taxonomic, one geographic. A project can also span two places.
CREATE TABLE IF NOT EXISTS subject_places (
    subject_id          INT NOT NULL REFERENCES subjects(id),
    place_subject_id    INT NOT NULL REFERENCES subjects(id),
    -- Which claim says so, when a document rather than a registry is the source.
    claim_id            BIGINT REFERENCES claims(id),
    assigned_by         TEXT NOT NULL,
    assigned_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (subject_id, place_subject_id),
    CHECK (subject_id <> place_subject_id)
);
CREATE INDEX IF NOT EXISTS idx_subject_places_place ON subject_places(place_subject_id);

-- ---------------------------------------------------------------- who does what
-- coalition_members already carries person/org/body, a role and a claim_id for provenance,
-- and coalitions already hangs off a subject. That is the actors-with-roles requirement
-- entire; what was missing is a controlled vocabulary for `role`, without which it becomes
-- the free-text field quantity_unit was.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('involvement_role', 'How an actor is involved in an initiative', TRUE, 'participant')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('involvement_role','lead','accountable for delivery','schema-v0.3'),
  ('involvement_role','co_lead','shares accountability','schema-v0.3'),
  ('involvement_role','implementer','does the work without owning it','schema-v0.3'),
  ('involvement_role','community_partner','a community organisation collaborating','schema-v0.3'),
  ('involvement_role','funder','supplies money','schema-v0.3'),
  ('involvement_role','regulator','holds approval or oversight authority','schema-v0.3'),
  ('involvement_role','beneficiary','the work is done for them','schema-v0.3'),
  ('involvement_role','participant','involved, role unstated','schema-v0.3')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_coalition_role BEFORE INSERT OR UPDATE ON coalition_members
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('involvement_role','role');

COMMENT ON COLUMN coalition_members.role IS
  'How this actor is involved -- lead, community_partner, funder. Controlled, because an '
  'uncontrolled role field cannot answer "who leads this" across a corpus.';
