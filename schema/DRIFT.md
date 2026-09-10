> **RESOLVED 2026-09-09.** Everything below is the state that prompted the fix, kept as the
> record of what was wrong. All 17 unfolded migrations are now folded into
> `schema/claim_store.sql` and `schema/vocabularies.sql`, and a database rebuilt from those
> two files alone is identical to the live one across tables, columns, views, triggers,
> vocabularies and terms.
>
> `schema/live-snapshot.sql` is **not committed** — it is a generated dump, and a stale
> generated file is the very thing this report is about. The command that produces it is
> in `.gitignore` and below.
>
> `tests/test_schema_drift.py` now builds one database from the canonical files and another
> from canonical-plus-migrations and fails on any difference, so this cannot recur silently.
>
> Fixing it surfaced four things nothing was watching, each written up in a migration:
>
> | | |
> |---|---|
> | `figure_data_points.model_confidence` | annotated `-- vocab:` since migration 005, no vocabulary ever created — 45 rows unguarded (migration 027) |
> | `document_sections.parse_confidence` | no enforcement trigger, ever. The column the whole fail-closed rule rests on (migration 028) |
> | `funding_program_aliases.alias_type`, `documents.covers_period_source` | same: annotated, never enforced (migration 028) |
> | `mention_method.core_phrase_verified` | live with no file anywhere claiming it, while `detect_mentions.py` writes it on 46 rows (migration 029) |

---

# Schema drift: LIVE database vs canonical `schema/*.sql`

Generated 2026-09-09 against the live local Postgres (18.4, conda env `grapevine-db`,
socket `/tmp`, port 5433, db `grapevine`).

**Verdict: drift is present and material.** A database built from `schema/claim_store.sql` +
`schema/vocabularies.sql` alone — which is exactly what `tests/test_schema_runtime.py` builds —
is missing **9 tables, 14 columns on tables that do exist, 2 views, 5 triggers, 7 vocabularies
and 47 vocabulary terms** that the live database has. Every missing object traces to a
migration in `migrations/` that was never folded back into the canonical files.

Nothing exists in the canonical files that is absent from live. Drift is one-directional:
live is a strict superset.

---

## Method

The snapshot is a **real `pg_dump`**, not a reconstruction:

```bash
cd /Users/calebjohnson/Developer/Grapevine/Coding_Projects/grapevine-claim-store
/opt/miniconda3/envs/grapevine-db/bin/pg_dump \
    -h /tmp -p 5433 -U grapevine -d grapevine \
    --schema-only --no-owner --no-privileges \
    -f schema/live-snapshot.sql
```

`pg_dump` 18.4 matches the server's 18.4, so there was no version refusal. Result:
6447 lines, 0 `INSERT` statements (schema-only confirmed).

The live inventory was read from `information_schema` / `pg_catalog` through
`./scripts/db.sh psql`. The canonical side was parsed textually from the two SQL files.
Textual parsing is sound here because the canonical files contain **no** `ALTER TABLE ...
ADD COLUMN`: the only `ALTER TABLE` in either file is
`ALTER TABLE body_lineage ADD CONSTRAINT fk_bl_matter ...` (claim_store.sql:374). Every
column therefore appears inside its own `CREATE TABLE` block.

---

## Live counts

| Object | Live | Canonical files |
|---|---|---|
| Base tables | 69 | 60 |
| Columns (base tables) | 670 | 566 |
| Views | 17 | 15 |
| Materialized views | 0 | 0 |
| Triggers (non-internal) | 79 | 74 |
| Functions, user-defined | 4 | 4 |
| Functions, total in `public` | 132 | — (128 are pgvector / uuid-ossp) |
| Indexes | 143 | 41 explicit `CREATE INDEX` + implicit |
| Enum types | 0 | 0 |
| `vocabularies` rows | 69 | 62 |
| `vocabulary_terms` rows | 480 | 433 |

---

## 1. Tables live but absent from the canonical files

All 9 are entirely absent — not one is even mentioned by name in `claim_store.sql`.

| Table | Cols | Added by |
|---|---|---|
| `document_figures` | 14 | `005_document_figures.sql` |
| `figure_data_points` | 10 | `005_document_figures.sql` |
| `document_links` | 12 | `005_document_figures.sql` |
| `document_sections` | 23 | `006_document_sections.sql` (+014, 018, 022) |
| `footnotes` | 9 | `015_footnotes.sql` (+016) |
| `footnote_references` | 5 | `015_footnotes.sql` |
| `funding_programs` | 8 | `012_funding_programs_and_a2zero_alias.sql` |
| `funding_program_aliases` | 4 | `012_funding_programs_and_a2zero_alias.sql` |
| `subject_places` | 5 | `020_initiative_layer.sql` |

Live column lists:

- `document_figures`: id, document_id, page_no, bbox, classifier_label, classifier_conf, caption, crop_path, crop_dpi, extracted_by, prompt_version, raw_xml, extracted_at, source_content_hash
- `figure_data_points`: id, figure_id, label, value_text, value_numeric, unit, model_confidence, period_start, period_end, period_is_partial
- `document_links`: id, document_id, uri, anchor_text, context_sentence, page_no, char_start, char_end, located, source_content_hash, harvested_at, fetched_at
- `document_sections`: id, document_id, sequence, heading, section_topic, char_start, char_end, page_start, page_end, extraction_tier, tier_assigned_by, summary, content_hash, parse_confidence, parse_flags, parse_reviewed_by, parse_reviewed_at, human_verdict, human_verdict_by, human_verdict_at, human_verdict_note, human_verdict_hash, subject_id
- `footnotes`: id, document_id, number, body_text, page_no, char_start, char_end, document_section_id, contact_person_id
- `footnote_references`: id, footnote_id, document_section_id, char_at, marker_evidence
- `funding_programs`: id, name, administering_org_id, parent_program_id, abbreviation, description, created_by, created_at
- `funding_program_aliases`: id, program_id, alias, alias_type
- `subject_places`: subject_id, place_subject_id, claim_id, assigned_by, assigned_at

**Tables in the canonical files but absent live: none.**

---

## 2. Columns live but absent from the canonical files

These are the dangerous ones — the table exists in `claim_store.sql`, so a schema built from
it looks superficially correct and then fails only when the pipeline touches the column.

| Column | Type | Null | Added by |
|---|---|---|---|
| `claims.document_section_id` | integer | YES | `006_document_sections.sql` |
| `coalition_members.id` | bigint | NO, `nextval('coalition_members_id_seq')` | `021_coalition_members_unusable.sql` |
| `documents.converter` | text | YES | `006_document_sections.sql` |
| `documents.converter_version` | text | YES | `006_document_sections.sql` |
| `documents.parse_verdict` | text | YES | `006_document_sections.sql` |
| `documents.parse_override_reason` | text | YES | `006_document_sections.sql` |
| `documents.covers_period_source` | text | YES | `007_period_provenance.sql` |
| `documents.covers_period_note` | text | YES | `007_period_provenance.sql` |
| `documents.snapshot_source` | text | YES | `013_snapshot_provenance.sql` |
| `documents.snapshot_note` | text | YES | `013_snapshot_provenance.sql` |
| `event_attestations.figure_data_point_id` | bigint | YES | `005_document_figures.sql` |
| `fiscal_references.funder_name_text` | text | YES | `011_funder_name_text.sql` |
| `fiscal_references.program_id` | integer | YES | `012_funding_programs_and_a2zero_alias.sql` |
| `fiscal_references.direction` | text | YES | `017_fiscal_direction.sql` |

`coalition_members` is the structurally worst of these: live has a surrogate `id` plus a
`coalition_members_unique_member` index; canonical still declares
`PRIMARY KEY (coalition_id, person_id, org_id, body_id)`, the composite PK that
migration 021 exists to repair (NULLs in a PK). The canonical file still carries the broken form.

**Columns in the canonical files but absent live: none.**

---

## 3. Views

Live 17, canonical 15. Every canonical view exists live. Live-only:

| View | Depends on | Added by |
|---|---|---|
| `v_meeting_recordings` | media linkage | `004_media_linkage.sql` |
| `v_money_by_direction` | `fiscal_references.direction` | `017_fiscal_direction.sql` |

No materialized views on either side.

---

## 4. Triggers

Live 79, canonical 74. Every canonical trigger exists live. Live-only — all 5 are vocabulary
enforcement on objects that are themselves drift:

| Trigger | On |
|---|---|
| `trg_vocab_sections_human` | `document_sections` (table missing from canonical) |
| `trg_vocab_section_topic` | `document_sections` (table missing from canonical) |
| `trg_vocab_footnote_ref` | `footnote_references` (table missing from canonical) |
| `trg_vocab_documents_snapshot` | `documents.snapshot_source` (column missing from canonical) |
| `trg_vocab_fiscal_direction` | `fiscal_references.direction` (column missing from canonical) |

---

## 5. Functions

**No drift.** Both sides define exactly the same 4 user-defined functions:
`enforce_merge_target`, `enforce_vocabulary`, `reject_private_voiceprint`,
`require_curated_claim`.

The other 128 functions in the live `public` schema belong to the `vector` (pgvector) and
`uuid-ossp` extensions, both of which the canonical file installs via
`CREATE EXTENSION IF NOT EXISTS`.

---

## 6. Vocabularies and vocabulary terms

Seeded live: **69 vocabularies / 480 terms**. Seeded by `vocabularies.sql`:
**62 vocabularies / 433 terms**. Every canonical vocabulary and term is present live.

### Vocabularies live but not seeded by `vocabularies.sql` (7)

`covers_period_source`, `fiscal_direction`, `human_verdict`, `marker_evidence`,
`parse_confidence`, `provenance_source`, `section_topic`

### Terms live but not seeded by `vocabularies.sql` (47)

Belonging to the 7 unseeded vocabularies above:

- `covers_period_source`: human_estimate, stated, unknown
- `fiscal_direction`: authorized, disbursed, received, saved, spent, unknown
- `human_verdict`: approved, approved_with_caveats, not_reviewed, rejected
- `marker_evidence`: human, printed, second_read, text_layer, unknown
- `parse_confidence`: clean, known_incomplete, suspect, unaudited
- `provenance_source`: human_supplied, local_file, retrieved, unknown
- `section_topic`: assumptions, engagement_log, ideas_considered, roster, timeline

Added to vocabularies that **are** seeded canonically, but whose term set has since grown:

- `date_precision`: reporting_period
- `quantity_measure`: area_protected, facilities_treated, households_served, units_deployed
- `quantity_unit`: days, kWh, metric_tons, metric_tons_co2e, miles, other, percent, ratio, square_feet, years
- `mention_method`: core_phrase_verified — **see below, this one is different**

That group is the quietest failure mode: the vocabulary row exists in the canonical file,
so nothing looks missing, but a test database rejects — or logs a proposal for — a term the
live database accepts.

### `mention_method.core_phrase_verified` — live-only with no file provenance

This term is in the live database with `approved_by = 'schema-v0.4'`, but it does not exist
in `vocabularies.sql` **and it does not exist in any file under `migrations/`**:

```bash
grep -c core_phrase_verified schema/vocabularies.sql        # 0
grep -l core_phrase_verified migrations/*.sql               # no matches
grep -rn vocabulary_terms --include=*.py pipeline/ scripts/ processing/   # no matches
./scripts/db.sh psql -At -c \
  "SELECT count(*) FROM vocabulary_proposals WHERE proposed_term='core_phrase_verified'"  # 0
```

Migration `023_claim_subject_mentions.sql` created the `mention_method` vocabulary and seeded
exactly three terms (`literal_name`, `literal_alias`, `human`), all three of which *were*
correctly folded into `vocabularies.sql` (lines 230–231). `core_phrase_verified` was not one
of them. No pipeline code inserts into `vocabulary_terms`, and it never appeared as a
`vocabulary_proposals` row, so it did not arrive through the open-vocabulary proposal path
either. It was inserted into the live database by hand and has never been written down
anywhere in the repo.

`pipeline/detect_mentions.py:382` writes `"core_phrase_verified"` as the `method` value on
every mention it detects at that tier. Because `mention_method` is an open vocabulary the
`enforce_vocabulary` trigger will not reject it — it will log a proposal — so this does not
break a freshly built test database loudly. It silently reclassifies every
`core_phrase_verified` mention as an unrecognised term.

Provenance of all 47 live-only terms, by `approved_by`:

| `approved_by` | Count | Traceable to |
|---|---|---|
| `migration-006` | 4 | `006_document_sections.sql` (`parse_confidence`) |
| `migration-007` | 3 | `007_period_provenance.sql` (`covers_period_source`) |
| `migration-009` | 5 | `009_measure_vocab_and_dates.sql` (`date_precision`, `quantity_measure`) |
| `schema-v0.2` | 19 | `014`, `015`, `017` (`human_verdict`, `marker_evidence`, `fiscal_direction`, `provenance_source` — the last via `013`) |
| `schema-v0.3` | 10 | `019_quantity_unit_vocabulary.sql` (`quantity_unit`) |
| `schema-v0.4` | 6 | `022_section_topic.sql` (5 × `section_topic`) + `core_phrase_verified` (**no source file**) |

---

## 7. Indexes

Every one of the 41 explicit `CREATE INDEX` names in the canonical files exists live.
13 explicitly-named live indexes are absent from the canonical files; all are on drifted
tables/columns:

`claims_document_section_idx`, `coalition_members_unique_member`,
`document_sections_document_idx`, `document_sections_tier_idx`,
`document_sections_topic_idx`, `idx_doclinks_document`, `idx_doclinks_uri`,
`idx_figpoints_figure`, `idx_figures_document`, `idx_footnotes_document`,
`idx_funding_programs_org`, `idx_sections_subject`, `idx_subject_places_place`

---

## 8. Migrations 024, 025, 026 — verified folded in

Checked explicitly rather than assumed. All three are clean.

- **024 (subject merges)** — `subjects.merged_into_id / merged_by / merged_at / merge_note`
  all present in `claim_store.sql` (lines 595–603), along with `enforce_merge_target()`
  (615), `trg_subject_merge_target` (641), `idx_subjects_merged_into` (608) and the
  `live_subjects` view (646). No drift.
- **025 (`org_type` = `faith_group`)** — present live in `vocabulary_terms`, and present in
  `vocabularies.sql`. No drift.
- **026 (org merges)** — `orgs.merged_into_id / merged_by / merged_at / merge_note`
  present in `claim_store.sql` (327–334), plus `idx_orgs_merged_into` (649),
  `trg_org_merge_target` (653) and the `live_orgs` view (656). No drift.

For reference, the previously-known case `subjects.subject_kind` (migration 020) is also
confirmed folded in — `claim_store.sql:582`.

---

## Migrations whose effects have NOT been folded back

Ordered. Each of these still contributes live objects that the canonical files do not create:

| Migration | Unfolded contribution |
|---|---|
| `004_media_linkage.sql` | view `v_meeting_recordings` |
| `005_document_figures.sql` | tables `document_figures`, `figure_data_points`, `document_links`; column `event_attestations.figure_data_point_id` |
| `006_document_sections.sql` | table `document_sections`; columns `claims.document_section_id`, `documents.converter`, `.converter_version`, `.parse_verdict`, `.parse_override_reason`; vocab `parse_confidence` |
| `007_period_provenance.sql` | columns `documents.covers_period_source`, `.covers_period_note`; vocab `covers_period_source` |
| `009_measure_vocab_and_dates.sql` | terms in `quantity_measure`, `date_precision` |
| `011_funder_name_text.sql` | column `fiscal_references.funder_name_text` |
| `012_funding_programs_and_a2zero_alias.sql` | tables `funding_programs`, `funding_program_aliases`; column `fiscal_references.program_id` |
| `013_snapshot_provenance.sql` | columns `documents.snapshot_source`, `.snapshot_note`; vocab `provenance_source`; trigger `trg_vocab_documents_snapshot` |
| `014_human_verdict.sql` | vocab `human_verdict`; `document_sections.human_verdict*` columns |
| `015_footnotes.sql` | tables `footnotes`, `footnote_references`; vocab `marker_evidence` |
| `016_footnote_contact.sql` | column `footnotes.contact_person_id` |
| `017_fiscal_direction.sql` | column `fiscal_references.direction`; vocab `fiscal_direction`; view `v_money_by_direction`; trigger `trg_vocab_fiscal_direction` |
| `018_section_subject.sql` | column `document_sections.subject_id` |
| `019_quantity_unit_vocabulary.sql` | 10 terms in `quantity_unit` |
| `020_initiative_layer.sql` | table `subject_places` (note: `subjects.subject_kind` from this migration *is* folded in) |
| `021_coalition_members_unusable.sql` | column `coalition_members.id`; unique index; canonical still has the broken composite PK |
| `022_section_topic.sql` | column `document_sections.section_topic`; vocab `section_topic` + its 5 terms |

Migrations 001, 002, 003, 008, 010, 023, 024, 025, 026 show no unfolded residue.
Migration 023's own objects (`claim_subject_mentions`, the `mention_method` vocabulary and its
three seeded terms, `trg_vocab_mention_method`) are all present in the canonical files; the
extra live term `core_phrase_verified` does not come from 023 — see §6.

Vocabulary attribution above was verified by name, e.g.:

```bash
for v in covers_period_source fiscal_direction human_verdict marker_evidence \
         parse_confidence provenance_source section_topic; do
  echo -n "$v -> "; grep -lE "'$v'" migrations/*.sql | tr '\n' ' '; echo
done
```

---

## Commands used

```bash
REPO=/Users/calebjohnson/Developer/Grapevine/Coding_Projects/grapevine-claim-store
cd "$REPO"

# 1. the snapshot
/opt/miniconda3/envs/grapevine-db/bin/pg_dump -h /tmp -p 5433 -U grapevine -d grapevine \
    --schema-only --no-owner --no-privileges -f schema/live-snapshot.sql

# 2. counts
./scripts/db.sh psql -At -c "SELECT count(*) FROM information_schema.tables
    WHERE table_schema='public' AND table_type='BASE TABLE'"
./scripts/db.sh psql -At -c "SELECT count(*) FROM information_schema.views WHERE table_schema='public'"
./scripts/db.sh psql -At -c "SELECT count(*) FROM pg_trigger tg
    JOIN pg_class c ON c.oid=tg.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND NOT tg.tgisinternal"
./scripts/db.sh psql -At -c "SELECT count(*) FROM vocabularies"
./scripts/db.sh psql -At -c "SELECT count(*) FROM vocabulary_terms"

# 3. live inventories (piped to files and diffed against the parsed canonical files)
./scripts/db.sh psql -At -F'|' -c "SELECT c.table_name, c.column_name
    FROM information_schema.columns c
    JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
    WHERE c.table_schema='public' AND t.table_type='BASE TABLE'
    ORDER BY c.table_name, c.ordinal_position"
./scripts/db.sh psql -At -c "SELECT table_name FROM information_schema.views
    WHERE table_schema='public' ORDER BY 1"
./scripts/db.sh psql -At -c "SELECT c.relname||'.'||tg.tgname FROM pg_trigger tg
    JOIN pg_class c ON c.oid=tg.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND NOT tg.tgisinternal ORDER BY 1"
./scripts/db.sh psql -At -c "SELECT p.proname FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY 1"
./scripts/db.sh psql -At -c "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY 1"
./scripts/db.sh psql -At -c "SELECT name FROM vocabularies ORDER BY 1"
./scripts/db.sh psql -At -F'|' -c "SELECT vocabulary, term FROM vocabulary_terms ORDER BY 1,2"

# 4. canonical side — confirm no ADD COLUMN hides outside a CREATE TABLE block
grep -nEi '^ *ALTER TABLE' schema/claim_store.sql schema/vocabularies.sql
```

All database access was read-only. Nothing was created, altered or dropped.
