-- 024: one programme recorded twice, reconciled without losing either record.
--
-- WHY THIS IS NOT A DELETE. The wiki curated 229 initiatives by hand over several years, and
-- that produced the same programme under two names: `Electrify City Fleet` and `City Fleet
-- Electrification` and `City EV Fleet` are one thing, offered side by side as candidates for
-- the same sentence. The obvious fix -- repoint the rows, delete the loser -- does not hold.
-- pipeline/initiatives.py resolves a wiki page to a subject with
--     WHERE wiki_slug = %s OR lower(name) = lower(%s)
-- and ../a2zero-wiki is READ-ONLY and still contains all three pages. A deleted duplicate is
-- recreated by the next re-seed, every repointed row is orphaned, and nothing errors. The
-- tombstone is what makes the merge survive its own source.
--
-- IT ALSO KEEPS A TRUE FACT. The wiki really does hold two pages for one programme. Deleting
-- the row would assert that it does not, and would erase the only evidence of what the second
-- page was called -- which is exactly the string a future document is likely to print.
--
-- MERGING IS A HUMAN ACT, like every other identity decision here: `created_by` is human by
-- design and "a cluster ID never becomes a canonical key". pipeline/dedup_subjects.py may
-- only propose. `merged_by` is NOT NULL for the same reason `subjects.created_by` is.
--
-- NO CHAINS. If A merges into B and B later merges into C, a reader following one hop lands
-- on a tombstone. The trigger below refuses to point a merge at an already-merged subject, so
-- resolution is always exactly one hop and no query needs a recursive CTE.

ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS merged_into_id INT REFERENCES subjects(id),
    ADD COLUMN IF NOT EXISTS merged_by      TEXT,
    ADD COLUMN IF NOT EXISTS merged_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS merge_note     TEXT;

ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_merge_not_self;
ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_merge_not_self;
ALTER TABLE subjects ADD CONSTRAINT subjects_merge_not_self
    CHECK (merged_into_id IS NULL OR merged_into_id <> id);

-- A merge without an author is an anonymous identity claim, which is the thing this schema
-- refuses everywhere else.
ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_merge_attributed;
ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_merge_attributed;
ALTER TABLE subjects ADD CONSTRAINT subjects_merge_attributed
    CHECK (merged_into_id IS NULL OR (merged_by IS NOT NULL AND merged_at IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_subjects_merged_into ON subjects(merged_into_id)
    WHERE merged_into_id IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_merge_target() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.merged_into_id IS NULL THEN
        RETURN NEW;
    END IF;
    -- One hop, always. See the header note on chains.
    IF EXISTS (SELECT 1 FROM subjects s
                WHERE s.id = NEW.merged_into_id AND s.merged_into_id IS NOT NULL) THEN
        RAISE EXCEPTION
            'subject % is itself merged; point the merge at its survivor instead',
            NEW.merged_into_id;
    END IF;
    -- A survivor cannot become a tombstone while things point at it.
    IF EXISTS (SELECT 1 FROM subjects s WHERE s.merged_into_id = NEW.id) THEN
        RAISE EXCEPTION
            'subject % is the survivor of another merge and cannot itself be merged', NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_subject_merge_target
    BEFORE INSERT OR UPDATE OF merged_into_id ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_merge_target();

-- The list to resolve names against. Every consumer that offers subjects as candidates should
-- read this rather than `subjects`, or a tombstone reappears in a review queue.
CREATE OR REPLACE VIEW live_subjects AS
    SELECT * FROM subjects WHERE merged_into_id IS NULL;

COMMENT ON COLUMN subjects.merged_into_id IS
  'This subject was found to be the same programme as another. Rows have been repointed to '
  'the survivor; this one is kept as a tombstone so pipeline/initiatives.py still resolves '
  'the wiki page that created it, instead of recreating the duplicate on the next re-seed.';
