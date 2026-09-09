"""The figure readings a hand-prepared markdown carries inline -> the shape ingest expects.

WHY NOT figures.json. Both exist for cap-2020 and they are different readings of the same
pages. Measured:

                        figures.json      inline in the markdown
    figures                      66                          88
    pages covered                38                          49
    distinct $ figures           11                          49
    data items      61 <point .../>          410 prose bullets

The inline blocks are the richer read AND the one the document text belongs to -- the claims
being ingested come from that file, so its figure readings are the ones that match. Ingesting
figures.json instead would store 66 readings the reader never sees, miss twelve pages
entirely, and leave the store's figures disagreeing with its prose.

WHAT THIS DOES NOT DO. It does not turn the 410 bullets into figure_data_points rows. Those
require label and value_text NOT NULL, and a bullet is a sentence -- "The estimated cost over
10 years, including staffing, hard, and soft costs, is $820,000." is neither a label nor a
value. Splitting it is a semantic judgement, not a parse, and it is a separate pass. Nothing
is lost in the meantime: document_figures.raw_xml holds the whole block, so every bullet is
stored and searchable, just not yet decomposed.

WHAT IT REFUSES TO INVENT. These blocks carry no geometry, no classifier confidence, no
deployment id and no timestamp. pictures.json has 196 pictures over 82 pages against 88
figures over 49, and only FOUR pages have an unambiguous one-to-one match, so a bbox would be
a guess about which of two to four pictures a block describes. It is left empty, which is
already this pipeline's convention for unknown, rather than fabricated.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_PAGE = re.compile(r"^<!-- p\.(\d+) -->$")
_OPEN = "<figure_description>"
_CLOSE = "</figure_description>"
_TYPE = re.compile(r"<type>(.*?)</type>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)

# The wiki records no deployment for these readings, and naming ours would attribute them to
# a model that did not produce them. extracted_by is provenance, so it says what is known.
SOURCE = "a2zero-wiki (deployment unrecorded)"
PROMPT_VERSION = "wiki-inline-v1"


def parse_inline_figures(md: str, extracted_at: str,
                         source: str = SOURCE) -> list[dict]:
    """One record per <figure_description>, in the shape ingest_document --figures reads.

    The page comes from the running `<!-- p.N -->` marker, which is the same rule
    canonical.py applies to text, so a figure and the prose around it agree about where they
    are. A block before any marker gets page 0 rather than a guessed 1; ingest requires
    page_no NOT NULL and 0 is visibly not a page.
    """
    out: list[dict] = []
    page, buf, depth = 0, [], 0
    for line in md.splitlines():
        m = _PAGE.match(line.strip())
        if m and depth == 0:
            page = int(m.group(1))
            continue
        if _OPEN in line:
            depth, buf = depth + 1, []
        if depth:
            buf.append(line)
            if _CLOSE in line:
                depth -= 1
                if depth == 0:
                    xml = "\n".join(buf)
                    t = _TYPE.search(xml)
                    out.append({
                        "page_no": page,
                        # The wiki's own <type>, not a classifier label -- there was no
                        # classifier. Recorded as-is so the two are never confused.
                        "top_label": (t.group(1).strip() if t else "unknown"),
                        "top_conf": None,
                        "bbox": [],
                        "crop_path": None,
                        "crop_dpi": None,
                        "xml": xml,
                        "deployment": source,
                        "prompt_version": PROMPT_VERSION,
                        "extracted_at": extracted_at,
                        "title": (m2.group(1).strip()
                                  if (m2 := _TITLE.search(xml)) else None),
                    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", required=True, help="the prepared markdown (read only)")
    ap.add_argument("--out", required=True, help="figures.json-shaped output")
    ap.add_argument("--extracted-at",
                    help="ISO date the readings were produced. Defaults to the mtime of "
                         "--source-file, or of --md, because a made-up 'now' would claim a "
                         "vision call happened at a moment when none did.")
    ap.add_argument("--source-file", help="the wiki file these readings came from")
    a = ap.parse_args()

    stamp = a.extracted_at
    if not stamp:
        ref = Path(a.source_file or a.md)
        stamp = datetime.fromtimestamp(ref.stat().st_mtime,
                                       timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    figs = parse_inline_figures(Path(a.md).read_text(), stamp)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(figs, indent=1))

    pages = sorted({f["page_no"] for f in figs})
    bullets = sum(len(re.findall(r"^\s*-\s+\S", f["xml"], re.M)) for f in figs)
    print(f"[figures] {len(figs)} figure(s) over {len(pages)} page(s) -> {a.out}")
    print(f"[figures]   {bullets} data bullet(s) preserved in raw_xml "
          f"(0 decomposed into figure_data_points -- that is the semantic pass)")
    print(f"[figures]   extracted_by={SOURCE!r}  extracted_at={stamp}")
    if any(f["page_no"] == 0 for f in figs):
        print(f"[figures]   WARNING: {sum(1 for f in figs if f['page_no'] == 0)} figure(s) "
              f"before the first page marker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
