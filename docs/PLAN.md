# Grapevine — Unified Claim Store + v2 Video Pipeline

## Context

Two efforts have been running in parallel and are the same effort:

- **`a2zero-wiki/`** — ingests A2Zero reports into a typed Obsidian wiki (514 pages: 229
  initiatives, 154 actors, 7 strategies, contradictions, framing, funding-events). It also
  extracted **129 "quads"** into `blackboard/quads.jsonl`, then **paused them**. `CLAUDE.md`:
  *"the quad linter is paused pending schema redesign… produces output that no downstream pipeline
  currently consumes."*
- **`video_analysis/`** — a v1 prototype that extracted one Sustainability Commission meeting into
  a 1.7 MB JSON blob, then designed a 42-table `claim_store.sql` to replace it.

**The claim store IS the schema redesign the quads were paused pending.** This plan condenses them
into one store fed by video, reports, minutes, correspondence, webpages, and — as the corpus grows
— regulatory dockets.

### v1 video code review — three blocking defects, one unexpected capability
Full evidence: `video_analysis/project_handover/docs/v2-plan.md`.

- **`segment_id` collides** — `utils.py:17-20` rounds start times to 10s buckets. 1,156 turns →
  631 unique IDs; **525 turns (45%) carry another turn's extraction.**
- **V4 never merges same-speaker turns** — `v4_merge_turns.py:75-106`. 1,198 diarization segments →
  1,156 "turns". Consequences: 44.6% text duplication, total text 1.79× the transcript, and
  **71 empty-text turns that all received fabricated claims.**
- **V3 fabricated 5 of 18 speaker identities** — 8 clusters first speak after minute 20; the clip
  uploaded was 0–20 min. Five were named `source: "placard"`, `confidence: 1.0` from video they
  never appear in (incl. both DTE reps). `v3:193` then auto-set `validated: true`.
- **Segmentation works.** V5 fetched the Legistar agenda *and* minutes into the prompt and produced
  12 correct agenda-aligned sections with no index points, plus two agenda-deviation detections.
  The roadmap's planned monotonic-DP aligner is unnecessary.

### Five-source stress test (2026-07-27)
Tested against `cap-2020.md`, `a2zero-year5.md`, the `lWvRVUMyLP4` transcript, `a2gov.org`
carbon-neutrality page, and the Building Decarbonization Coalition Ann Arbor case study. Validated
`asserted_events`, `covers_period_*`, date precision, and `verbatim NOT NULL`. Exposed eight gaps,
three severe — all folded in below. The thesis held on a second body: the report *announces* the
DTE franchise in one sentence; the video explains the instrument (three-layer structure, quarterly
leadership panel, annual stakeholder requirement, termination clause) across 47 mentions.

**Intended outcome:** one claim store with provenance to the moment and precise world-time,
supporting chronological/timeline analysis and a ranked dark-matter queue that feeds future source
acquisition.

---

## Part I — Nomenclature

An earlier draft proposed `plan → strategy → subject → sub_subject` as a fixed tree imported from
the wiki. **Rejected as unportable** — it conflates a *document's* internal structure with *world*
containment. A city has many plans (Comprehensive Land Use Plan, Annual Budget, Capital
Improvements Plan); `strategy` exists only because A2Zero defines seven; and a Subject appearing in
two plans would have to falsely pick one parent.

```
TOPIC          broad · few · stable · curated · CROSS-CITY
               the ONLY commensurability surface. "Grid Decarbonization"
                 │
SUBJECT TREE   world containment · recursive · arbitrary depth · jurisdiction-scoped
               SEU ⊃ Solarize Ann Arbor ⊃ Commercial Solarize Pilot
               no fixed level names, no tree_level enum
                 │
FRAMEWORKS     a document's category system, as many-to-many TAGS not a tier
               "A2Zero CAP-2020 strategies" → 7 categories → CAP-2020's named Actions
               a Subject may sit in A2Zero Strategy 2 AND the Comp Plan's Land Use
               chapter AND the FY26 budget simultaneously
                 │
             CLAIMS  ← the only join point: subject_id × issue_id
                 │
ISSUE          narrow · emergent · many · CROSSES SUBJECTS
               NOT contained by Subject — recurrence across Subjects is the finding
ARGUMENT       canonical REASON · orthogonal to Topic, Subject, and Issue
```

**Why Issue is not under Subject:** with `issues.subject_id` as an FK, "groundwater contamination
risk" raised about Bryant and about another project become two unrelated rows, and noticing they are
the same concern needs fuzzy matching. Containment is **derived**:
`SELECT DISTINCT issue_id FROM claims WHERE subject_id = X`.

| Entity | Definition | Distinguishing test | Named by |
|---|---|---|---|
| **Topic** | Navigational category spanning cities | Would Boston have one with the same name? | Human, once |
| **Subject** | The unit of discussion — a named program/project/policy effort | Can you point and say "that thing"? | Human, from proposals |
| **Framework** | A category system defined by one document | Does a specific document enumerate it? | Human, on import |
| **Issue** | A contested sub-question; what is *in dispute* | Could two people disagree? Could it recur on another Subject? | Human, from proposals |
| **Argument** | The canonical form of a reason | Could it be deployed on a different Issue entirely? | Human — least parallelizable |
| **Claim** | One actor's position, at one moment, on one Issue about one Subject | Does it have a verbatim span? | Machine, human-reviewed |
| **Reason** (was *warrant*) | An instance of an Argument in one Claim. Persuades. | Is it a *reason*? | Machine |
| **Condition** | A contingency the position depends on. Negotiates. | Is it a tradeable *move* — or a documented constraint? | Machine, always human-checked |

Subject = *what we're talking about*. Issue = *what's in dispute about it*. Argument = *why someone
holds their position*. Claim = *one person saying so, once, on the record*.

**Reason : Argument :: utterance of a word : dictionary entry.** First occurrence creates the
Argument at n=1; repetition makes it interesting, not existent. Propagation is
`COUNT(*) GROUP BY argument_id`.

**Renames.** `claim_warrants` → `claim_reasons`; `warrant_text` → `reason_text`; `warrant_type` →
`reason_class` (DDL comment notes the Toulmin correspondence). **Quad `subject` → `actor_slug`**
before any migration — it means the grammatical subject (an actor), the opposite of
`claims.subject_id`. "Topic" has three incompatible meanings across the repos; only the
Topic-registry sense survives.

---

## Part II — Two architectural rules

### Rule 1 — Semi-open ontology, everywhere, human-gated

Every controlled vocabulary follows the wiki's proven pattern: **approved terms → LLM uses the
closest fit and proposes when nothing works → drift log → human approves → registry grows.**

The DDL currently expresses ~26 vocabularies as **SQL comments** (`barrier_class TEXT NOT NULL,
-- legal | regulatory | financial | …`). Comments enforce nothing; `CHECK` constraints can't grow
without a migration. Both fail Rule 1.

```sql
CREATE TABLE vocabularies (
    name            TEXT PRIMARY KEY,       -- 'barrier_class', 'reason_class', 'doc_type', …
    description     TEXT NOT NULL,
    is_open         BOOLEAN DEFAULT TRUE    -- FALSE for closed sets like polarity
);

CREATE TABLE vocabulary_terms (
    vocabulary      TEXT NOT NULL REFERENCES vocabularies(name),
    term            TEXT NOT NULL,
    description     TEXT,
    approved_by     TEXT NOT NULL,          -- human required
    approved_at     TIMESTAMPTZ DEFAULT now(),
    deprecated_by_term TEXT,                -- vocabularies evolve; keep old rows readable
    PRIMARY KEY (vocabulary, term)
);

-- The drift log. Mirrors meta/schema-drift.md.
CREATE TABLE vocabulary_proposals (
    id              BIGSERIAL PRIMARY KEY,
    vocabulary      TEXT NOT NULL REFERENCES vocabularies(name),
    proposed_term   TEXT NOT NULL,
    written_as      TEXT NOT NULL,          -- the approved term actually stored on the row
    entity_table    TEXT NOT NULL,
    entity_id       BIGINT NOT NULL,
    rationale       TEXT,
    example_verbatim TEXT,
    occurrences     INT DEFAULT 1,          -- recurrence is the promotion signal
    status          TEXT DEFAULT 'pending', -- pending | approved | rejected | merged
    resolved_by     TEXT, resolved_at TIMESTAMPTZ
);
```

**Enforcement is a trigger, not a `CHECK`**, and it is deliberately permissive: on an unknown term
it stores the closest approved term, writes/increments a `vocabulary_proposals` row, and sets a
`vocab_pending` flag on the entity. **It never rejects the insert** — hard-rejecting would discard
extraction work, which is exactly what `v8_assemble.py`'s enum coercion did badly. Same behaviour as
`proposed-type:` in the wiki, where the page is written with the closest approved type *and* the
proposal is logged.

Unresolved proposals surface in `review_queue` with `reason = 'vocab_proposal'`, prioritised by
`occurrences`. `curation_decisions` records rejections durably so the same term is never
re-proposed.

### Rule 2 — The corpus grows, driven by dark matter

Dark matter (§ below) generates supplemental research questions, which drive acquisition of new
source types. **The source-type list is not fixed and must never be hard-coded.** `doc_type` is a
Rule-1 vocabulary, so a new source type is an approved INSERT.

Three tables form the **interface** to the action-discovery process being built in parallel. This
plan owns the emit side only; the discovery mechanism is out of scope.

```sql
-- Emitted by dark-matter detection, promoted by a human. Formalizes research-agenda.md.
CREATE TABLE research_questions (
    id              BIGSERIAL PRIMARY KEY,
    question        TEXT NOT NULL,
    subject_id      INT REFERENCES subjects(id),
    issue_id        INT REFERENCES issues(id),
    origin          TEXT NOT NULL,          -- dark_matter_gap | outcome_without_mechanism |
                                            -- unmet_condition | commitment_unclosed |
                                            -- numeric_conflict | human
    origin_claim_id BIGINT REFERENCES claims(id),
    priority        INT DEFAULT 5,
    status          TEXT DEFAULT 'open',    -- open | answered | abandoned | superseded
    promoted_by     TEXT,                   -- human gate before it drives acquisition
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- What to go find. Consumed by the external action-discovery process.
CREATE TABLE source_targets (
    id              BIGSERIAL PRIMARY KEY,
    research_question_id BIGINT REFERENCES research_questions(id),
    target_description TEXT NOT NULL,       -- 'MPSC gas case testimony, U-21297'
    doc_type        TEXT,                   -- Rule-1 vocabulary
    venue           TEXT,
    expected_url    TEXT,
    status          TEXT DEFAULT 'identified',
                    -- identified | searched | acquired | prepared | ingested |
                    -- not_found | inaccessible | out_of_scope
    resolved_document_id INT REFERENCES documents(id)
);

-- CRITICAL for dark-matter epistemics: "we looked and it isn't there" is a DIFFERENT
-- statement from "we haven't looked." Without this, every gap is ambiguous.
CREATE TABLE source_search_log (
    id              BIGSERIAL PRIMARY KEY,
    subject_id      INT REFERENCES subjects(id),
    doc_type        TEXT NOT NULL,
    venue           TEXT,
    searched_by     TEXT NOT NULL,
    searched_at     TIMESTAMPTZ NOT NULL,
    query_used      TEXT,
    outcome         TEXT NOT NULL,          -- found | none_exist | exists_inaccessible | partial
    coverage_note   TEXT
);
```

**Every dark-matter finding must cite `source_search_log`** or be labelled *unsearched*. This is
the mechanism that keeps a non-exhaustive corpus honest.

---

## Part III — Decisions locked

| Decision | Rationale |
|---|---|
| **One store; quads retired** | The quad path is paused pending exactly this redesign. |
| **Frameworks replace the Plan→Strategy tier** | Strictly more expressive; portable to cities with different or absent plan structure. |
| **Semi-open ontology on all ~26 vocabularies, trigger-enforced, never hard-rejecting** | Rule 1. Comments enforce nothing; CHECKs can't grow. |
| **Corpus source types grow via dark matter → research questions → source targets** | Rule 2. Interface only; discovery is a parallel effort. |
| **Files through segmentation, Postgres above** | Lower rungs re-derivable and diffable. Boundary at S5→S6. |
| **Per-utterance hash for invalidate-forward** | Speaker relabels are the common correction; per-file hashing would invalidate a meeting and discourage correcting anything. |
| **Text-first speaker ID; video deferred, not deleted** | Council uses illegible physical placards and is ~130–150 events vs. the commission's 17. Roll call is a free labelled enrollment set every meeting. |
| **Voiceprints for `is_public_figure = TRUE` only** | `HANDOFF.md:76`. |
| **Subject discovery: inductive clustering triangulated against the wiki** | 229 initiatives already exist — human work is *promotion from a candidate list*, not invention. |
| **Issues + arguments in scope corpus-wide, machine-proposed, HITL confirm** | Avoids the case-2 retrofit. **Accepted cost: argument naming, which does not sample or parallelize.** |
| **Duplication resolves at the referent layer, never the assertion layer** | `claims` are never deduplicated — each is one actor at one moment. Repeated *facts* → `asserted_events`; repeated *reasons* → `arguments`. |
| **Corroboration measured by independence, not count** | BDC's 77% figure was contributed by city staff — one source published twice, not two sources. |
| **Dark matter ships as a ranked lead queue, never a finding** | Every gap cites `source_search_log` or is labelled unsearched. |
| **Embeddings propose; symbols decide** | Cosine for candidate sets; canonical integer IDs for every join, count, and absence test. |
| **Multimodal affect scrapped; `citations` dropped** | `HANDOFF.md:84-89`; `citations` was 0/1,157. |
| **Drop the planned agenda→transcript aligner** | V5 already works and additionally reports agenda deviations. |
| **News deferred; MPSC dockets promoted ahead of it** | Ann Arbor claims **$1B in ratepayer savings** from MPSC interventions. Text-native, cheaper than video, and the decisive venue wherever an IOU is the sponsor. |
| **a2zero-wiki is read-only; one-way export; new repo/path** | Protect existing work. Never a live dependency. |

---

## Part IV — Schema changes to `claim_store.sql`

### Blocking — cold-start deadlock
```sql
-- claims.subject_id NOT NULL (:393) makes insertion impossible until a human names the
-- Subject, but Subjects are discovered BY extracting. Blocks extraction on day one.
ALTER TABLE claims ALTER COLUMN subject_id DROP NOT NULL;
ALTER TABLE claims ADD COLUMN curation_state TEXT NOT NULL DEFAULT 'unassigned';
  -- unassigned | proposed | confirmed; enforce NOT NULL before page_manifest entry
```

### Time
```sql
-- WORLD time on claims, distinct from utterance time. Without these, timeline
-- generation is impossible: the only available date is when someone spoke.
ALTER TABLE claims ADD COLUMN asserted_start DATE;
ALTER TABLE claims ADD COLUMN asserted_end   DATE;      -- intervals; both nullable
ALTER TABLE claims ADD COLUMN asserted_precision TEXT;
       -- day|month|quarter|year|fiscal_year|era|relative|unresolved
ALTER TABLE claims ADD COLUMN asserted_calendar TEXT DEFAULT 'calendar';  -- calendar|fiscal
ALTER TABLE claims ADD COLUMN asserted_date_text TEXT;  -- verbatim time expression
ALTER TABLE claims ADD COLUMN date_confidence NUMERIC(3,2);
ALTER TABLE claims ADD COLUMN date_validation_flag BOOLEAN DEFAULT FALSE;

ALTER TABLE documents ADD COLUMN published_date DATE;
ALTER TABLE documents ADD COLUMN covers_period_start DATE;
ALTER TABLE documents ADD COLUMN covers_period_end DATE;
```
Then **fix `v_argument_propagation` (`:690`)**, which does
`COALESCE(e.event_date, d.retrieved_at::date)` — the scrape date. The wiki already hit and fixed
this bug via `covers-period-start`/`covers-period-end`; the store hasn't.

**Ann Arbor fiscal year — CONFIRMED (City Charter): July 1 → June 30.**
`"FY25" → asserted_start 2024-07-01, asserted_end 2025-06-30, precision 'fiscal_year',
calendar 'fiscal'`. Two consequences: (1) an FY25 claim and a calendar-2025 claim overlap by only
six months, so timeline queries must compare **intervals**, never `asserted_start` alone; (2) the
observed ASR error "fiscal year 2005" for FY25 becomes machine-detectable. **Residual assumption:**
"FY25" taken as the year *ending* June 30 2025 (standard municipal convention) — confirm against one
budget document.

*Validated by the stress test:* Year 5's *"On May 20th, we supported the groundbreaking…"* carries
**no year**; it is resolvable only from `covers_period_*` (2024-06 → 2025-05).

### Duplication — the referent layer
```sql
CREATE TABLE asserted_events (
    id                  BIGSERIAL PRIMARY KEY,
    subject_id          INT REFERENCES subjects(id),
    canonical_description TEXT NOT NULL,
    event_class         TEXT,              -- Rule-1 vocabulary
    occurred_start      DATE, occurred_end DATE,
    occurred_precision  TEXT, occurred_calendar TEXT DEFAULT 'calendar',
    -- FREE AUTHORITY: Legistar actions arrive pre-identified with dates and votes, so any
    -- event matching one auto-links at high confidence with ZERO human naming.
    legistar_event_item_id INT REFERENCES event_items(id),
    named_by            TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE event_attestations (
    id                  BIGSERIAL PRIMARY KEY,
    asserted_event_id   BIGINT NOT NULL REFERENCES asserted_events(id) ON DELETE CASCADE,
    claim_id            BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    stated_start        DATE, stated_end DATE, stated_precision TEXT, stated_date_text TEXT,
    -- INDEPENDENCE, not presence. This is what makes the count mean anything.
    attestation_type    TEXT NOT NULL,     -- primary_record | firsthand | secondhand |
                                           -- restatement | boilerplate
    derives_from_attestation_id BIGINT REFERENCES event_attestations(id),
    text_similarity     NUMERIC(4,3),      -- cosine vs. nearest prior attestation
    UNIQUE (asserted_event_id, claim_id)
);
```
Three duplication axes, three mechanisms, never merged: repeated **fact** → `asserted_events`;
repeated **reason** → `arguments`; repeated **position** → N claims, counted.

Buys: **timeline salience** ranked by independent attestations (raw count would put boilerplate on
top); **date drift** free via `GROUP BY asserted_event_id HAVING COUNT(DISTINCT stated_start) > 1`;
and **`evidence_grade = 'B'` becomes computable** — it is defined as "corroborated by a second
source" and nothing currently computes corroboration. `derives_from_attestation_id` is the
anti-laundering mechanism and makes news a special case rather than a separate design.

*Validated:* two DOE Community Geothermal grants (2022 feasibility, Dec 2024 $10M deployment) that
Year 5 conflates into one; and BDC's 77% figure, contributed by city staff, which a naive count
would grade as corroborated.

### Quantities — GAP 1 (severe)
`fiscal_references` is money-only, but MW, tCO2e, percentages, and counts are the *substance* of
annual reports and where numeric drift lives.
```sql
CREATE TABLE quantities (
    id BIGSERIAL PRIMARY KEY,
    claim_id BIGINT REFERENCES claims(id) ON DELETE CASCADE,
    subject_id INT REFERENCES subjects(id),
    value_low NUMERIC, value_high NUMERIC,
    bound TEXT,                 -- exact | at_least | at_most | approximate  ("over 6.5 MW")
    unit TEXT NOT NULL,         -- Rule-1 vocab: MW|kW|tCO2e|USD|count|pct|acres|tons|hours
    unit_basis TEXT,            -- what a % or count is OF
    measure TEXT NOT NULL,      -- Rule-1 vocab: installed_capacity | emissions | goal_target |
                                -- participants | cost | savings | vote_share
    scope_note TEXT,            -- "Solarize only" vs "all since plan adoption"
    as_of_date DATE, as_of_precision TEXT,
    is_projection BOOLEAN DEFAULT FALSE,
    verbatim TEXT NOT NULL
);
```
*Why `scope_note` is not optional:* Year 5 prints **5.4 MW** (Solarize), **6.5 MW** (all since
adoption), and **11.88 MW** (dashboard) within one page. Three scopes, one metric. The wiki already
logged this as `contradictions/solarize-mw-scope.md`.

### Rejected alternatives — GAP 2 (severe)
```sql
CREATE TABLE considered_alternatives (
    id BIGSERIAL PRIMARY KEY,
    subject_id INT NOT NULL REFERENCES subjects(id),
    alternative_name TEXT NOT NULL,
    disposition TEXT NOT NULL,      -- adopted | rejected | deferred | untested
    rejection_class TEXT,           -- Rule-1 vocab: legal_authority | cost | timeline |
                                    -- technical | political | equity | operating_cost
    rejection_reason TEXT,
    superseded_by_alternative_id BIGINT REFERENCES considered_alternatives(id),
    claim_id BIGINT REFERENCES claims(id),
    -- a rejection reason IS a candidate scope condition — feed Phase 6 like dispute_basis
    is_scope_condition_candidate BOOLEAN DEFAULT TRUE,
    verbatim TEXT NOT NULL
);
```
**The most playbook-relevant structure in any source tested, and it had nowhere to go.** BDC
documents air-source heat pumps → rejected (operating cost "unacceptable"); utility-owned geothermal
→ rejected (*Michigan lacks enabling legislation*); full municipalization → rejected (cost,
and exceeds the 2030 deadline); → SEU adopted, legitimized by a 79% vote. `barriers` is not this — a
barrier is an obstacle encountered; a rejected alternative is a decision point with a stated
counterfactual, which is the direct antidote to §6.1 spurious transferability.

### Funding reversal — GAP 3 (severe)
```sql
ALTER TABLE fiscal_references ADD COLUMN award_status TEXT DEFAULT 'unknown';
       -- Rule-1 vocab: applied | awarded | obligated | disbursed | on_hold |
       -- terminated | rescinded | disputed | withdrawn | lapsed
ALTER TABLE fiscal_references ADD COLUMN status_as_of DATE;
ALTER TABLE fiscal_references ADD COLUMN status_change_reason TEXT;
ALTER TABLE fiscal_references ADD COLUMN awarding_org_id INT REFERENCES orgs(id);
```
Four reversals appear in Year 5 alone — returnable-container grant *"terminated by the federal
administration"*, Heat Resilient Communities *"terminated"*, a $1M EPA EJ award *"terminated… the
City is actively disputing"*, and the Bryant $10M DOE grant *"(Funding is currently on hold)"*.
A playbook recommending a clawed-back grant is §6.1's failure mode in its purest form. **The
reversal is the finding.**

### Conditions in text — GAP 4
```sql
ALTER TABLE claim_conditions ADD COLUMN condition_origin TEXT;
       -- negotiating_move | documented_constraint | design_assumption | regulatory_prerequisite
```
`ARCHITECTURE-AND-ROADMAP.md:240-252` predicts conditions by speech act — negotiating moves in live
deliberation. But CAP-2020 states one outright: *"In order to implement a CCA, the State will need
to enact legislation to allow for CCA,"* with a target of *"first bulk buy… by 2027."* BDC adds a
**20 MW viability floor** and a **6% interest-rate threshold**. The hypothesis isn't falsified — its
scope was narrower than stated.

CAP-2020 also carries a literal `**Assumptions**` heading per action. **The plan pre-registers its
own assumptions** — free Phase 6 condition vocabulary, needing no video.

### Mutable sources — GAP 5
```sql
ALTER TABLE documents ADD COLUMN is_mutable_source BOOLEAN DEFAULT FALSE;
ALTER TABLE documents ADD COLUMN snapshot_path TEXT;   -- spans resolve against THIS, not the URL
ALTER TABLE documents ADD COLUMN snapshot_hash TEXT;
```
The a2gov.org page has **no last-updated timestamp**, a © 2026 footer, and lists annual reports
**Year 1 through Year 6** — while the corpus has Year 1 ingested and Year 5 prepared. Year 6 exists
and isn't in the store. A char offset into a live URL is unverifiable within weeks.

### Authority outside Legistar — GAP 6
```sql
ALTER TABLE matters ADD COLUMN venue_type TEXT DEFAULT 'municipal_legislative';
       -- Rule-1 vocab: municipal_legislative | ballot_measure | state_legislation |
       -- regulatory_docket | federal_grant_program | court
ALTER TABLE matters ADD COLUMN external_identifier TEXT;  -- 'Proposal A', 'SB 271', MPSC case no.
ALTER TABLE matters ADD COLUMN venue_jurisdiction_id INT REFERENCES jurisdictions(id);
```
BDC names the SEU ballot measure **"Proposal A"**; Year 5 never does. **SB 271** is state
legislation. The **MPSC gas cases** are a regulatory docket. None is a Legistar matter.

### Policy diffusion — GAP 8
```sql
CREATE TABLE program_diffusion (
    id BIGSERIAL PRIMARY KEY,
    subject_id INT NOT NULL REFERENCES subjects(id),
    adopting_jurisdiction TEXT NOT NULL,   -- 'Grand Rapids, MI'
    adopting_ocd_id TEXT,
    relation TEXT NOT NULL,                -- expanded_to | replicated_by | partnership | exchange
    occurred_start DATE, occurred_precision TEXT,
    claim_id BIGINT REFERENCES claims(id),
    verbatim TEXT NOT NULL
);
```
*"Solarize was expanded throughout the State, including in Grand Rapids"* is the program **operating
elsewhere** — replication happening, Grapevine's dependent variable. `jurisdiction_citations` models
a speaker *invoking* another jurisdiction as evidence. Different relation.

### Dark matter
```sql
-- quad `dark_matter`: "an outcome stated with NO mechanism described" (_pages.py:42).
-- Grapevine's Outcome 1 thesis as a boolean. Generates a research question, not a finding.
ALTER TABLE claims ADD COLUMN outcome_without_mechanism BOOLEAN DEFAULT FALSE;
```
Two complementary detectors: **record-level** (above) catches sanitization *within* a source;
**corpus-level** (`NOT EXISTS` across `source_type`) catches sanitization *between* sources. Both
feed `research_questions`, and both must cite `source_search_log`.

### Curation and embeddings
```sql
CREATE TABLE curation_decisions (
    id BIGSERIAL PRIMARY KEY,
    entity_table TEXT NOT NULL, entity_a_id BIGINT NOT NULL, entity_b_id BIGINT,
    decision TEXT NOT NULL,     -- merge | keep_separate | rename | reject
    decided_by TEXT NOT NULL, decided_at TIMESTAMPTZ DEFAULT now(), rationale TEXT
);

ALTER TABLE claim_reasons ADD COLUMN embedding vector(1536);
ALTER TABLE claim_reasons ADD COLUMN embedding_model TEXT;    -- REQUIRED
ALTER TABLE claim_reasons ADD COLUMN embedding_version TEXT;  -- REQUIRED
CREATE INDEX ON claim_reasons USING hnsw (embedding vector_cosine_ops);
```
Store model+version **with** the vector — re-embedding with a different model silently invalidates
every stored neighbour. `pages.embedding vector(1536)` (`:635`) pins a dimension; switching models
is a migration.

**Clustering constraint (not optional):** stratify by `contested_dimension` and `reason_class`
before comparing. *"We can't afford it this year"* and *"we can't afford it at all"* have
near-identical embeddings and are different arguments — timing vs. substance. `:405-408` explains
why collapsing them is fatal.

### Text-corpus readiness and provenance hardening
```sql
ALTER TABLE claims ALTER COLUMN speech_act DROP NOT NULL;      -- enum is entirely oral
ALTER TABLE claims ALTER COLUMN actor_capacity DROP NOT NULL;  -- a 200-page plan has none
ALTER TABLE claims ADD COLUMN source_content_hash TEXT;
ALTER TABLE claims ADD COLUMN reported_by_document_id INT REFERENCES documents(id);
  -- News is out of v2 scope; column kept to avoid a later migration.

-- NOT NULL accepts ''. v8_assemble.py:51-60 is live proof of code that writes exactly that.
ALTER TABLE claims ADD CONSTRAINT verbatim_nonempty CHECK (length(trim(verbatim)) > 0);
-- same on claim_reasons, claim_conditions, jurisdiction_citations, commitments,
-- fiscal_references, barriers, quantities, considered_alternatives, program_diffusion
```

### Subject tree + frameworks + V5 deviation capture
```sql
ALTER TABLE subjects ADD COLUMN parent_subject_id INT REFERENCES subjects(id);
ALTER TABLE subjects ADD COLUMN topic_id INT REFERENCES topics(id);
ALTER TABLE subjects ADD COLUMN wiki_slug TEXT;
-- NO tree_level column. Depth is whatever the world is.

CREATE TABLE frameworks (
    id SERIAL PRIMARY KEY,
    defining_document_id INT REFERENCES documents(id),
    jurisdiction_id INT REFERENCES jurisdictions(id),
    name TEXT NOT NULL, valid_from DATE, valid_to DATE
);
CREATE TABLE framework_categories (
    id SERIAL PRIMARY KEY,
    framework_id INT NOT NULL REFERENCES frameworks(id),
    code TEXT, name TEXT NOT NULL,
    parent_category_id INT REFERENCES framework_categories(id), sequence INT
);
CREATE TABLE subject_framework_categories (
    subject_id INT NOT NULL REFERENCES subjects(id),
    category_id INT NOT NULL REFERENCES framework_categories(id),
    assigned_by TEXT NOT NULL,
    PRIMARY KEY (subject_id, category_id)
);

ALTER TABLE segments ADD COLUMN agenda_deviation_note TEXT;
ALTER TABLE segments ADD COLUMN deviation_kind TEXT;   -- Rule-1 vocabulary
```
*Import note:* CAP-2020 defines **two** framework levels (Strategy → named Action, e.g.
`### Implement Community Choice Aggregation`), and Year 5 reports only at Strategy level.
`parent_category_id` handles it. The CAP markdown contains **duplicate headings** from PDF
conversion (`### Electrify Private Fleets` at :1118 and :1182; `### Develop Energy Concierge…` at
:1630 and :1684) — dedupe on import, don't create two categories. Also note Year 5 names Strategy 7
**"OTHER"** while the wiki names it `strategy-7-engagement`; the source wins.

---

## Part V — Pipeline

Every stage writes `{schema_version, producer_version, input_hash}`. Downstream rows carry
`source_hash`. Stale = `WHERE source_hash <> current_hash_of_source`. Reuse the `pages.content_hash`
pattern (`:628`).

```
S0  ACQUIRE     yt-dlp → video, audio, metadata                          [file]
S1  TRANSCRIBE  WhisperX + per-jurisdiction custom vocab, word ts        [file]
S2  DIARIZE     pyannote 3.1, return_embeddings=True                     [file]
S3  TURNS  ★    same-speaker coalescing + interjections                  [file] ◄ DURABLE ASSET
S4  SPEAKER ★   roll-call enrollment → embedding registry → names        [file] ◄ DURABLE ASSET
S5  SEGMENT     agenda + minutes + transcript triangulation              [file]
──────────────────────────── Postgres boundary ────────────────────────────
S6  CLASSIFY ★  segment_kind, extraction_tier, is_substantive            [pg]
S7  EXTRACT  ★  Tier A/B ∧ substantive, section-scoped, text-only        [pg]
S8  CANONICAL★  Subjects/Issues/arguments/events + wiki triangulation    [pg]
S9  REVIEW      triaged queue; minutes_spent BY REASON                   [pg]
```

**S3+S4 jointly are the human-correctable diarized transcript.** All speaker relabels and
transcript fixes land there once and amortize across every re-extraction. Nothing may `UPDATE
utterances` in Postgres directly — the file side is authoritative for that layer.

**A parallel text path (S6t/S7t/S8) handles reports, plans, minutes, webpages, and dockets.** It
skips S0–S5 entirely, sections on document headings instead of agenda alignment, and joins the same
S6–S9 stages. This is where CAP-2020, the annual reports, and MPSC filings enter.

### S3 — fix identity and merging first (blocks everything)
`pipeline/utils.py` — replace `make_segment_id`; identity must not depend on a rounded float:
```python
def make_utterance_key(media_asset_id: str, sequence: int, start_ms: int, end_ms: int) -> str:
    return f"{media_asset_id}:{sequence:05d}:{start_ms}-{end_ms}"
```
Add `UNIQUE (media_asset_id, sequence)` so a collision is a DB error, not a silent dict overwrite.

`pipeline/v4_merge_turns.py` — rewrite the merge. Coalesce consecutive same-speaker segments at
**2.0s gap tolerance** (measured: 1,156 → ~470 turns), then apply the existing interjection-sandwich
logic. Replace `collect_text`'s overlap collection with **word-level assignment** from WhisperX.

### S1/S2
`v1_transcribe.py`: WhisperX for forced alignment. Add `initial_prompt` custom vocabulary per
jurisdiction — domain terms, member names, agenda prefixes (`DC-1`), years and fiscal-year forms.
There is currently **none**; `vocabulary.json` holds only the constituency enum.

`v2_diarize.py:55`: `pipeline(audio_path, return_embeddings=True)`. Centroids are computed and
discarded — one kwarg, and the reason `voice_embedding: []` has never held data.

### S4 — text-first speaker ID
Delete the video path in `v3_resolve_speakers.py`; keep the registry merge but **remove the
auto-validation gate at `v3:193`** — `validated` must be human-set, never model self-confidence
(v1: `1.0` for 22 of 23 clusters).

Order: (1) **roll-call enrollment** — chair names each member, member answers; a name↔voice pair for
the whole body, every meeting, entirely in the transcript; (2) chair address; (3) self-ID in public
comment; (4) minutes attendee roster as a closed-world constraint; (5) embedding match,
`is_public_figure = TRUE` only. (5) also auto-merges v1's split clusters (Curtis 03/05, Kothari
07/11, Stults 00/19). Unresolved → `review_queue`.

### S5 — keep the method, fix the plumbing
1. **Per-event document URLs.** `body_registry.json:27-28` are hardcoded single-meeting Legistar
   URLs with a literal `EventId`+`GUID`; `format_agenda_url` only substitutes date tokens. **As
   written, V5 fetches the March 10 documents for every video.** Look up per event from `events`
   (~10 lines). Keep the PDF fallback — `AADA`/`MADA` HTML only exists from ~March 2026 for Council.
2. **Per-source character budgets, loud on truncation.** `v5:61` returns `text[:12000]` shared
   across sources; a Council packet will exceed it silently.
3. **Assertion harness:** non-overlapping, monotonic, no gaps, coverage ±30s, section count within
   2× agenda item count, every boundary timestamp present in the transcript.

### S6 — Pass 1 classifier (cheap, exhaustive, text-only)
One call per section, ~12/meeting, using the provider's **structured-output mode** —
`schema/video_output_schema.json` becomes an API-bound schema with a tier discriminator, not a
post-hoc `jsonschema.validate`. v1 sent no schema to the model at all (`v7:139`, `v6:54`, `v5:114`).

→ `segments`: `segment_kind`, `extraction_tier`, `tier_assigned_by`, `summary` (**Tier C stops
here — the whole browse affordance**).
→ `utterances`: `is_substantive`, `actor_capacity`, `responds_to_utterance_id` (stated nullable),
`date_validation_flag`.

Deterministic pre-gates, no model call: empty/whitespace text → not substantive + `review_queue`
(catches all 71 v1 fabrications); `duration_ms < 2000 AND word_count < 8` → not substantive;
`segment_kind = 'roll_call'` → forced Tier C.

### S7 — Pass 2 extraction (gated, section-scoped, text-only)
Selector, **both gates ANDed**: `WHERE s.extraction_tier IN ('A','B') AND u.is_substantive`.
Measured: 438 turns → 378 Tier A/B → **247 also substantive.**

- **Extract at section scope, attribute at turn scope.** Batch a section's substantive turns with
  surrounding context; every claim names its `utterance_key` and span. Turn-by-turn extraction
  produced *"The speaker confirms his presence at the meeting in Ann Arbor."* This is the roadmap's
  own wiki-beats-RAG argument (`:22-24`) applied to extraction.
- **`verbatim` validated by exact substring match before insert.** Fail the claim, not the batch.

Tier A → claims, reasons, conditions, relations, jurisdiction_citations, decisions, commitments,
fiscal_references, quantities, barriers, coalitions, considered_alternatives, program_diffusion.
Tier B → claims + reasons. Tier C → no call.

**Delete `pipeline/v7_argumentation.py`.** Its only video-dependent outputs were `tone` and
`demeanor`; it spent 37.9M video tokens on them. `v8_assemble.py` becomes a DB writer — keep the
validation discipline, **delete the `else` branch at `:51-60`**.

### S8 — canonicalization
`v6_extract_sections.py` retargeted: `entities_discussed[]` with `definition_in_context` and
`mention_count` becomes the proposal feed.

1. Cluster proposals from entities + claims; rank by mention count and cross-meeting recurrence.
   Embeddings for candidate retrieval, stratified per the constraint above.
2. **Triangulate against the wiki export** — 229 `initiatives/`, 154 `actors/`, 7 strategy
   categories. Three buckets: *matches a wiki page* (promote), *no wiki page* (**novel — a
   Subject-level dark-matter signal**), *wiki page with no discussion* (the reverse gap).
3. HITL confirm gate. `subjects.created_by`, `issues.named_by`, `arguments.named_by`,
   `asserted_events.named_by` stay human. **A cluster ID never becomes a canonical key.**
   Rejections → `curation_decisions`.
4. Assignment (segment→Subject, claim→Issue, reason→Argument, claim→asserted_event) is machine,
   sampled audit. ~3,000 segment links over 5 years is not human-viable; ~229 candidate names is.
5. **`asserted_events` resolution.** Auto-link anything matching a Legistar `event_item`. For the
   remainder, propose merges on `same subject + overlapping date window + high text_similarity`;
   human confirms. Classify each attestation and set `derives_from_attestation_id` where one source
   visibly copies another — that pointer is what stops six annual reports counting as six sources.
6. **Vocabulary proposals** triaged from `vocabulary_proposals`, prioritised by `occurrences`.
7. **Dark matter → `research_questions`**, human-promoted, each citing `source_search_log`.

### S9 — review, instrumented
`review_queue.minutes_spent` measured **separately by `reason`**. Add `argument_naming`,
`coverage_gap`, `vocab_proposal`, `event_merge`. Averaging them hides the one that matters.

---

## Part VI — Corpus roadmap

`doc_type` is a Rule-1 vocabulary; this is the expected growth order, not a fixed list.

| Wave | Source types | Notes |
|---|---|---|
| **v2 (now)** | `plan` (CAP-2020), `annual_report` (Y1–Y6), `minutes`, `agenda`, `video_utterance`, `webpage` | Y6 exists on a2gov.org and is **not** in the corpus |
| **next** | **`regulatory_docket`** — MPSC filings, testimony, orders | Ann Arbor claims **$1B ratepayer savings** from its interventions; *"the two gas cases"*; *"calling for a 'Future of Gas' proceeding"*. Text-native, cheaper than video. **Decisive venue wherever an IOU is the sponsor — Framingham (Eversource/MA DPU) and probably Boston.** Corrects §6.2's assumption that Ann Arbor's contestation lives only in council video. |
| **next** | `ballot_measure` (Proposal A), `state_legislation` (SB 271), `rfp` (RFP 25-21, 25-23) | `matters.venue_type` + `external_identifier` |
| **then** | `correspondence`, `ecomment`, `third_party_case_study`, `dashboard` | BDC-type sources: classify `attestation_type` from contributor credits |
| **later** | `news` | Editorial posture unresolved; hook in place |
| **driven by dark matter** | whatever `source_targets` names | The action-discovery process (parallel effort) consumes `research_questions` + `source_targets` and returns acquired documents |

---

## Part VII — Migration

**Do not migrate v1's analytical output.** 45% misattributed, 6% fabricated, wrong units — the
`verbatim` would be a real quote but not the quote the claim came from, undetectable downstream.
Re-run S3→S8 from cached intermediates: **~$1.30**, no download, no Whisper, no pyannote, no
re-segmentation. Cheaper than writing the migration script.

| Reuse | Discard |
|---|---|
| `v1_transcript.json` (2.0 MB, word ts) | `v4_turns.json` — rebuild |
| `v2_diarization.json` (108 KB) | `v7_argumentation.json` |
| `v5_sections.json` (the 12 sections) | `output/lWvRVUMyLP4.json` as *data* |
| `v6_extracts.json` as proposal seeds | `v3_speakers.json` names — 5 are fabricated |

Keep `output/lWvRVUMyLP4.json` as the **eval negative control**: confident, schema-valid,
well-written, wrong.

**Quad retirement:** migrate the 129 records into `claims`. They have **no `verbatim`** —
document-level `sources` only — so they land as `evidence_grade = 'D'` with a re-extraction flag,
not usable claims. Then delete `_legacy/quad_linter.py`, `_legacy/post_ingest.py`, and
`--include-quads`. Rename `subject` → `actor_slug` first.

**Wiki export (one-way, read-only):** 229 initiatives → Subject candidates; 154 actors →
persons/orgs candidates; 7 strategies → one `framework` + categories; `covers-period-*` →
`documents.covers_period_*`; `contradictions/` (3) → `claim_relations.contradicts` seeds;
`funding-events/` (35) → `fiscal_references` seeds; `registry/entity_aliases.json` →
`subject_aliases`/`person_aliases`; `merge-log.jsonl` `KEEP_SEPARATE` rows → `curation_decisions`.

`body_description` supplies two `body_lineage` rows (Energy + Environmental → Sustainability, 2025).
Both predecessors must be ingested or a 5-year retrospective silently drops the earlier record.

---

## Part VIII — Cost

| | v1 actual | v1 clean | **v2** | v2 (Flash extract) |
|---|---|---|---|---|
| per 2-hr meeting | $14.50 | $7–8 | **$1.71** | **$0.79** |

Decomposed: removing video from extraction **~40×**; fixing the merge 2.6×; the tier gate 1.8×;
bigger batches 1.6×. **Video removal dominates by an order of magnitude — a consequence of
scrapping affect, not of tiering.** Tiering earns its place through the *review* budget. Full
retrospective inference ~$350–500; inference is not the constraint, human review is.

Add token/cost accounting to every model call. v1 has none — zero hits for `usage_metadata`,
`token_count`, `cost`.

---

## Part IX — Verification

1. **S3 unit tests** on cached `v2_diarization.json`: ~440–470 turns; text ratio ≤1.05× (currently
   1.79×); zero empty-text extraction units; zero duplicate utterance keys.
2. **Legistar API check** — `/eventitems/{id}/votes` per-member votes populated? The one unresolved
   Phase 0 question (`legistar-a2gov-notes.md:180`). Supplies the `EventId`/`GUID` S5 needs.
3. **S5 assertion harness**, then hand-score boundary error in seconds on **5 meetings across 2
   bodies** against minutes. Never measured.
4. **Speaker ID accuracy** — eval protocol Tier 2, hand-label ~20 min, text-only vs. ground truth.
   Officials and public reported separately. Decides whether video returns.
5. **Date accuracy** — sample 30 dated claims; check `asserted_*` against source; log fiscal/calendar
   confusions separately. Include Year 5's *"On May 20th"* (year inferable only from
   `covers_period_*`) as a required pass.
6. **S7 claim fidelity** — eval protocol Tier 3, all 10 tests, on the Sustainable Heating Franchise
   section. **Condition agreement is the make-or-break number** (`ARCHITECTURE-AND-ROADMAP.md:717`);
   v1 has zero condition data so it cannot be scored against v1. Log fabrication rate separately.
7. **Negative control** — run the harness against `output/lWvRVUMyLP4.json`; confirm it scores badly.
8. **Timeline smoke test** — render one Subject's `asserted_events` chronologically, ranked by
   independent attestations. Confirm interval comparison handles fiscal and relative dates, and that
   **no boilerplate restatement ranks above a genuinely corroborated event.** Without that check the
   timeline is a frequency count of copy-paste.
9. **Cross-source regression suite** — these five must reproduce, as they were found by hand:
   - two distinct DOE geothermal grants (2022 feasibility, Dec 2024 $10M), not one merged event
   - three MW figures with distinct `scope_note` (5.4 Solarize / 6.5 since-adoption / 11.88 dashboard)
   - four `award_status` reversals in Year 5
   - CCA condition unmet from CAP-2020 (2020) through Year 5 (2025), against a 2027 target
   - BDC's 77% classified `secondhand` with `derives_from` → **not** counted as corroboration
10. **Vocabulary loop** — force an unknown `barrier_class`; confirm the row is stored with the
    closest approved term, a proposal is logged with `occurrences`, and nothing is rejected.
11. **Human review of every claim** on one section; `minutes_spent` by reason.
12. **Second meeting** — a contested Council vote plus one low-scoring control. Everything above is
    n=1 on the easy case.

---

## Part X — Risks carried forward

- **Argument naming is uncosted and corpus-wide.** Doesn't sample, doesn't parallelize. Expected
  largest human cost in v2. Instrumented in S9.
- **Four curation registries now** (Subjects, Issues, arguments, asserted_events) plus vocabulary
  proposals. `asserted_events` is cheapest — Legistar supplies votes and resolutions
  pre-identified. Still real; measure each separately.
- **Dark matter is a lead queue, never a finding.** Every gap cites `source_search_log` or is
  labelled unsearched. With a partial corpus, absence can only mean *"our corpus lacks this."*
- **Embedding drift.** Model+version per vector; `pages.embedding vector(1536)` pins a dimension.
- **n=1 on the easy case.** All video confidence rests on a 17-member advisory body with clean
  audio, a narrating chair, and a tidy agenda. Council is 2× the size with roll-call theater,
  crosstalk, 3-hour runtimes, and some sessions not broadcast at all.
- **`evidence_grade` still does two jobs** — extraction confidence and corroboration.
  `event_attestations` makes corroboration computable, which resolves the worst of it.
- **"FY25" naming convention assumed** to mean the year *ending* June 30 2025. Boundary confirmed
  (July 1 → June 30, City Charter). One budget document settles the label.
- **Year 6 annual report is published and not in the corpus.** First `source_targets` row.
- **MPSC docket scope is unestimated.** Promoted to the next corpus wave on the strength of a $1B
  claim; volume, format, and access are unknown. Recon needed before committing a timeline.
- **News ingest deferred.** Hook in place; editorial posture on attributing a reporter's possible
  error to a named living person remains unresolved.
- **Action-discovery is a parallel effort.** This plan owns only the emit interface
  (`research_questions`, `source_targets`, `source_search_log`). If that effort's contract differs,
  these three tables are the negotiation surface.
