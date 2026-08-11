-- ============================================================
-- A guided tour of the Grapevine claim store, in SQL.
-- ============================================================
--
-- HOW TO RUN THESE IN POSTICO
--   Put your cursor anywhere inside one statement and press Cmd-R.
--   Postico runs the statement under the cursor, not the whole file.
--   To run just part of one, select the text you want and press Cmd-R.
--
-- WHERE THE DATA ACTUALLY IS
--   Only 8 tables are populated right now:
--     persons 1771, vocabulary_terms 400, bodies 135, events 63,
--     vocabularies 57, media_assets 10, body_lineage 2, jurisdictions 1
--   The other 50 tables and all 11 views are empty. `claims` has not been
--   populated yet, and every view is built on top of claims. Empty results
--   from those are correct, not a mistake you made.
--
-- Every query below was run against the live database before being written
-- here, and the commented "expected" output is the real output.
-- ============================================================


-- ------------------------------------------------------------
-- 1. The simplest thing that works
-- ------------------------------------------------------------
-- SELECT chooses columns, FROM chooses the table, LIMIT caps the rows.
-- Always put a LIMIT on an exploratory query. It costs nothing and it
-- protects you from accidentally pulling a million rows.

SELECT * FROM bodies LIMIT 10;


-- ------------------------------------------------------------
-- 2. Ask a question instead of dumping a table
-- ------------------------------------------------------------
-- WHERE filters rows, ORDER BY sorts them.
-- Note `active` is a real boolean, so you write `WHERE active`, not
-- `WHERE active = 'true'`.

SELECT name, classification, created_date
FROM bodies
WHERE active
ORDER BY created_date DESC NULLS LAST
LIMIT 15;


-- ------------------------------------------------------------
-- 3. Counting, and the FILTER clause
-- ------------------------------------------------------------
-- GROUP BY collapses rows into buckets; count(*) counts each bucket.
-- count(*) FILTER (WHERE ...) counts only the rows matching a condition,
-- which lets you put a total and a subtotal side by side in one pass.
-- This is a Postgres feature and it is much nicer than the SUM(CASE WHEN...)
-- trick you will see in older SQL.

SELECT classification,
       count(*)                        AS bodies,
       count(*) FILTER (WHERE active)  AS still_active
FROM bodies
GROUP BY classification
ORDER BY bodies DESC;

--   classification | bodies | still_active
--   ---------------+--------+--------------
--   commission     |     76 |           26
--   committee      |     35 |           14
--   other          |     22 |           17
--   board          |      1 |            1
--   legislature    |      1 |            1
--
-- Ann Arbor has chartered 135 bodies over time and 59 are still active.
-- The city dissolves boards constantly. That churn is why body_lineage
-- exists (query 8).


-- ------------------------------------------------------------
-- 4. Working with dates
-- ------------------------------------------------------------
-- to_char() formats a date into a string you can group by.
-- 'YYYY-MM' sorts correctly as text, which 'MM/YYYY' would not.

SELECT to_char(event_date, 'YYYY-MM') AS month,
       count(*)                       AS meetings
FROM events
GROUP BY month
ORDER BY month;

-- 24 rows, Jan 2025 through Dec 2026, 1-4 meetings a month.
-- Note the future dates: these are scheduled meetings, not just past ones.


-- ------------------------------------------------------------
-- 5. JOIN: following a foreign key
-- ------------------------------------------------------------
-- events.body_id holds a number, not a name. To get the name you JOIN to
-- bodies and match that number against bodies.id. The `e` and `b` are
-- aliases so you do not retype the table name.

SELECT e.event_date, b.name AS body, e.location
FROM events e
JOIN bodies b ON b.id = e.body_id
ORDER BY e.event_date DESC
LIMIT 10;


-- ------------------------------------------------------------
-- 6. LEFT JOIN, and why count(*) lies
-- ------------------------------------------------------------
-- This is the single most useful query in the file, and the one most
-- likely to teach you something that saves you later.

SELECT b.name      AS body,
       count(*)    AS meetings,
       count(m.id) AS videos
FROM events e
JOIN bodies b       ON b.id = e.body_id
LEFT JOIN media_assets m ON m.event_id = e.id
GROUP BY b.name
ORDER BY meetings DESC;

--   body                      | meetings | videos
--   --------------------------+----------+--------
--   City Council              |       46 |      0
--   Sustainability Commission |       17 |     10
--
-- Two different joins are doing two different jobs:
--   JOIN (inner) drops events with no matching body.
--   LEFT JOIN keeps every event even when no media_asset matches.
--
-- And the two counts are NOT the same function:
--   count(*)    counts ROWS.
--   count(m.id) counts NON-NULL VALUES.
-- A council meeting with no video still produces a row, but m.id is NULL
-- in it. Write count(*) in both columns and you get "46 videos", which
-- looks completely plausible and is completely wrong.
--
-- Sanity check: 46 + 17 = 63 = every row in events. Nothing was dropped.


-- ------------------------------------------------------------
-- 7. NULL is not false, and it is not zero
-- ------------------------------------------------------------
-- Query 6 said City Council has 0 videos. Does that mean the meetings
-- were not recorded? Look before concluding.

SELECT video_available, count(*)
FROM events
GROUP BY video_available;

--   video_available | count
--   ----------------+-------
--   NULL            |    46
--   true            |    10
--   false           |     7
--
-- The 46 City Council meetings are not "no video". They are UNKNOWN --
-- nobody has checked yet. Only 7 events are confirmed to have no video.
--
-- This distinction matters enormously in a claim store. "We found no
-- evidence" and "we have not looked" are different facts, and a schema
-- that cannot tell them apart will launder one into the other.
--
-- Practical consequence: `WHERE video_available = false` returns 7 rows,
-- and `WHERE video_available <> true` also returns 7, NOT 53. NULL
-- comparisons yield NULL, which is not true, so those rows are filtered
-- out. To catch unknowns you must ask explicitly:

SELECT count(*) FROM events WHERE video_available IS NULL;   -- 46


-- ------------------------------------------------------------
-- 8. Joining a table to itself
-- ------------------------------------------------------------
-- body_lineage records that one body replaced another. Both the successor
-- and the predecessor are rows in `bodies`, so you join `bodies` TWICE
-- under two different aliases.

SELECT s.name  AS successor,
       l.relation,
       p.name  AS predecessor,
       l.effective_date
FROM body_lineage l
JOIN bodies s ON s.id = l.successor_body_id
JOIN bodies p ON p.id = l.predecessor_body_id;

--   successor                 | relation    | predecessor              | effective_date
--   --------------------------+-------------+--------------------------+----------------
--   Sustainability Commission | merged_into | Energy Commission        | 2025-07-01
--   Sustainability Commission | merged_into | Environmental Commission | 2025-07-01
--
-- Two commissions merged into one on 2025-07-01. Without this table, a
-- question like "what has Ann Arbor decided about energy?" would silently
-- lose everything the Energy Commission did before that date.


-- ------------------------------------------------------------
-- 9. Absence: NOT EXISTS
-- ------------------------------------------------------------
-- Finding what ISN'T there is often the interesting question.

SELECT count(*) AS bodies_with_no_meetings
FROM bodies b
WHERE NOT EXISTS (
    SELECT 1 FROM events e WHERE e.body_id = b.id
);

-- 133 of 135 bodies have zero meetings loaded. Only City Council and the
-- Sustainability Commission have been ingested so far. This one number
-- tells you the true state of the corpus better than any dashboard.


-- ------------------------------------------------------------
-- 10. Real data is dirty, and SQL will show you
-- ------------------------------------------------------------
-- btrim() strips leading and trailing whitespace. Comparing a column to
-- its own trimmed version finds rows with stray spaces.

SELECT count(*) FILTER (WHERE sort_name <> btrim(sort_name)) AS stray_whitespace,
       count(*) FILTER (WHERE full_name LIKE '%  %')         AS double_spaced
FROM persons;

-- 2 and 2. Small, but they surface immediately:

SELECT '[' || sort_name || ']' AS bracketed, full_name
FROM persons
ORDER BY sort_name
LIMIT 4;

--   bracketed  | full_name
--   -----------+-------------------------
--   [ Buendia] | Marie Gabrielle Buendia
--   [ Larcom]  | Kristen  Larcom
--   [A Welch]  | Cozine A Welch Jr.
--   [Abrons]   | Ellie Abrons
--
-- A leading space sorts BEFORE the letter A, so those two names jump to
-- the top of any alphabetical list of 1,771 people. Wrapping a value in
-- brackets to make whitespace visible is a debugging habit worth keeping.


-- ------------------------------------------------------------
-- 11. Searching text
-- ------------------------------------------------------------
-- ILIKE is case-insensitive LIKE. % matches any run of characters.

SELECT full_name, is_public_figure
FROM persons
WHERE full_name ILIKE '%taylor%'
ORDER BY sort_name;

-- Beware: a leading % ('%taylor%') cannot use a normal index, so on a big
-- table this scans everything. Fine at 1,771 rows, not fine at 10 million.


-- ------------------------------------------------------------
-- 12. The controlled vocabulary
-- ------------------------------------------------------------
-- 57 vocabularies govern 400 approved terms. This is how the schema stops
-- free text from sprawling into 40 spellings of "electric vehicle".

SELECT v.name, v.is_open, count(t.term) AS terms
FROM vocabularies v
LEFT JOIN vocabulary_terms t ON t.vocabulary = v.name
GROUP BY v.name, v.is_open
ORDER BY terms DESC
LIMIT 12;

-- doc_type leads with 20 terms. `is_open` = true means new terms may be
-- proposed (that is what the empty vocabulary_proposals table is for).
--
-- To read one vocabulary's actual terms, change the name here:

SELECT term, description
FROM vocabulary_terms
WHERE vocabulary = 'doc_type'
ORDER BY term;


-- ------------------------------------------------------------
-- 13. Look at the empty core
-- ------------------------------------------------------------
-- `claims` is what this whole repository exists to fill. It has 38 columns
-- and 0 rows. Reading its shape now tells you what the pipeline must
-- eventually produce.

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'claims'
ORDER BY ordinal_position;

-- information_schema is the database describing itself. Every Postgres
-- database has it, so this query works anywhere, forever.


-- ------------------------------------------------------------
-- 14. Which tables actually have anything in them
-- ------------------------------------------------------------
-- Orient yourself in any unfamiliar database with this one.

SELECT relname AS table_name, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY n_live_tup DESC;

-- n_live_tup is an ESTIMATE maintained by the statistics collector, not a
-- real count. It is instant on tables of any size, where count(*) has to
-- walk every row. Use it to orient, use count(*) when the number matters.


-- ============================================================
-- Nothing here is precious. To start over:
--     ./scripts/db.sh reset
-- Rebuilds the entire database in about a second.
-- ============================================================
