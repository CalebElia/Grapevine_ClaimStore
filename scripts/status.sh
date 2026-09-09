#!/usr/bin/env bash
# Where the store is: coverage, entity resolution, vocabulary drift, outstanding queues.
#
# WHY THIS EXISTS. "Where are we at?" was answered by a session of ad-hoc queries, which means
# the answer depended on which questions someone thought to ask. The gaps that matter are the
# ones nobody asks about -- 81 orgs typed 'other', 75 fiscal rows with award_status unknown --
# so they are asked here every time, whether or not anyone suspects them.
#
# READ-ONLY. Nothing here writes.
set -euo pipefail
cd "$(dirname "$0")/.."

exec ./scripts/db.sh psql <<'SQL'
\pset border 2
\pset null '·'

\echo ''
\echo '════ CORPUS ════'
SELECT doc_type, count(*) AS docs, sum(page_count) AS pages,
       count(*) FILTER (WHERE published_date IS NULL) AS no_pubdate,
       count(*) FILTER (WHERE covers_period_start IS NULL) AS no_period
  FROM documents GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '════ CLAIM COVERAGE ════'
SELECT d.doc_type, count(*) AS claims,
       count(*) FILTER (WHERE cl.subject_id IS NULL) AS no_subject,
       round(100.0*count(*) FILTER (WHERE cl.subject_id IS NULL)/count(*),1) AS pct_orphan,
       count(*) FILTER (WHERE cl.org_id IS NULL) AS no_org,
       count(*) FILTER (WHERE cl.actor_id IS NULL) AS no_actor
  FROM claims cl JOIN documents d ON d.id=cl.document_id GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '════ SECTIONS ════   (human_verdict is the first of the two signatures)'
SELECT parse_confidence, count(*),
       count(*) FILTER (WHERE human_verdict IS NOT NULL) AS human_signed,
       count(*) FILTER (WHERE subject_id IS NULL) AS no_subject
  FROM document_sections GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '════ ENTITIES ════'
SELECT 'subjects (initiative)' AS entity,
       count(*) FILTER (WHERE merged_into_id IS NULL) AS live,
       count(*) FILTER (WHERE merged_into_id IS NOT NULL) AS merged,
       (SELECT count(*) FROM subject_aliases) AS aliases
  FROM subjects WHERE subject_kind='initiative'
UNION ALL
SELECT 'orgs',
       count(*) FILTER (WHERE merged_into_id IS NULL),
       count(*) FILTER (WHERE merged_into_id IS NOT NULL),
       (SELECT count(*) FROM org_aliases)
  FROM orgs;

-- A tombstone still pointed at is a merge that missed a foreign key, and nothing errors.
SELECT 'subjects' AS entity,
       (SELECT count(*) FROM claims c JOIN subjects s ON s.id=c.subject_id
         WHERE s.merged_into_id IS NOT NULL) AS claims_on_a_tombstone
UNION ALL
SELECT 'orgs',
       (SELECT count(*) FROM claims c JOIN orgs o ON o.id=c.org_id
         WHERE o.merged_into_id IS NOT NULL);

SELECT org_type, count(*) FROM orgs GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '════ MENTIONS ════'
SELECT method, count(*), count(DISTINCT subject_id) AS subjects_named
  FROM claim_subject_mentions GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '════ MENTION INVARIANTS ════   (all must be zero)'
SELECT
  (SELECT count(*) FROM claim_subject_mentions m JOIN claims cl ON cl.id=m.claim_id
    WHERE substring(cl.verbatim from m.span_start+1 for m.span_end-m.span_start)
          <> m.matched_text) AS span_mismatch,
  (SELECT count(*) FROM claim_subject_mentions a JOIN claim_subject_mentions b
      ON b.claim_id=a.claim_id AND b.subject_id=a.subject_id
     AND b.span_start > a.span_start AND b.span_start < a.span_end) AS overlapping,
  (SELECT count(*) FROM claim_subject_mentions m JOIN subjects s ON s.id=m.subject_id
    WHERE s.merged_into_id IS NOT NULL) AS on_a_tombstone;

\echo ''
\echo '════ MONEY ════'
SELECT count(*) AS fiscal_rows,
       count(subject_id) AS has_subject,
       count(awarding_org_id) AS has_awarding_org,
       count(*) FILTER (WHERE award_status='unknown') AS status_unknown
  FROM fiscal_references;

\echo ''
\echo '════ VOCABULARY DRIFT ════   (pending = nobody has ruled)'
SELECT vocabulary, count(*) FILTER (WHERE status='pending') AS pending_terms,
       COALESCE(sum(occurrences) FILTER (WHERE status='pending'),0) AS occurrences,
       max(last_seen_at)::date AS last_seen
  FROM vocabulary_proposals GROUP BY 1
 HAVING count(*) FILTER (WHERE status='pending') > 0
 ORDER BY 3 DESC;
SQL
