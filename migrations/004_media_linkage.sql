-- 004 — media linkage: fix two defects, and remove the class they belong to.
--
-- Both were found while selecting a starting corpus, and both would have corrupted the
-- transcript layer at ingest — the layer every later claim rests on.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- DEFECT 1 — a video linked to the wrong meeting, by the STRONGER evidence chain.
--
-- IkZ4APPWNgY was attached to the 2026-05-12 Sustainability Commission meeting by
-- `legistar_calendar` — the city asserted the link by publishing it. Ground truth from
-- the host says otherwise:
--
--     IkZ4APPWNgY   7,426s   uploaded 2026-04-24   "…Sustainability Commission Meeting 4/14/26"
--
-- It is the April 14 meeting, and it was ALSO correctly attached to 2026-04-14 by
-- `host_channel` — the weaker chain, the one we inferred ourselves. Ingested naively,
-- every claim from that meeting is misdated by four weeks, and chronology is the join key
-- across every source in this store.
--
-- The real 2026-05-12 recording exists and was never ingested:
--
--     bc7m_xHhLSo   4,860s   uploaded 2026-05-13   "…Sustainability Commission Meeting - May 12, 2026"
--
-- ROOT CAUSE, and the reason this is not a one-off: the host_channel matcher recognises
-- titles in `M/D/YY` form. CTN publishes some meetings as `- Month D, YYYY` instead, and
-- those are invisible to it. The 2026-05-12 recording sat unlinked for that reason alone
-- while a wrong video occupied its row. Any future discovery pass must accept both forms.
-- (Checked: 2025-08-12 has genuinely no published recording — a real absence, not this bug.)
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- DEFECT 2 — four videos on one event, three of them seconds long.
--
-- 2026-01-13 carries four YouTube ids with IDENTICAL titles, all uploaded 2026-01-14:
--
--     2sc4VfsrS8g   6,398s   the meeting
--     BM-w9MgRNik      59s   aborted stream
--     bLpyad0up9o      42s   aborted stream
--     PHet5NGdPG0       1s   aborted stream
--
-- Not a multi-part recording — CTN restarted the stream three times. This is live and
-- dangerous: a `string_agg`-based pick of "the video for this event" selected the
-- 42-SECOND CLIP for the recommended starting set.
--
-- The stubs are NOT deleted. They exist, they are real uploads, and a row silently
-- removed is a fact nobody can re-check later. Instead durations are recorded and
-- `v_meeting_recordings` picks the longest recording per event, so every consumer gets
-- the meeting without needing to know this happened.
--
-- Deliberately NO minimum-duration threshold in that view: a threshold is a magic number
-- that fails the first time a commission adjourns in six minutes. Longest-per-event needs
-- no such constant and cannot be wrong for the reason a threshold would be.
--
-- Idempotent: keyed on external_id and event_date, never on serial ids. Safe to re-run.

BEGIN;

-- ── Defect 1 ────────────────────────────────────────────────────────────────────────
-- Re-point the 2026-05-12 row at the recording that IS the 2026-05-12 meeting.
-- discovered_via becomes host_channel: we found it by title on the host's channel, which
-- is the truthful provenance. The Legistar assertion was wrong and is not preserved as
-- though it were still evidence for this row.
UPDATE media_assets m
   SET external_id       = 'bc7m_xHhLSo',
       url               = 'https://www.youtube.com/watch?v=bc7m_xHhLSo',
       host_title        = 'Ann Arbor Sustainability Commission Meeting - May 12, 2026',
       duration_seconds  = 4860,
       host_upload_date  = DATE '2026-05-13',
       title_stated_date = DATE '2026-05-12',
       date_verification = 'matched',
       discovered_via    = 'host_channel'
  FROM events e, bodies b
 WHERE m.event_id = e.id AND e.body_id = b.id
   AND b.name ILIKE '%Sustainability%'
   AND e.event_date = DATE '2026-05-12'
   AND m.external_id = 'IkZ4APPWNgY';

-- The 2026-04-14 link was right all along. Record the evidence that confirms it.
UPDATE media_assets m
   SET duration_seconds  = 7426,
       host_upload_date  = DATE '2026-04-24',
       title_stated_date = DATE '2026-04-14',
       date_verification = 'matched'
  FROM events e
 WHERE m.event_id = e.id
   AND e.event_date = DATE '2026-04-14'
   AND m.external_id = 'IkZ4APPWNgY';

-- ── Defect 2 ────────────────────────────────────────────────────────────────────────
-- Durations for the 2026-01-13 uploads. The title dates are correct on all four, so
-- date_verification is 'matched' for each — these are not date defects, they are stubs,
-- and the honest discriminator is length.
UPDATE media_assets SET duration_seconds = v.dur,
                        host_upload_date = DATE '2026-01-14',
                        title_stated_date = DATE '2026-01-13',
                        date_verification = 'matched'
  FROM (VALUES ('2sc4VfsrS8g', 6398),
               ('BM-w9MgRNik',   59),
               ('bLpyad0up9o',   42),
               ('PHet5NGdPG0',    1)) AS v(ext, dur)
 WHERE media_assets.external_id = v.ext;

-- ── The structural fix ──────────────────────────────────────────────────────────────
-- One row per event: the longest recording. Everything downstream — transcription,
-- span selection, review-budget estimates — should read this, never media_assets
-- directly, so that a duplicate or a stub upload can never again be mistaken for a
-- meeting. `others` is exposed rather than hidden: a non-zero value is a linkage
-- question worth someone's attention, not noise to be suppressed.
CREATE OR REPLACE VIEW v_meeting_recordings AS
SELECT DISTINCT ON (m.event_id)
       m.event_id,
       e.event_date,
       b.name              AS body_name,
       m.id                AS media_asset_id,
       m.host,
       m.external_id,
       m.url,
       m.duration_seconds,
       m.host_title,
       m.host_upload_date,
       m.title_stated_date,
       m.date_verification,
       m.discovered_via,
       m.asr_model,
       m.asr_completed_at,
       count(*) OVER (PARTITION BY m.event_id) - 1 AS others
  FROM media_assets m
  JOIN events e ON e.id = m.event_id
  JOIN bodies b ON b.id = e.body_id
 ORDER BY m.event_id, m.duration_seconds DESC NULLS LAST, m.id;

COMMENT ON VIEW v_meeting_recordings IS
  'One recording per event: the longest. Guards against duplicate and aborted-stream '
  'uploads (2026-01-13 has four, three of them under a minute). Read this, not '
  'media_assets, when you need "the video for this meeting". `others` counts the '
  'additional assets on the same event.';

COMMIT;
