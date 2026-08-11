-- 003 — v_person_dossier: the compiled person record.
--
-- Every piece of this already existed. persons, person_aliases, person_voiceprints,
-- memberships, affiliations, and ELEVEN tables carrying a person_id in some role —
-- speaker, voter, mover, seconder, committer, coalition member, named-by. What did not
-- exist was anything that pulled them together, so "what do we know about this person"
-- was a query you had to write from scratch each time.
--
-- `page_type` already contains 'person', so a person page was always intended. This is
-- the query that feeds it.
--
-- WHY A VIEW AND NOT A TABLE. A dossier is derived — every fact in it lives somewhere
-- with its own provenance. Materialising it would create a second copy that can drift
-- from the claims that justify it, which is the failure mode `pages.is_dirty` and
-- `page_manifests` exist to prevent. If it gets slow, make it MATERIALIZED and refresh
-- on the same signal that dirties a page; do not hand-maintain it.

CREATE OR REPLACE VIEW v_person_dossier AS
SELECT
    p.id                        AS person_id,
    p.full_name,
    p.is_public_figure,
    p.legistar_person_id,

    -- Every spelling this person is known by. THE SPELLING AUTHORITY: ASR and LLM
    -- transcription both mangle proper nouns ('Malik' for Mallek, 'Kathari' for
    -- Kothari, 'Mazlumian' for Mazloomian), and this is what maps them back.
    (SELECT array_agg(a.alias ORDER BY a.alias)
       FROM person_aliases a WHERE a.person_id = p.id)            AS aliases,

    -- Voice identity. NULL for private individuals BY DESIGN — reject_private_voiceprint()
    -- refuses the insert, so absence here is a privacy guarantee, not missing data.
    (SELECT count(*) FROM person_voiceprints v WHERE v.person_id = p.id)
                                                                   AS voiceprint_count,
    (SELECT max(v.sample_count) FROM person_voiceprints v WHERE v.person_id = p.id)
                                                                   AS voiceprint_samples,
    (SELECT array_agg(DISTINCT m) FROM person_voiceprints v,
            unnest(v.enrolled_from_media) m WHERE v.person_id = p.id)
                                                                   AS voiceprint_sources,

    -- Where they sit, over time. Time-bounded: people change seats and a flat mapping
    -- misattributes votes.
    (SELECT array_agg(DISTINCT b.name) FROM memberships ms
       JOIN bodies b ON b.id = ms.body_id WHERE ms.person_id = p.id) AS bodies,
    (SELECT array_agg(DISTINCT o.name) FROM affiliations af
       JOIN orgs o ON o.id = af.org_id WHERE af.person_id = p.id)  AS orgs,

    -- Speaking record.
    (SELECT count(*) FROM utterances u WHERE u.person_id = p.id)    AS utterances,
    (SELECT count(DISTINCT u.media_asset_id) FROM utterances u
       WHERE u.person_id = p.id)                                    AS meetings_spoken_in,
    (SELECT round(sum(u.duration_ms)/60000.0, 1) FROM utterances u
       WHERE u.person_id = p.id)                                    AS minutes_spoken,
    -- How we came to believe this is them, strongest evidence first. A person known
    -- only by 'chair_address' is a weaker record than one with roll_call + human.
    (SELECT array_agg(DISTINCT u.speaker_id_method) FROM utterances u
       WHERE u.person_id = p.id AND u.speaker_id_method IS NOT NULL) AS id_methods,

    -- Legislative record. These arrive pre-identified from Legistar, so they are the
    -- cheapest high-confidence facts we hold about anyone.
    (SELECT count(*) FROM votes v WHERE v.person_id = p.id)         AS votes_cast,
    (SELECT count(*) FROM event_items ei
       WHERE ei.mover_person_id = p.id OR ei.seconder_person_id = p.id)
                                                                    AS motions_moved_or_seconded,

    -- Claim record — what they are on record as having said.
    (SELECT count(*) FROM claims c WHERE c.actor_id = p.id)         AS claims,
    (SELECT count(*) FROM commitments cm WHERE cm.committed_by_person_id = p.id)
                                                                    AS commitments_made,

    -- CROSS-SOURCE PRESENCE. The answer to "where else is this person named?" — the
    -- distinct source types they appear in. One appearance in one source is a mention;
    -- the same person across video, minutes, and a docket is a record.
    (SELECT array_agg(DISTINCT d.doc_type) FROM claims c
       JOIN documents d ON d.id = c.document_id WHERE c.actor_id = p.id)
                                                                    AS claim_source_types
FROM persons p;

COMMENT ON VIEW v_person_dossier IS
  'Compiled record for one person: identity and aliases, voiceprint enrollment, '
  'memberships and affiliations, speaking record with identification methods, '
  'legislative record from Legistar, and cross-source claim presence. Feeds '
  'page_type = ''person''. Derived — never write to it.';
