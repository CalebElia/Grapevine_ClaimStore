# Text path bake-off — A2Zero Year 2

Five converters, one document, scored against a **fully hand-vetted** ground truth.

Year 2 was chosen because it is the corpus's hardest case and its only verified one: 14 pages,
938 embedded images, and a text layer of 234 extractable words. Every figure in it lives inside
an image. Its markdown was hand-corrected line by line, including the twelve-grant list on
page 13 that no automated pipeline had ever read correctly.

## Results

| arm | words | money R/P | grants | complete sentences | time |
|---|---|---|---|---|---|
| pdfplumber | 4% | 0% / 100% | 0/12 | — | <1s |
| Docling (Mar 2026) | 96% | 57% / 100% | 5/12 | 22% | — |
| Docling 2.120.3 | 92% | 100% / 100% | **12/12** | 37% | **1188s** |
| Azure CU | 98% | 100% / 100% | **12/12** | **15%** | 9s |
| FireCrawl | 98% | 100% / 100% | **12/12** | **54%** | 12s |

R = recall · P = precision. "Complete sentences" = of 105 ground-truth sentences (60–250 chars),
how many appear **intact** and could therefore be quoted as a `verbatim`.

## The grant table is solved

Three arms independently read all twelve amounts with correct funders. The March-era Docling
managed 5/12, which matches the recollection that no pipeline ever got it right — that was true
when it was last tried, and is no longer true now.

## Numeric recall is no longer the discriminator

Three arms are perfect on it. What separates them is whether a claim can quote a sentence
**intact**, because `verbatim` is NOT NULL and non-empty on every claim-bearing table.

**Azure CU truncates mid-phrase.** It has the best word coverage and perfect numeric scores, and
is still the weakest on the metric that decides this:

> ground truth: "Introduced residents to electrification and **energy efficiency while deepening
> their connection to their energy usage and production through the Solarize program**"
>
> Azure CU: "Introduced residents to electrification and"

Every number survives; the prose does not. A truncated `verbatim` either fails anchoring or —
worse — anchors cleanly to text that misrepresents the source. That is the v1 failure mode
(*confidently wrong, nothing errors*) reappearing in the conversion layer.

**FireCrawl leads on completeness (54%) but has no page anchors.** A char offset in its output
cannot resolve to a page, and the citation spine requires that. So neither arm is yet a clean
production choice, and that is the open question years 4–5 must settle.

## Docling's chart extraction did not fire

Zero charts, despite `do_chart_extraction=True`. Verified **not** a misconfiguration: the flag is
set, the model resolves to `GRANITE_VISION_V4`, `chart2csv` is on. Docling's chart support covers
**bar, pie and line** charts; Year 2's 938 images are photographs and stylised infographics, which
are not charts. The capability that justified retesting Docling did not apply to this document —
at a cost of 20 minutes against Azure CU's 9 seconds.

This does not settle the question for years 4–5, which hold 28 and 33 chart-sized figures that
may be conventional charts. It does mean the capability cannot be assumed.

## Measurement failures worth remembering

Three metrics in this harness were wrong before they were right, each in the same direction —
**the measurement was at fault, not the arm**:

1. **"Invented" percentages.** The scorer flagged the March Docling for reporting 10%, 40%, 60%,
   absent from ground truth. They are chart readings. The ground-truth pipeline's healer
   *deliberately deletes* chart content — its Rule 4 reads "Delete text/stats already captured
   inside `<figure_description>`", and the files confirm it (8 figure blocks before, 2 after).
   Ground truth therefore **excludes chart-derived content by construction**, and scoring it as
   hallucination would have penalised `enableFigureAnalysis` for working.
2. **A hallucination that was a line break.** `10,000\nTrees` counted as a different quantity
   from `10,000 Trees`. Internal whitespace is now collapsed before comparison.
3. **A formatting gap read as a fidelity gap.** Markdown links and em-dash spacing made CU look
   far worse than it was, until normalised — after which a *real* truncation defect was still
   there underneath.

The general lesson: **on a corpus this adversarial, distrust a metric before distrusting an arm.**

## Reproducing

```bash
python -m pipeline.convert_check                      # all arms live?
python -m pipeline.convert_cu        --pdf X --out Y  # async; sync caps at 5 pages
python -m pipeline.convert_firecrawl --pdf X --out Y
/opt/miniconda3/envs/grapevine-docling/bin/python -m pipeline.convert_docling --pdf X --out Y
python -m pipeline.convert_score --truth vetted.md --against "label=out.md" --grants
```

---

# Years 4 and 5 — the text-layer regime

Year 2 has no text layer. Years 3–5 do, and pdfplumber reads it at ~98% parity. That makes
these documents a different test: not *can an arm recover anything*, but **does an arm preserve
a text layer that is already good, and does it add the chart content nobody has ever mined?**

## The arms turn out to be complementary, not competing

**Chart recovery.** Year 5's hand-made `data_points` block records nine numeric facts read out
of one dashboard chart. Testing who finds them:

| known chart value | pdfplumber | Azure CU | FireCrawl |
|---|---|---|---|
| 6.5 MW · 11.88 · 711 · $3.8 million · $400,000 | – | **found** | – |
| 78 MW · 2,770 · 1,390 · 1,090 | – | – | – |

**Azure CU recovers 5 of 9; pdfplumber and FireCrawl recover 0 of 9.** CU detected 27 figures on
year 4 and 33 on year 5 — essentially every chart-sized figure in each — and its alt-text is
genuinely reading pictures ("City officials break ground on Fire Station 4, Michigan's first
net-zero fire station"). This is the capability nothing else in the stack has.

Caveat on scale: detecting 33 figures yielded only 6 genuinely new numeric values on year 5 and
2 on year 4. Most figures are photographs, not data graphics. The gap is real but smaller than
the raw figure count suggests.

Caveat on noise: 3 of 33 alt-texts contain garbled OCR of stylised logos. Roughly 9%, and it is
decorative text, not data — but it is text that will enter the corpus unless filtered.

**Verbatim fidelity.** Against pdfplumber as the text-layer reference:

| | Azure CU intact | FireCrawl intact |
|---|---|---|
| year 4 (174 sentences) | **14%** | 39% |
| year 5 (250 sentences) | **44%** | 51% |

CU's mid-phrase truncation is **systematic, not a year-2 artifact**. It loses prose on every
document tested, in the regime where the text layer is known-good.

## Docling's chart extraction did not fire on these either

Zero charts on year 4, despite 28 chart-sized figures — the same result as year 2, and again not
a misconfiguration. Two documents, two regimes, zero charts. The capability that justified
retesting Docling does not apply to this corpus. It ran in 70s on year 4 (versus 1188s on year 2,
where the missing text layer forced heavy VLM work), so the cost is regime-dependent, but the
benefit has not appeared in either regime.

## What this implies for the architecture

The result is **not** "pick a winner." It is that two arms do different, non-overlapping jobs:

- **Text layer → pdfplumber.** It *is* the text layer, it is free, it is instant, and it carries
  a native page map. On years 3–5 nothing beat it on fidelity because nothing can: the other arms
  are transcribing what pdfplumber reads directly.
- **Figure content → Azure CU.** The only arm that recovers numbers locked in images, in both
  regimes.
- **No text layer (year 2) → CU or FireCrawl.** FireCrawl for prose completeness, CU for figures.

Which lands exactly where the plan anticipated: figure-derived content has no char offset in the
primary text, so it cannot be spliced into it. It becomes a claim with a **different provenance
kind** — page and figure rather than character span — recorded as such. The bake-off did not
choose between arms; it established which layer each one is trustworthy for.

---

# Correction, and a hallucination risk in chart extraction

## The "zero charts" finding was partly my own bug

`chart_data` is a `TableData` object, not a list, and calling `len()` on it raises. The crash was
diagnostic: it can only fire *when a chart exists*. So "0 charts" on years 2 and 4 was real, while
**year 5 found a chart and took the counter down with it**. Fixed; year 5 re-run reports
**1 chart, 90 extracted cells**.

A second defect sat behind it: chart data does not appear in `export_to_markdown()`. Even a
successful extraction was invisible to every downstream consumer until the cells were explicitly
appended.

## Docling and Azure CU extract DIFFERENT charts

Not better or worse — different figures on the same page range:

| known chart value | Docling | Azure CU |
|---|---|---|
| 11.88 MW · 711 installations (solar) | – | **found** |
| GHG time series by fuel type, 2015-2020 | **found (90 cells)** | – |
| 78 MW · 2,770 · 1,390 · 1,090 | – | – |

Neither is a superset. Both miss the same four values.

## The extracted chart data shows signs of fabrication

Docling's 90 cells parse as a clean table — and that is the problem:

| Year | Electricity | Vehicle Fuel | Natural Gas | Waste | Propane | AvGas | Rail |
|---|---|---|---|---|---|---|---|
| 2015 | 1.33 | 0.6 | 0.6 | 0.2 | 0.1 | 0.1 | 0.1 |
| 2016 | 1.22 | 0.6 | 0.6 | 0.2 | 0.1 | 0.1 | 0.1 |
| 2020 | 0.69 | 0.6 | 0.6 | 0.2 | 0.1 | 0.1 | 0.1 |

**Only the Electricity column varies.** Six of seven categories are identical to one decimal
place across every year. Real emissions inventories do not behave that way. The header also
contains `A22ERC`, garbled from `A2ZERO`, which is direct evidence of OCR strain on the same
figure.

The structure is right, the values are plausible, and nothing errors — so these would land as six
years of well-formed `quantities` rows. This is the founding failure mode reproduced inside the
one capability that was supposed to close the infographic gap.

**Therefore: chart-derived numerics are `unverified` by default, never publishable without human
adjudication.** They are a lead, not a fact. The same rule already applies to unverified
transcript spans, and for the same reason.

---

# Human review of the Year 5 workbook — 52/52 rows

The reviewer completed all three sections. One bug was found in the import script's own
scoring before the numbers below could be trusted — a "both fine" row where the reviewer
had also filled ✎ True text with wording IDENTICAL to the machine's version was counted as
a correction. That inflated the control error rate from 8% to 17%. Fixed, with a regression
test pinned to this exact case.

## Corrected results

| section | rows | flagged wrong | rate |
|---|---|---|---|
| Disagreement | 20 | 20 | 100% |
| Chart data | 20 | 19 | 95% |
| **Control** | 12 | **1** | **8%** |

**8% false-negative rate.** Of passages the automation flagged nothing on, 1 in 12 was
still wrong. Better than feared, and it has a specific, nameable cause (below) rather than
being unexplained noise.

## The disagreement outcome breaks down as: pdfplumber wins or nobody does

`{'A is right': 11, 'both wrong': 9}` — B (whichever arm diverged from pdfplumber, mostly
Azure CU) **never won outright**. This matches the automated sentence-completeness numbers
exactly (CU 15-44% intact vs. pdfplumber's own text). But "both wrong" at 9/20 is new
information: pdfplumber's own text — the thing being used as the de facto reference for
years 4-5 — has real defects nearly half the time a disagreement was flagged.

## Independent human confirmation of the GHG chart fabrication

The reviewer, with no knowledge of our automated finding, wrote **"Don't know where this is
from"** on eight consecutive rows of Docling's extracted GHG table — the same table flagged
earlier for having six of seven fuel-type columns identical to one decimal place across six
years. Other notes on the same figure: *"mangled text from a bar chart,"* *"useless in this
format."* This upgrades that finding from statistical inference to human-verified.

By contrast, the three Azure CU chart values marked **unsure** (not wrong) are exactly the
three we had already independently confirmed as genuine, pulled from the same dashboard
screengrab: 11.88 (MW new solar), $400,000, $3.8 million. The reviewer's uncertainty was
honest — hard to verify a screengrab by eye — and it landed precisely on the real ones.

## Two pdfplumber defects, neither known before this review

**Page-number bleed.** Three rows start with a bare 1-2 digit number lifted from the
PREVIOUS page's footer: `"23 CLOSING A2ZERO is..."`, `"19 7: OTHER - AmeriCorps..."`,
`"6 1: 100% RENEWABLES - The City..."`. Checked for scale: **16 such prefixes** appear
across the document, not an isolated glitch. A cheap regex strip at page-join time removes
it; the fix belongs in `convert_document.py`, since pdfplumber is the production text-layer
backend.

**Column interleaving.** *"three Electronics Collection events this last year! At each
[Local Food Festival.] event, over 6,000 pounds..."* — a caption from an adjacent column
spliced mid-sentence, because pdfplumber reads left-to-right across the physical page
rather than column-by-column. This is the Control-section false negative: nothing is
missing and nothing disagrees between arms (every arm made the same reading-order error,
if they touched this region at all), so it is invisible to both Tier 1 and Tier 2 by
construction. Only a human, or a semantic-coherence pass of the kind discussed for a
possible adjudicator, would catch it.

## Net effect on the architecture decision

Nothing here overturns the earlier conclusion — pdfplumber remains the right text-layer
backend, Azure CU remains the only source of real figure content, chart-derived numerics
remain unverified-by-default. What it adds: two concrete, fixable pdfplumber defects, human
confirmation strong enough to stop hedging on the chart-fabrication finding, and a real
(not assumed) 8% baseline for what "nothing flagged" is worth.
