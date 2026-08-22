# Review guide — the A2ZERO annual report corpus

**State as of this writing:** 903 claims from 48 of 59 sections across five annual reports
(FY2021–FY2025). Every claim round-trips against its span, no duplicates, no hash drift,
every claim carries a date.

What follows is what a person still has to decide. The machine has done what string matching
and corroboration can do; everything below needs judgement.

Run queries with `./scripts/db.sh psql`. Each item says **what to look at**, **how**, and
**what a good answer looks like** — the last part matters most, because several of these
have no obviously correct answer and the point is to record the decision, not to find one.

---

## 1. `quantity_unit` is being used as free text — 160 pending proposals

**The most consequential data-quality problem in the store.** `unit` should hold the unit of
measurement; `measure` should hold what is being measured. The extraction is putting the
*counted noun* in `unit`:

| stored `unit` | should probably be |
|---|---|
| `Direct Current Fast Chargers (DCFCs)` | measure=`units_deployed`, unit=`chargers` |
| `community organization, collaborators, and partners` | measure=`participants`, unit=`organizations` |
| `pilot battery back-up Solarize bulk buys` | measure=`units_deployed`, unit=`programs` |

Real units *are* present — `percent` (30), `MW` (21), `years` (20), `trees` (11) — so the
field works when the text offers a unit and degrades to a noun phrase when it does not.

```sql
SELECT unit, count(*) n FROM quantities WHERE unit IS NOT NULL
GROUP BY unit ORDER BY n DESC LIMIT 40;

SELECT vocabulary, proposed_term, occurrences FROM vocabulary_proposals
WHERE status='pending' AND vocabulary='quantity_unit'
ORDER BY occurrences DESC LIMIT 30;
```

**A good answer decides one of three things**, and any of them is defensible:
- tighten the extraction prompt so `unit` may only be a measurement unit and the noun goes
  to `measure`; then re-extract (`--replace`) and watch the proposal queue shrink;
- accept `unit` as free text, drop its vocabulary and its trigger, and stop pretending it is
  controlled; or
- approve a real unit vocabulary (`percent, MW, kW, metric_tons, years, people, count, …`)
  and let everything else fall to the fallback.

**Do not leave it as it is.** 218 pending proposals means the proposal queue has stopped
being a signal — it is now the normal case, which is exactly how a guard becomes furniture.

---

## 2. No claim has a subject — 903 of 903 `subject_id IS NULL`

The store can tell you what was said and when, and cannot group it by *what it is about*.
Only two subjects exist (`A2ZERO`, `Bryant Neighborhood Decarbonization`).

```sql
SELECT count(*) FILTER (WHERE subject_id IS NULL) AS no_subject, count(*) FROM claims;
SELECT id, name, parent_subject_id FROM subjects;
```

This is the gap that blocks the analysis the corpus exists for — "how has Ann Arbor's solar
programme progressed across five years" has no join without it. The seven A2ZERO strategies
are the obvious first subjects, and `subjects.parent_subject_id` already exists to hang them
under `A2ZERO`. The wiki carries 7 strategies and 229 initiatives as a seed.

**A good answer:** decide whether subjects come from the wiki registry, from the section
headings (each strategy section is one subject), or from a curation pass — and whether a
claim gets its subject from its section or from its own content. Those give different
answers for a claim about solar inside the "Resilience" section.

---

## 3. Forty-nine claims name more than one organisation

Deliberately left unattached. `resolve_orgs` attaches an org only when exactly one is named,
because choosing between two is not string matching's job.

```sql
-- the ones a human has to split or attach
SELECT c.id, left(c.verbatim, 110) FROM claims c
WHERE c.org_id IS NULL AND c.verbatim ILIKE '%Ann Arbor Housing Commission%'
ORDER BY c.id LIMIT 20;
```
Run `python -m pipeline.resolve_orgs --dry-run` to list all 49 with the orgs each names.

**A good answer:** you previously ruled that a claim naming two actors should attach both via
`coalition_members`. That is still unwired. Confirm the rule and it can be automated — with
the caveat that "In collaboration with CAN, the City won $500,000" names a partner and a
recipient, which are different roles, and `coalition_members` flattens them.

---

## 4. Twenty-one fiscal references have `direction = 'unknown'`

`v_money_by_direction` is the only safe way to total money. **Never `SUM(amount_low)` across
the whole table** — it reads $1.11bn, of which $1.0bn is a savings claim.

```sql
SELECT * FROM v_money_by_direction ORDER BY total_low DESC NULLS LAST;

SELECT f.amount_low, left(c.verbatim, 100) FROM fiscal_references f
JOIN claims c ON c.id = f.claim_id
WHERE f.direction = 'unknown' ORDER BY f.amount_low DESC NULLS LAST;
```

The classifier abstains by design. Most of the 21 are grant-list entries with no verb —
`SEMCOG Carbon Reduction Program ($980,000) for LED streetlight conversions`.

**A good answer:** either add the pattern (a named programme plus an amount plus a purpose is
money received) or set them by hand. Either way, `unknown` should become rare, because an
`unknown` is excluded from every total and therefore silently understates.

---

## 5. The $1,000,000,000 claim — is it in scope?

> the City has saved rate payers more than $1,000,000,000 through testimony and advocacy

Correctly classified `saved`, so it no longer contaminates funding totals. But it is a
different *kind* of fact from a grant: it is a modelled, contested, jurisdiction-wide figure
won at the Michigan Public Service Commission, and `CLAUDE.md` flags it as exactly the sort
of number the store must be able to hold.

```sql
SELECT c.id, c.modality, c.asserted_start, left(c.verbatim, 200)
FROM claims c JOIN fiscal_references f ON f.claim_id = c.id
WHERE f.amount_low = 1000000000;
```

**A good answer:** decide whether ratepayer savings belong in `fiscal_references` at all, or
whether they are a `quantity` with `measure='savings'`. The current row is not wrong, but it
will surprise anyone who queries fiscal references expecting money that moved.

---

## 6. `direction` has no recipient, and one row proves it matters

> OSI supported TheRide in their successful grant application for **$25 MILLION**

That is `received` — **by TheRide**, not by the City. `fiscal_references` has
`awarding_org_id` but no `recipient_org_id`, so "how much did Ann Arbor receive" currently
includes money that went to a transit agency.

```sql
SELECT f.amount_low, coalesce(f.funder_name_text,'—'), left(c.verbatim, 100)
FROM fiscal_references f JOIN claims c ON c.id = f.claim_id
WHERE f.direction = 'received' ORDER BY f.amount_low DESC;
```

**A good answer:** confirm whether a recipient column is wanted before the corpus grows. It
is cheap now and expensive after the next thousand claims.

---

## 7. 124 claims are `hypothetical` — check that is what you want

They come from the "Priorities" and "Next Steps" sections, which are forward-looking by
design, plus some in-strategy statements of intent.

```sql
SELECT s.heading, count(*) FROM claims c
JOIN document_sections s ON s.id = c.document_section_id
WHERE c.modality='hypothetical' GROUP BY 1 ORDER BY 2 DESC;

SELECT left(verbatim, 120) FROM claims WHERE modality='hypothetical' LIMIT 15;
```

**A good answer:** these are the most valuable claims in the corpus for accountability work —
a stated Year-5 priority that never appears as a Year-6 accomplishment is exactly the
"commitment_unclosed" research question the schema anticipates. Confirm the modality is
right, then decide whether closing the loop across years is worth building.

---

## 8. Only two `asserted_events` exist across five reports

The same fact restated in consecutive reports is not yet resolved to one event. Five annual
reports describing the same solar programme currently look like five unrelated claims.

```sql
SELECT e.id, e.canonical_description, count(a.*) attestations,
       count(a.*) FILTER (WHERE a.derives_from_attestation_id IS NULL) independent
FROM asserted_events e LEFT JOIN event_attestations a ON a.asserted_event_id = e.id
GROUP BY e.id, e.canonical_description;

-- candidates: the same figure appearing in more than one document
SELECT q.value_low, q.unit, count(DISTINCT c.document_id) docs
FROM quantities q JOIN claims c ON c.id = q.claim_id
GROUP BY 1,2 HAVING count(DISTINCT c.document_id) > 1 ORDER BY docs DESC LIMIT 20;
```

**A good answer:** remember the rule — corroboration is independence, not count. Five reports
by the same office restating one fact is **one** source, and `derives_from_attestation_id`
is what keeps `v_timeline` honest about that.

---

## 9. Eleven tier C sections were never extracted

Tier C means "nothing to extract" — contents pages, section dividers, photo captions.

```sql
SELECT id, document_id, sequence, extraction_tier, char_end-char_start AS chars,
       coalesce(heading,'(front matter)')
FROM document_sections WHERE extraction_tier='C' ORDER BY document_id, sequence;
```

**A good answer:** spot-check two or three against the markdown and confirm nothing
claim-bearing was tiered out. The tiering is by length and numeric density, so a short
section carrying one important sentence is the failure mode.

---

## 10. Fidelity spot-check — the one thing no automated check replaces

Every claim round-trips against its span, which proves the text is *what the conversion
produced*. It does not prove the conversion matches the **page**.

```sql
-- ten at random, with their page
SELECT c.id, s.page_start, left(c.verbatim, 130)
FROM claims c JOIN document_sections s ON s.id = c.document_section_id
ORDER BY random() LIMIT 10;
```
Open the PDF at that page and read the sentence.

**A good answer:** ten claims, ten pages, no surprises. If one is wrong, the class of error
matters far more than the instance — every defect found this way so far turned out to be
systematic (a missing space, a mangled superscript, a corrupted date) rather than a one-off.

---

## 11. Open research questions

```sql
SELECT id, origin, priority, status, left(question, 120)
FROM research_questions WHERE status='open' ORDER BY priority, id;
```

Six are `dark_matter_gap` — money the document says was received without naming a source.
One is yours: whether Bryant's $1.3M includes the $500,000 MI-HOPE award or sits on top of
it. **Dark matter is a lead queue, never a finding** — each needs a human before it drives
any acquisition.

---

## What is already settled, so you do not re-check it

- **Provenance.** All five documents are bound to their source PDF by sha256.
  `python -m pipeline.record_provenance --verify` re-hashes and exits non-zero on drift.
- **Conversion conflicts.** Zero remain. Every cross-arm disagreement was adjudicated by
  glyph geometry, by a second independent read, or by a person.
- **Dates.** 903 of 903 claims carry one. Years 1 and 2 are human estimates and say so in
  `documents.covers_period_source`; Year 5 covers June–May, not July–June, which is real
  and published — **year-over-year comparisons are not on a common axis.**
- **Footnotes.** Seven, each linked to the section that cites it and to the City officer it
  names, all resolved to identities that predate this corpus.
