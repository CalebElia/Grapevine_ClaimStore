-- 008 — a number in this store must have a source.
--
-- quantities.claim_id, fiscal_references.claim_id and barriers.claim_id are nullable
-- while claims.verbatim is NOT NULL and non-empty. That combination permits exactly the
-- thing this project exists to prevent: a quantity, a dollar figure or a stated barrier
-- sitting in the store with nothing to cite. The claim is what carries the verbatim, the
-- span and the content hash; a payload row without one is a number nobody said.
--
-- Found in Collin's schema review and deferred because nothing was writing these tables
-- yet. Annual reports are mostly quantities and commitments, so the text path is what
-- makes it urgent: the first extraction run would otherwise be free to write a fiscal
-- reference with a NULL claim_id and no error anywhere.
--
-- Safe to apply as a plain constraint: all three tables are empty. If they were not, this
-- would need the offending rows resolved first, which is the correct order -- backfilling
-- a claim_id by guessing which claim a number came from is how a citation becomes fiction.
--
-- NOT extended to commitments: that table carries its own utterance_id/document_id and
-- verbatim, so a commitment can stand on its own source without a claim. quantities,
-- fiscal_references and barriers cannot -- their verbatim describes the same assertion the
-- claim does.

BEGIN;

ALTER TABLE quantities        ALTER COLUMN claim_id SET NOT NULL;
ALTER TABLE fiscal_references ALTER COLUMN claim_id SET NOT NULL;
ALTER TABLE barriers          ALTER COLUMN claim_id SET NOT NULL;

COMMIT;
