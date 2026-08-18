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
