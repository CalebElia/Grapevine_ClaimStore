# Working the mention queue

The queue holds claims where string matching found *something* and could not prove it. Your
job is to answer one question per candidate — **does this sentence refer to this programme?**
— and record the answer either way.

Nothing in the queue is in the store. Nothing you skip is lost. A claim you never look at
simply keeps no mention, which is the same state it was in before.

---

## Start here

Every command below runs from the repo root. It is one directory deeper than `Grapevine/`:

```bash
cd ~/Developer/Grapevine/Coding_Projects/grapevine-claim-store
```

The database has to be up (`./scripts/db.sh start`). If a command reports it cannot find the
queue, it will print the directory you are actually in — that is nearly always the whole
problem.

---

## Claim, subject, mention

A **claim** is one sentence one actor said at one moment. A **subject** is a canonical thing
in the world — an initiative, an org, a place — seeded by a person, never minted by a
pipeline.

Every claim already *has* a subject, and you are not changing it. Claim 608 is filed under
`Strategy 7: Community Engagement & Equity`, because that is the heading the City printed it
under. What the queue asks is whether the sentence additionally **names** a second subject:

| | claim 608 | strength |
|---|---|---|
| `claims.subject_id` = 9 | Strategy 7 — where the document filed it | the City's own organizing act |
| a mention of subject 177 | Move Toward a Circular Economy — what the sentence refers to | weaker: the sentence merely names it |

The display says `already ABOUT:` for the first and `would NAME` for the second. A `yes` here
adds the second and never touches the first.

**If the sentence carries money, the mention is not the whole fact.** `fiscal_references`
already holds the amount, the awarding org, and the purpose, and its `subject_id` is usually
NULL — that column is what records "this grant funded this Action". 70 of 75 rows are
currently unattributed. Fill it alongside the mention:

```sql
UPDATE fiscal_references SET subject_id = 177 WHERE claim_id = 608 AND subject_id IS NULL;
```

---

## What the queue is, and what it is not

Three tiers of evidence run before you see anything:

| tier | evidence | who decided | stored? |
|---|---|---|---|
| `literal_name` | the programme's full name occurs in the sentence | string matching alone | **yes** |
| `core_phrase_verified` | the name minus its leading verb occurs, or its words occur together in any order | string matching *located*, a model *decided* | **yes** |
| proximity | the name's words appear near each other, scattered | nobody yet | **no — this is the queue** |

So the queue is the residue: cases where the only evidence is that some words turned up in
the same sentence. That is genuinely not enough — it pairs `Support Aging in Place
Efficiently` with a sentence about feedback sessions — which is why it waits for you.

---

## The fast way: the review UI

```bash
./scripts/review.sh processing/cap-2020/ingest/proposals/circular-economy.json
```

It opens `http://127.0.0.1:8765/` in your browser. Run it again any time to restart with your
latest edits — it stops whatever holds the port first, then starts fresh. **Reload the tab
after it restarts**, and check the `build ` hash in the header matches:

```bash
python3 -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('pipeline/review_server.py').read_bytes()).hexdigest()[:7])"
```

Do **not** restart it with `pkill -f pipeline.review_server && python3 -m pipeline.review_server …`.
`pkill -f` matches full command lines, and that one contains the pattern, so pkill kills its
own shell before the `&&` runs. Nothing starts, the browser keeps showing the dead page, and
it looks like your edit had no effect. `review.sh` kills by port, which cannot self-match. One card per candidate, with:

- **where it came from** — document, fiscal year, page, section heading
- **show surrounding page** — the real document text either side of the claim, opened
  scrolled to the sentence itself. This is a substring of the same canonical text the claim
  was extracted from, not a summary, and it is only shown when the claim's stored offsets
  still slice back to its verbatim. If the document was re-rendered underneath the claim you
  get "no verifiable page context" rather than the wrong page.
- **already about** — the subject the claim already has, which your ruling does not change
- **the money**, if the claim has a `fiscal_references` row, with a checkbox to attribute it
- **a proposed verdict and its reason**, when you pass `--proposals`

Yes / No / Skip are buttons. The phrase box is prefilled and editable, and a Yes goes through
the same span guard as the CLI — a click cannot assert a span the sentence does not contain.
Localhost only, no authentication; do not bind it to a routable interface.

Borderline items sort first, so the calls that need a person are not buried under twenty
obvious ones.

---

## The slow way: the CLI

```bash
python3 -m pipeline.review_mentions --rank
```

`--rank` answers "where is my time best spent" first: it lists candidates by how often they
recur, because one ruling about a repeated name settles every instance of it. Today that is
21 claims for `Move Toward a Circular Economy` and 9 for `Offset Greenhouse Gas Emissions`
out of 69 total — two decisions cover 43% of the queue.

Then read the actual sentences:

```bash
python3 -m pipeline.review_mentions --subject "circular economy"
```

```bash
python3 -m pipeline.review_mentions --claim 587
```

With no flags it shows the first ten of everything; `--limit 50` shows more. Each entry has:

- **`claim_id`** — the claim, so you can look it up or cite it
- **`verbatim`** — the sentence, exactly as the document printed it
- **`candidates`** — the programmes whose words appeared, each with `subject_id` and `name`
- **a parenthetical refusal line** — candidates the model already looked at and rejected.
  Useful context, not a verdict: if the model refused `circular economy` here, it read the
  phrase as subject-area vocabulary rather than the programme. You can overrule it.

---

## The question to ask

**Is the sentence talking about that programme, or is it using words the programme's name
happens to contain?**

The distinction that matters, from real cases already decided:

| sentence | programme | answer | why |
|---|---|---|---|
| "Launched a City circular economy website." | Move Toward a Circular Economy | **yes** | the City did something under the programme's banner |
| "USDN Emergent Learning Fund ($20,000) to advance ... the circular economy" (claim 608) | Move Toward a Circular Economy | **yes** | took money to do the programme's work |
| "presentations to the community on the circular economy" (claim 587) | Move Toward a Circular Economy | **no** | discussing the concept, not the programme |
| "complete their greenhouse gas emissions inventories" | Offset Greenhouse Gas Emissions | **no** | a subject area, not this programme |
| "our Aging in Place Efficiently program" | Support Aging in Place Efficiently | **yes** | names it outright |

The test is **agency, not funding**: did the City *do something* under the programme's banner
— build, launch, map, contract, take a grant for it — or did the programme's words merely
appear while something else was described? A money test would be wrong: of 34 circular-economy
claims only two carry an amount, and the unmistakable programme work (the website, the GIS
map, the working group) carries none.

When you cannot tell from the sentence alone, **leave it**. An unanswered candidate costs
nothing; a wrong `yes` puts a false edge into every aggregate that joins on subject.

---

## Record a YES (CLI)

A mention needs a **span** — the offsets into the claim's own verbatim where the reference
sits. That is what makes the row provable rather than asserted. You supply the phrase; the
command finds the offsets and refuses if it cannot.

```bash
python3 -m pipeline.review_mentions --yes 587 177 "circular economy" --dry-run
```

Read what it prints — the claim, the subject's real name, and the exact slice it will store.
Drop `--dry-run` to write it:

```bash
python3 -m pipeline.review_mentions --yes 587 177 "circular economy"
```

The phrase must occur **literally, with matching case**, in that claim's verbatim. This is
the same guard every automated tier obeys: **a mention that cannot be sliced out of the
sentence is not a mention**, whoever asserts it. If the case is wrong the command tells you
what the document actually printed.

The row lands with `method='human'` and your username in `confirmed_by`, and the candidate is
removed from the queue file so a re-run does not re-offer it.

## Record a NO (CLI)

There is no "rejected" table, deliberately — a negative is not a fact about the world, and
storing one would invite it to be read as one. Drop the candidate instead:

```bash
python3 -m pipeline.review_mentions --drop 587 177
```

Omit the subject id to drop every candidate on that claim. The refusal is noted in the queue
file so the next automated run does not simply propose it again.

## Overrule a machine decision

To remove a mention the pipeline stored and you think is wrong:

```bash
./scripts/db.sh psql -c "DELETE FROM claim_subject_mentions WHERE claim_id=1234 AND subject_id=56"
```

Then `--drop 1234 56` so the re-run does not put it back.

---

## Check your work

```bash
./scripts/db.sh psql
```

```sql
-- every stored mention must slice back to its own matched_text; this must return zero rows
SELECT m.id, m.matched_text, substring(cl.verbatim from m.span_start + 1
                                       for m.span_end - m.span_start) AS sliced
  FROM claim_subject_mentions m JOIN claims cl ON cl.id = m.claim_id
 WHERE substring(cl.verbatim from m.span_start + 1
                 for m.span_end - m.span_start) <> m.matched_text;

-- what you have added, by method
SELECT method, detected_by, count(*) FROM claim_subject_mentions GROUP BY 1,2 ORDER BY 3 DESC;

-- the payoff: a programme traced from the 2020 plan into the reports
SELECT d.doc_type, d.covers_period_start, left(cl.verbatim, 90)
  FROM claim_subject_mentions m
  JOIN claims cl ON cl.id = m.claim_id
  JOIN document_sections s ON s.id = cl.document_section_id
  JOIN documents d ON d.id = s.document_id
 WHERE m.subject_id = (SELECT id FROM subjects WHERE name = 'Geothermal Districts')
 ORDER BY d.covers_period_start;
```

---

## When you are done

Re-run the detector to pick up anything your decisions imply, and to regenerate the queue:

```bash
python3 -m pipeline.detect_mentions --verify --queue processing/cap-2020/ingest/mention-queue.json
```

It is idempotent: existing mentions are left alone by `ON CONFLICT DO NOTHING`, and rows you
added by hand carry `method='human'`, which no automated pass will overwrite.

---

## What is genuinely worth your time

Sorted by what a decision buys:

1. **A repeated candidate** — run `--rank` and start at the top. `Move Toward a Circular
   Economy` appears 21 times; one ruling about what counts as naming that programme settles
   all of them, and is worth writing down as a note for the next document.
2. **A programme the CAP names as an Action** — resolving it completes an arc from plan to
   outcome, which is the whole point. Check the candidate against
   `processing/cap-2020/ingest/action-queue.json` first.
3. **Everything else** — a one-off pairing that resolves one claim. Fine to skip.
