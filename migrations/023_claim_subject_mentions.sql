-- 023: what a claim NAMES, as distinct from what it is ABOUT.
--
-- WHY A NEW TABLE AND NOT claims.subject_id. That column is single-valued and already used:
-- an annual-report claim carries the strategy its section belongs to. Overwriting it with an
-- initiative would trade one true fact for another, and a claim can name several initiatives
-- in one sentence -- "supporting the installation of solar, geothermal, electric appliances,
-- and energy efficiency improvements at multiple sites" names four.
--
-- MENTION IS WEAKER THAN SUBJECT, DELIBERATELY. A section heading that says "Implement
-- Community Choice Aggregation" is the document declaring what the section is about. A
-- sentence that happens to contain the words "community choice aggregation" is the document
-- REFERRING to it, which is a different and lesser fact. Storing both in one column would
-- make the two indistinguishable, and the weaker one would silently inherit the stronger
-- one's authority in every aggregate.
--
-- THE SPAN IS THE EVIDENCE, which is the same discipline claims themselves are held to. A
-- row here is provable by slicing the claim's own verbatim: if the offsets do not yield the
-- matched text, the row is wrong and a test can say so. Nothing here rests on a similarity
-- score, per the standing rule that embeddings may propose and only symbols decide.
--
-- MEASURED BEFORE IT WAS WRITTEN. Across 903 annual-report claims against 229 curated
-- initiatives: 139 claims contain an initiative's full name of two words or more, and a
-- twelve-row sample of those was entirely correct ("Green Rental Housing program", "landfill
-- solar project", "Community Climate Action Millage"). Loosening to "the name's identity
-- words appear within sixty characters" adds 110 more and visibly costs precision -- it
-- pairs "Support Aging in Place Efficiently" with a sentence about feedback sessions -- so
-- proximity matches are NOT stored here. They go to a queue for a person or the semantic
-- pass, because a mention nobody can verify is worse than a mention nobody made.

CREATE TABLE IF NOT EXISTS claim_subject_mentions (
    id                  BIGSERIAL PRIMARY KEY,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    subject_id          INT NOT NULL REFERENCES subjects(id),

    -- Offsets into claims.verbatim, not into the canonical text: a mention is a property of
    -- the quoted sentence, and stays valid however the document is later re-rendered.
    span_start          INT NOT NULL,
    span_end            INT NOT NULL,
    -- What actually appeared, in the document's own casing. "community choice aggregation"
    -- and "Community Choice Aggregation" are the same subject and different text, and which
    -- one the page printed is worth keeping.
    matched_text        TEXT NOT NULL,

    method              TEXT NOT NULL,          -- vocab: mention_method
    detected_by         TEXT NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A human ruling on a machine's proposal. NULL means nobody has looked, which is not the
    -- same as nobody agreeing.
    confirmed_by        TEXT,
    confirmed_at        TIMESTAMPTZ,

    -- One row per (claim, subject, position). A sentence naming an initiative twice records
    -- both, because the second occurrence is also evidence.
    UNIQUE (claim_id, subject_id, span_start),
    CHECK (span_end > span_start),
    CHECK (confirmed_by IS NULL OR confirmed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS claim_mentions_claim_idx   ON claim_subject_mentions (claim_id);
CREATE INDEX IF NOT EXISTS claim_mentions_subject_idx ON claim_subject_mentions (subject_id);

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('mention_method',
   'How a mention was found. The weakest acceptable evidence for a stored row is a literal '
   'occurrence of a name; anything softer is queued rather than written.',
   TRUE, NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('mention_method', 'literal_name',
   'The subject''s name occurs in the verbatim, case-insensitively, as a phrase of two or '
   'more words. One word is never enough: the initiative named "Offsets" would otherwise '
   'match any sentence using the word.', 'schema-v0.4'),
  ('mention_method', 'literal_alias',
   'An approved alias from subject_aliases occurs in the verbatim. Same rule as a name, and '
   'the alias that matched is kept in matched_text.', 'schema-v0.4'),
  ('mention_method', 'human',
   'A person asserted this mention. Needs no span rule; it needs a name in detected_by.',
   'schema-v0.4')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_mention_method
    BEFORE INSERT OR UPDATE ON claim_subject_mentions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('mention_method', 'method');

COMMENT ON TABLE claim_subject_mentions IS
  'A claim NAMES a subject. Weaker than claims.subject_id, which is what the claim is about, '
  'and many-to-many because one sentence can name several. Every row is provable by slicing '
  'claims.verbatim at span_start..span_end.';
