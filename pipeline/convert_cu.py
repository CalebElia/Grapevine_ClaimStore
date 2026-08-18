"""Azure Content Understanding backend — the arm aimed at text trapped in images.

WHY THIS ARM EXISTS. Every other converter reads a PDF's text layer. This corpus's
numbers largely are not in one: the Year 2 report carries 47-120 images per page and only
19-157 extractable characters, and years 3/4/5 hold 20/28/33 chart-sized figures of which
1/0/1 were ever processed. CU's documented behaviour is that figure alt-text CONTAINS the
text detected inside the figure -- which is precisely the gap.

THE OPERATION IS ASYNC, AND THAT IS NOT OPTIONAL. Measured, not assumed: posting the real
14-page Year 2 PDF to the synchronous `:analyzeBinaryInline` returns

    InputPageCountExceeded: The input file has 14 pages, which exceeds the
    maximum allowed page count of 5.

Five pages. Every annual report in this corpus is 7-24, so sync is unusable for all of
them and `:analyzeBinary` + Operation-Location polling is the only viable path. The
`.env.example` note flagged this limit as unconfirmed; it is now confirmed and this module
does not offer sync as an option, because choosing it would fail on every real document.

FIGURE ANALYSIS IS OFF UNLESS ASKED. `enableFigureAnalysis=true` is what appends the
structured chart reading; `chartFormat=markdown` returns it as a table rather than
Chart.js JSON. Markdown is chosen deliberately: Chart.js output invites the model to emit
plausible styling fields alongside the data, and we want the numbers, not a rendering.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

POLL_SECONDS = 3.0
POLL_TIMEOUT = 900.0


def analyze(pdf: Path, *, figure_analysis: bool = True,
            chart_format: str = "markdown") -> dict:
    import httpx
    from pipeline import config

    ep = (config.get("CU_ENDPOINT") or "").rstrip("/")
    key = config.get("CU_API_KEY")
    ver = config.get("CU_API_VERSION")
    az = config.get("CU_ANALYZER_ID")
    if not all((ep, key, ver, az)):
        raise RuntimeError("CU_ENDPOINT/CU_API_KEY/CU_API_VERSION/CU_ANALYZER_ID must be set")

    hdr = {"Ocp-Apim-Subscription-Key": key}
    params = {"api-version": ver}
    if figure_analysis:
        params["enableFigureAnalysis"] = "true"
        params["chartFormat"] = chart_format

    r = httpx.post(f"{ep}/contentunderstanding/analyzers/{az}:analyzeBinary",
                   params=params,
                   headers={**hdr, "Content-Type": "application/octet-stream"},
                   content=pdf.read_bytes(), timeout=300)
    r.raise_for_status()
    op = r.headers.get("Operation-Location") or r.headers.get("operation-location")
    if not op:
        return r.json()

    t0 = time.time()
    while time.time() - t0 < POLL_TIMEOUT:
        time.sleep(POLL_SECONDS)
        p = httpx.get(op, headers=hdr, timeout=120)
        p.raise_for_status()
        d = p.json()
        status = (d.get("status") or "").lower()
        if status in ("succeeded", "failed", "canceled"):
            if status != "succeeded":
                raise RuntimeError(f"CU analysis {status}: {json.dumps(d)[:400]}")
            return d
    raise TimeoutError(f"CU analysis still running after {POLL_TIMEOUT}s")


def to_markdown(payload: dict) -> tuple[str, dict]:
    """Concatenate per-content markdown, and report what the response actually contained.

    The stats matter as much as the text: `figures` is the count CU detected, and it is
    the number to compare against the 47 images pdfplumber sees on the grant page. A high
    word count with zero figures would mean figure analysis silently did nothing.
    """
    res = payload.get("result", payload)
    contents = res.get("contents") or []
    parts, figures, tables = [], 0, 0
    for c in contents:
        md = c.get("markdown") or ""
        parts.append(md)
        figures += len(c.get("figures") or [])
        tables += len(c.get("tables") or [])
    text = "\n\n".join(p for p in parts if p)
    return text, {"contents": len(contents), "figures": figures, "tables": tables,
                  "words": len(text.split()), "warnings": res.get("warnings") or []}


def main() -> int:
    ap = argparse.ArgumentParser(description="Azure Content Understanding conversion")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-figures", action="store_true",
                    help="control arm: disable figure analysis to isolate its effect")
    ap.add_argument("--raw", help="also dump the full JSON response here")
    a = ap.parse_args()

    t0 = time.time()
    payload = analyze(Path(a.pdf), figure_analysis=not a.no_figures)
    text, stats = to_markdown(payload)
    Path(a.out).write_text(text)
    if a.raw:
        Path(a.raw).write_text(json.dumps(payload, indent=1))
    print(f"[cu] {time.time()-t0:.0f}s  figures={'off' if a.no_figures else 'ON'}  "
          f"{stats['words']:,} words  {stats['figures']} figure(s)  {stats['tables']} table(s)")
    if stats["warnings"]:
        print(f"[cu] warnings: {json.dumps(stats['warnings'])[:300]}")
    print(f"[cu] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
