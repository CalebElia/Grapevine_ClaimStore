# Reviewing vocabulary proposals

Every controlled vocabulary is enforced by a trigger that **never rejects an insert** —
rejecting would throw away extraction work. On a term it does not recognise it stores the
vocabulary's *fallback* instead, logs a proposal, and increments `occurrences`.

So a proposal is the store saying: *something wrote a term I do not know, and I kept the row
anyway.* The row is now sitting on a value nobody chose.

**Unread proposals are silent wrong answers.** 81 organisations were typed `other` because
seventeen proposals sat unread; every query on `org_type` was quietly wrong and nothing looked
broken. There was no command for this until recently, which is most of why nobody looked.

```bash
./scripts/vocab.sh
```

Each entry shows the term, how many times it was seen, what was stored instead, which row first
wrote it, whether that row still exists, and the vocabulary's currently approved terms.

---

## The four rulings

| | when | effect |
|---|---|---|
| `--approve N --description '...'` | the term is a real category the vocabulary lacked | adds it, then re-points the rows that fell back |
| `--map N --as TERM` | the term is an existing category spelled differently | re-points the rows; **adds nothing** |
| `--stale N` | the rows it describes no longer exist | closes it; touches no data |
| `--reject N` | the term should never have been written | closes it; rows keep the fallback |

Add `--dry-run` to any of them. It performs the work and rolls back, so a constraint violation
surfaces before you commit rather than after.

### `--map` is the common one, and the one people reach for last

Sixteen of seventeen `org_type` proposals were **spellings**: `government-office` for
`government`, `company` for `business`, `university` for `academic`. Approving those would have
left the store with two dialects for one idea — the same fragmentation as keying the CAP with
`doc_type='cap'` when `plan` was already approved.

When you map, also fix the **boundary** so the foreign spelling stops arriving: the wiki's
`actor-type` values are translated in `registries/ann_arbor/org_types.json` before they ever
reach the column. A map without a boundary fix means the same proposal reappears next ingest.

### `--approve` grows the ontology, so it needs a definition

`--description` is required. A term with no definition is the next reviewer's problem, and the
description is what tells them whether their case belongs in it.

Approve when no existing term *means* the same thing. `generation_mix` was approved because
"54% of the fuel mix was coal" is not `adoption_rate` (uptake of a programme), not `emissions`
(what the mix causes, not the mix), and not `goal_target` (observed, not aimed at).

Name it for **what is measured**, not the topic it sits near. `energy` was proposed ten times
and would have been a bucket — consumption, savings, capacity and price are all "energy" and
are all already covered.

### Units can never be mapped

`--map` on a `*_unit` vocabulary is refused outright. Mapping rewrites the unit column and
leaves the number alone, so `48 weeks` silently becomes `48 days`. That exact bug has already
happened once here. If a unit is real, approve it; if values genuinely need converting, do that
in `pipeline/units.py` where the conversion is tested.

### `--stale` is not a cop-out

`vocabulary_proposals` is append-only: it records what was *once* proposed, not what is
*currently* wrong. 38 proposals were closed as stale because the corpus had been re-extracted
and they pointed at deleted ids. Reading them as a defect list is reading a changelog as a bug
tracker — check `row still exists` before ruling.

---

## Check your work

```bash
./scripts/status.sh
```

The **VOCABULARY DRIFT** section should list only proposals nobody has ruled on. Anything left
there is either a decision waiting or a term you meant to map at the boundary and did not.

```sql
-- rows still sitting on a fallback, per vocabulary column
SELECT 'orgs.org_type' AS col, org_type AS value, count(*) FROM orgs
 WHERE org_type = 'other' GROUP BY 1,2;
```
