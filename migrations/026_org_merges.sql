-- 026: the same duplicate problem as subjects, one table over.
--
-- 142 orgs seeded from the wiki's actor files include `Ann Arbor SPARK` and `SPARK Ann
-- Arbor`, `U.S. Department of Energy` and `United States Department of Energy`. Same shape as
-- migrations/024, same resolution: repoint, alias, tombstone -- never delete, because
-- pipeline/resolve_orgs seeds from ../a2zero-wiki (READ-ONLY) and would recreate the row.
--
-- ORGS NEED AN ALIAS TABLE, WHICH SUBJECTS ALREADY HAD. resolve_orgs matches a claim's text
-- against org NAMES read from this table, so tombstoning `SPARK Ann Arbor` would delete a
-- name documents actually print. The alias table is where the merged name keeps working.
--
-- ONE TRIGGER FUNCTION FOR BOTH TABLES. enforce_merge_target was written against `subjects`
-- by name; it is rewritten here to read TG_TABLE_NAME, so subjects and orgs cannot drift into
-- two different definitions of what a legal merge is.

ALTER TABLE orgs
    ADD COLUMN IF NOT EXISTS merged_into_id INT REFERENCES orgs(id),
    ADD COLUMN IF NOT EXISTS merged_by      TEXT,
    ADD COLUMN IF NOT EXISTS merged_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS merge_note     TEXT;

ALTER TABLE orgs DROP CONSTRAINT IF EXISTS orgs_merge_not_self;
ALTER TABLE orgs DROP CONSTRAINT IF EXISTS orgs_merge_not_self;
ALTER TABLE orgs ADD CONSTRAINT orgs_merge_not_self
    CHECK (merged_into_id IS NULL OR merged_into_id <> id);

ALTER TABLE orgs DROP CONSTRAINT IF EXISTS orgs_merge_attributed;
ALTER TABLE orgs DROP CONSTRAINT IF EXISTS orgs_merge_attributed;
ALTER TABLE orgs ADD CONSTRAINT orgs_merge_attributed
    CHECK (merged_into_id IS NULL OR (merged_by IS NOT NULL AND merged_at IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_orgs_merged_into ON orgs(merged_into_id)
    WHERE merged_into_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS org_aliases (
    id          SERIAL PRIMARY KEY,
    org_id      INT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    alias       TEXT NOT NULL,
    alias_type  TEXT,                        -- vocab: alias_type
    UNIQUE (org_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_org_aliases_org ON org_aliases(org_id);

CREATE OR REPLACE TRIGGER trg_vocab_org_alias_type
    BEFORE INSERT OR UPDATE ON org_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type', 'alias_type');

-- Generic over the table the trigger fires on. See the header note.
CREATE OR REPLACE FUNCTION enforce_merge_target() RETURNS TRIGGER AS $$
DECLARE
    target_is_merged BOOLEAN;
    is_a_survivor    BOOLEAN;
BEGIN
    IF NEW.merged_into_id IS NULL THEN
        RETURN NEW;
    END IF;
    -- One hop, always: if A merges into B and B into C, a reader following one hop lands on
    -- a tombstone, and every consumer would need a recursive CTE.
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND merged_into_id IS NOT NULL)',
        TG_TABLE_NAME) INTO target_is_merged USING NEW.merged_into_id;
    IF target_is_merged THEN
        RAISE EXCEPTION '% % is itself merged; point the merge at its survivor instead',
            TG_TABLE_NAME, NEW.merged_into_id;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE merged_into_id = $1)',
        TG_TABLE_NAME) INTO is_a_survivor USING NEW.id;
    IF is_a_survivor THEN
        RAISE EXCEPTION '% % is the survivor of another merge and cannot itself be merged',
            TG_TABLE_NAME, NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_org_merge_target
    BEFORE INSERT OR UPDATE OF merged_into_id ON orgs
    FOR EACH ROW EXECUTE FUNCTION enforce_merge_target();

CREATE OR REPLACE VIEW live_orgs AS
    SELECT * FROM orgs WHERE merged_into_id IS NULL;

COMMENT ON TABLE org_aliases IS
  'Other names an organisation is published under, including the name of any org merged into '
  'it. resolve_orgs matches claim text against these as well as the canonical name, so a '
  'merge does not cost the store a name that documents actually print.';
