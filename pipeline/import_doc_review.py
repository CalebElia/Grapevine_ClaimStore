"""Read the annotated document review workbook back in.

THE CENTRAL RULE, SAME AS import_review.py: MACHINE OUTPUT IS NEVER MODIFIED. Corrections
land in a `.reviewed.json` beside the conversion. A converter re-run regenerates its own
output freely and cannot destroy a human's work, because the two never share a file.

The header is asserted before anything is read. Columns get reordered by well-meaning
spreadsheet editing, and reading by position after that silently files a correction under
the wrong field -- which is worse than failing, because it looks like it worked.

WHAT THE OUTPUT IS FOR. Three numbers come out of this, and the third is the one that
cannot be obtained any other way:

  confirmed / corrected     on flagged rows -- how good the conversion actually is
  disagreement yield        of rows the automation flagged, how many were real errors
  CONTROL error rate        of rows NOTHING flagged, how many were wrong anyway

The control rate is the false-negative estimate. Every other quality number in this project
is conditional on the automation having noticed, and this is the only measurement that
tests that assumption.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from pipeline.export_doc_review import COL, HEADER, VERDICTS


def _assert_header(ws) -> None:
    got = [(c.value or "").strip() if isinstance(c.value, str) else c.value
           for c in ws[1][:len(HEADER)]]
    if got != HEADER:
        raise ValueError(
            "Review sheet header does not match what this importer expects.\n"
            f"  expected: {HEADER}\n  found:    {got}\n"
            "Columns were reordered, inserted or deleted. Reading by position now would "
            "attribute corrections to the wrong fields, so this is a hard stop. Re-export "
            "with pipeline.export_doc_review and re-apply, or restore the header.")


def parse(path: Path) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    if "Review" not in wb.sheetnames:
        raise ValueError(f"no 'Review' sheet in {path.name}; found {wb.sheetnames}")
    ws = wb["Review"]
    _assert_header(ws)

    rows, unknown = [], []
    for n in range(2, ws.max_row + 1):
        key = ws.cell(n, COL["row_key"]).value
        if not key:
            continue
        val = lambda name: (ws.cell(n, COL[name]).value or "")
        verdict = str(val("✎ Correct?")).strip()
        if verdict and verdict not in VERDICTS:
            unknown.append((n, verdict))
        rows.append({
            "row_key": str(key), "section": str(val("Section")),
            "page": val("Page"), "check": str(val("What to check")),
            "version_a": str(val("Version A")), "version_b": str(val("Version B")),
            "verdict": verdict,
            "true_text": str(val("✎ True text")).strip(),
            "notes": str(val("✎ Notes")).strip(),
        })
    return {"rows": rows, "unknown_verdicts": unknown, "workbook": str(path)}


def report(d: dict) -> None:
    rows = d["rows"]
    ann = [r for r in rows if r["verdict"] or r["true_text"] or r["notes"]]
    print(f"\n{len(rows)} rows, {len(ann)} annotated ({len(ann)/max(len(rows),1):.0%})")
    if not ann:
        print("  nothing annotated yet — fill in the ✎ columns and re-run")
        return

    print(f"\n  {'section':<16}{'annotated':>10}{'wrong':>8}{'error rate':>12}")
    print("  " + "-" * 48)
    for sec in ("Disagreement", "Chart data", "Control"):
        s = [r for r in rows if r["section"] == sec and
             (r["verdict"] or r["true_text"] or r["notes"])]
        if not s:
            continue
        wrong = sum(1 for r in s if r["verdict"] in ("A is right", "B is right", "both wrong")
                    or r["true_text"])
        print(f"  {sec:<16}{len(s):>10}{wrong:>8}{wrong/len(s):>11.0%}")

    ctl = [r for r in rows if r["section"] == "Control" and r["verdict"]]
    if ctl:
        bad = sum(1 for r in ctl if r["verdict"] != "both fine" or r["true_text"])
        print(f"\n  FALSE-NEGATIVE ESTIMATE: {bad}/{len(ctl)} = {bad/len(ctl):.0%} of passages")
        print("  the automation flagged NOTHING on were wrong anyway.")
        print("  This is the only number here that tests whether the automation is trustworthy.")

    v = Counter(r["verdict"] for r in rows if r["verdict"])
    if v:
        print(f"\n  verdicts: {dict(v)}")
    if d["unknown_verdicts"]:
        print(f"\n  NOT A RECOGNISED VERDICT (kept verbatim, not silently dropped):")
        for n, txt in d["unknown_verdicts"][:8]:
            print(f"    row {n}: {txt!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="import the annotated document review workbook")
    ap.add_argument("--workbook", required=True)
    ap.add_argument("--out", help="defaults to <workbook>.reviewed.json")
    a = ap.parse_args()
    wb = Path(a.workbook)
    d = parse(wb)
    d["imported_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = Path(a.out) if a.out else wb.with_suffix(".reviewed.json")
    out.write_text(json.dumps(d, indent=1))
    report(d)
    print(f"\n  wrote {out}")
    print("  (machine conversions untouched — this file is the only record of your edits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
