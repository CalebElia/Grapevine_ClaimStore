-- Meetings and video coverage, per governing body.
--
-- A good first query: it touches three tables at once and shows the two
-- different kinds of JOIN you will use constantly.
--
-- Run it in Postico with the SQL Query tab (Cmd-N in an open connection),
-- then press Cmd-R / the Execute button.

SELECT b.name       AS body,
       count(*)     AS meetings,
       count(m.id)  AS videos
FROM events e
JOIN bodies b
  ON b.id = e.body_id            -- INNER join: only events that have a body
LEFT JOIN media_assets m
  ON m.event_id = e.id           -- LEFT join: keep bodies with zero videos
GROUP BY b.name
ORDER BY meetings DESC;

-- Expected against the current seed data (1,981 rows):
--
--   body                       | meetings | videos
--   ---------------------------+----------+--------
--   City Council               |       46 |      0
--   Sustainability Commission  |       17 |     10
--
-- Two things worth noticing:
--
-- 1. 46 + 17 = 63, which is every row in `events`. Nothing was dropped.
--
-- 2. `count(*)` counts ROWS; `count(m.id)` counts NON-NULL values. That is
--    the whole reason City Council shows 46 meetings but 0 videos. With a
--    LEFT JOIN, a council meeting with no media_asset still produces a row,
--    but m.id is NULL in it. Had you written count(*) in both columns you
--    would have gotten 46 videos and believed it. This distinction is the
--    single most common source of wrong numbers in SQL reporting.
