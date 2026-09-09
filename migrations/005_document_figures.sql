-- 005 — Figure extractions and harvested links.
--
-- ANSWERS A QUESTION THAT HAD NOT BEEN ANSWERED. The vision pass reads real numbers off
-- charts (31 cross-validated data points from the Year 5 dashboard alone), but nothing
-- said where they LAND. Saying only "model output stays out of the citation spine" left
-- the implication that the extraction was wasted. It is not — it lands here.
--
-- WHY FIGURE DATA IS NOT A `claims` ROW.
-- The load-bearing guarantee on `claims` is that `verbatim` can be located
-- character-for-character in the source conversion; a claim that fails that check is
-- discarded rather than stored. A chart value has no such source text. "2.33M" was never
-- in the PDF's text layer — it is a model's transcription of pixels. Writing it into
-- `claims` would mean either a NULL span (a claim exempt from the one check that makes
-- claims trustworthy) or a fabricated one. Both defeat the guard.
--
-- So chart readings are typed EVIDENCE, not claims. They reach the timeline the same way
-- any other source does — by attesting to an `asserted_event` — but with their own
-- attestation_type, so corroboration logic can tell "the report SAID this in prose" from
-- "a model READ this off a chart."
--
-- AND THAT DISTINCTION IS AN ASSET, NOT A CONCESSION. Year 5's GHG prose says emissions
-- must fall "from over 2.1 million metric tons"; the chart on the same page reads 2.33M
-- for 2015 and 1.97M for 2023. Those are independent extraction paths over the same
-- underlying fact — exactly the independence `event_attestations` exists to measure, and
-- exactly the input a numeric-drift check needs. Collapsing them into one claim would
-- destroy the disagreement that makes them useful.

BEGIN;

-- ── figures ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_figures (
    id                  SERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no             INT NOT NULL,
    -- Docling's own bbox, PDF points, BOTTOMLEFT origin. Kept so a crop is reproducible
    -- from the source rather than trusted from a PNG that may drift out of sync.
    bbox                JSONB NOT NULL,
    -- What made this image worth a paid vision call in the first place.
    classifier_label    TEXT NOT NULL,
    classifier_conf     NUMERIC(4,3),
    caption             TEXT,                   -- the document's own caption, when linked
    crop_path           TEXT,
    crop_dpi            INT,
    -- 600dpi cropping is what moved this model from "correctly declines to guess" to 31
    -- real data points, so the resolution is provenance, not a tuning detail.
    extracted_by        TEXT NOT NULL,          -- deployment id, e.g. 'gpt-5.6-sol'
    prompt_version      TEXT NOT NULL,          -- which prompt produced this reading
    raw_xml             TEXT NOT NULL,          -- the model's full response, unedited
    extracted_at        TIMESTAMPTZ NOT NULL,
    -- Ties the figure to the exact conversion whose page numbering it refers to.
    source_content_hash TEXT,
    CHECK (length(trim(raw_xml)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_figures_document ON document_figures(document_id);

-- ── one row per <point> ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS figure_data_points (
    id                  BIGSERIAL PRIMARY KEY,
    figure_id           INT NOT NULL REFERENCES document_figures(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,
    value_text          TEXT NOT NULL,          -- as printed/read: '2.33M', '711'
    value_numeric       NUMERIC,                -- parsed when unambiguous, else NULL
    unit                TEXT,
    -- MODEL SELF-REPORT, NEVER A PROPERTY OF THE DATUM. Flagged in review: a
    -- confidence="high" attribute sitting beside a value invites a reader (or a later
    -- extraction step) to treat it as something the chart asserted. The chart asserts a
    -- number; the model asserts how well it could read it. Separate column, separate
    -- meaning, and it can never travel inside a value.
    model_confidence    TEXT,                   -- vocab: model_confidence (high|medium|low)
    -- A CHART AXIS IS A TIME INTERVAL, AND A PARTIAL YEAR IS NOT A FULL ONE. Raised in
    -- review: Year 5 reports 20 solar installations for 2025 against 250 for 2024 —
    -- because the report was written mid-2025. Without an explicit interval that reads
    -- as a 92% collapse. Per CLAUDE.md, timeline queries compare intervals, never a
    -- start alone.
    period_start        DATE,
    period_end          DATE,
    period_is_partial   BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (length(trim(value_text)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_figpoints_figure ON figure_data_points(figure_id);

-- Chart readings attest to events like any other source; only the TYPE differs.
ALTER TABLE event_attestations
    ADD COLUMN IF NOT EXISTS figure_data_point_id BIGINT REFERENCES figure_data_points(id);

-- ── harvested links: the source-discovery queue ────────────────────────────────────────
-- The corpus names its own next sources. Year 5 carries 81 links across 23 hosts, 77 of
-- them anchored to the exact character span of the sentence citing them. Storing the
-- anchor and its sentence is what keeps this answerable a year later: "which claim relied
-- on this URL, in which reading of which document."
CREATE TABLE IF NOT EXISTS document_links (
    id                  BIGSERIAL PRIMARY KEY,
    document_id         INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    uri                 TEXT NOT NULL,
    anchor_text         TEXT NOT NULL,
    context_sentence    TEXT,
    page_no             INT,
    char_start          INT,
    char_end            INT,
    -- FALSE means the anchor could not be tied to a text span (4 of 81 on Year 5, all
    -- from link rects spanning a column gutter). Recorded rather than dropped, and never
    -- guessed: a wrong span would attribute a URL to a sentence that never cited it.
    located             BOOLEAN NOT NULL DEFAULT FALSE,
    source_content_hash TEXT,
    harvested_at        TIMESTAMPTZ NOT NULL,
    -- HARVESTED IS NOT VISITED. Whether this URL was ever retrieved, and what it said, is
    -- a separate decision with separate provenance. Dark matter is a lead queue, never a
    -- finding.
    fetched_at          TIMESTAMPTZ,
    CHECK (length(trim(uri)) > 0),
    CHECK (length(trim(anchor_text)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_doclinks_document ON document_links(document_id);
CREATE INDEX IF NOT EXISTS idx_doclinks_uri      ON document_links(uri);

COMMIT;
