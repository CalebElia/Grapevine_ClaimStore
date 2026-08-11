-- 002 — person_voiceprints: one row per (person, model), and enrollment provenance.
--
-- WHY. Enrollment is INCREMENTAL by design — `sample_count` exists so accuracy
-- compounds as more meetings are processed. Without a uniqueness constraint, "process
-- the same meeting twice" silently means "two centroids for one person", and every
-- later cosine lookup then has to decide which is real. This is the third instance of
-- the same defect in this repo (body_lineage -> 6 duplicate rows, media_assets -> 27),
-- both of which were ON CONFLICT clauses that matched no constraint and therefore did
-- nothing. Adding the constraint is what makes ON CONFLICT actually fire.
--
-- The model is part of the key because embeddings from different models are not
-- comparable and must not be averaged together. Re-enrolling under a new model adds a
-- row; it does not overwrite the old one.

ALTER TABLE person_voiceprints
    ADD COLUMN IF NOT EXISTS enrolled_from_media TEXT[],   -- which recordings contributed
    ADD COLUMN IF NOT EXISTS last_confirmed_by  TEXT;      -- the human who confirmed the name

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'person_voiceprints'::regclass
          AND conname  = 'person_voiceprints_person_model_key'
    ) THEN
        -- Fails loudly if duplicates already exist, which is the correct outcome:
        -- silently collapsing them would pick an arbitrary centroid.
        ALTER TABLE person_voiceprints
            ADD CONSTRAINT person_voiceprints_person_model_key
            UNIQUE (person_id, embedding_model);
        RAISE NOTICE '002: added UNIQUE (person_id, embedding_model)';
    ELSE
        RAISE NOTICE '002: constraint already present';
    END IF;
END $$;
