-- 001 — person_voiceprints.embedding: vector(192) -> vector(256)
--
-- WHY. The original 192 was the dimension of the wespeaker embedder bundled with
-- pyannote 3.1. We run pyannote 4.0.7 / speaker-diarization-community-1, whose
-- speaker_embeddings are 256-dimensional (measured, not assumed:
-- processing/lWvRVUMyLP4/diarization.json holds 21 clusters x 256 floats).
--
-- HOW IT WOULD HAVE FAILED. pgvector enforces declared dimension on insert, so this
-- is a hard ERROR rather than a truncation — which is the good case. It would have
-- surfaced the first time a voiceprint was enrolled, i.e. immediately after a human
-- finished a two-hour transcript review. Fixed before that happens.
--
-- SAFE TO RUN. person_voiceprints is empty; the guard below makes that explicit
-- rather than assumed, because ALTER ... TYPE on a populated vector column with a
-- different dimension silently fails to cast and would abort mid-transaction.

DO $$
DECLARE
    n BIGINT;
    cur TEXT;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO cur
    FROM pg_attribute a
    WHERE a.attrelid = 'person_voiceprints'::regclass
      AND a.attname = 'embedding' AND NOT a.attisdropped;

    IF cur IS NULL THEN
        RAISE NOTICE '001: person_voiceprints.embedding not found — nothing to do';
        RETURN;
    END IF;

    IF cur = 'vector(256)' THEN
        RAISE NOTICE '001: already vector(256) — no change';
        RETURN;
    END IF;

    SELECT count(*) INTO n FROM person_voiceprints WHERE embedding IS NOT NULL;
    IF n > 0 THEN
        RAISE EXCEPTION
          '001: refusing to change dimension with % existing embedding(s). Those were '
          'produced by a 192-dim model and cannot be reinterpreted as 256-dim — they '
          'must be re-enrolled from diarization output, not cast.', n;
    END IF;

    EXECUTE 'ALTER TABLE person_voiceprints ALTER COLUMN embedding TYPE vector(256)';
    RAISE NOTICE '001: person_voiceprints.embedding % -> vector(256)', cur;
END $$;
