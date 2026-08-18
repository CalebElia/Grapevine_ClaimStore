"""FireCrawl backend — the independent fourth read for Tier 2 disagreement.

WHY IT IS HERE. Not because it is expected to win, but because cross-converter
disagreement is our only signal on the 46 pages of this corpus that have no verified
ground truth. A fourth independent read costs nothing (existing unused credits) and makes
every divergence more informative: two arms disagreeing is ambiguous, three agreeing
against one is a lead.

It is scoped as a CROSS-CHECK, not a candidate for the primary converter, for one
structural reason: it returns markdown without page anchors, so a char offset in its
output cannot be resolved to a page. Our citation spine requires that. It can tell us a
figure exists; it cannot tell a reviewer where to look for it.

These are public government PDFs, so sending them to a third party discloses nothing.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

BASE = "https://api.firecrawl.dev/v2"


def parse_pdf(pdf: Path) -> dict:
    import httpx
    from pipeline import config
    key = config.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    r = httpx.post(f"{BASE}/parse",
                   headers={"Authorization": f"Bearer {key}"},
                   files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
                   data={"options": json.dumps({"formats": ["markdown"]})},
                   timeout=600)
    r.raise_for_status()
    return r.json()


def to_markdown(payload: dict) -> str:
    d = payload.get("data", payload)
    for key in ("markdown", "content", "text"):
        if isinstance(d.get(key), str):
            return d[key]
    # Some responses nest per-document results; concatenate whatever markdown exists.
    docs = d.get("documents") or d.get("results") or []
    return "\n\n".join(x.get("markdown", "") for x in docs if isinstance(x, dict))


def main() -> int:
    ap = argparse.ArgumentParser(description="FireCrawl conversion (Tier 2 cross-check)")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw")
    a = ap.parse_args()
    t0 = time.time()
    payload = parse_pdf(Path(a.pdf))
    md = to_markdown(payload)
    Path(a.out).write_text(md)
    if a.raw:
        Path(a.raw).write_text(json.dumps(payload, indent=1)[:2_000_000])
    print(f"[firecrawl] {time.time()-t0:.0f}s  {len(md.split()):,} words")
    if not md:
        print(f"[firecrawl] EMPTY — response keys: {list(payload)[:8]}")
    print(f"[firecrawl] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
