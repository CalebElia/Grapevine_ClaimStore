-- 021: coalition_members could never accept a row.
--
-- THE CONTRADICTION. Its primary key is (coalition_id, person_id, org_id, body_id), and a
-- primary key forces NOT NULL on every column in it. Its CHECK requires
-- num_nonnulls(person_id, org_id, body_id) = 1 -- exactly one actor, the other two NULL.
-- Both cannot hold. Every insert fails, whichever actor you name.
--
-- The table has 0 rows, which is why nobody found it: it is the first table in this schema
-- that nothing had yet tried to write. The CHECK is the correct intent -- a member is a
-- person OR an org OR a body, never two -- so the primary key is what changes.
--
-- A surrogate key, and uniqueness expressed where NULLs are allowed. NULLS NOT DISTINCT
-- keeps the original guarantee: the same org cannot join one coalition twice, and under the
-- default NULLS DISTINCT it could, because (1, NULL, 91, NULL) never equals itself.
ALTER TABLE coalition_members DROP CONSTRAINT IF EXISTS coalition_members_pkey;

ALTER TABLE coalition_members ALTER COLUMN person_id DROP NOT NULL;
ALTER TABLE coalition_members ALTER COLUMN org_id    DROP NOT NULL;
ALTER TABLE coalition_members ALTER COLUMN body_id   DROP NOT NULL;

ALTER TABLE coalition_members ADD COLUMN IF NOT EXISTS id BIGSERIAL PRIMARY KEY;

ALTER TABLE coalition_members DROP CONSTRAINT IF EXISTS coalition_members_unique_member;
ALTER TABLE coalition_members ADD CONSTRAINT coalition_members_unique_member
  UNIQUE NULLS NOT DISTINCT (coalition_id, person_id, org_id, body_id);
