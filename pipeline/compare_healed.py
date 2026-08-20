"""Compare a pipeline conversion against a hand-healed reference, deviation by deviation.

WHAT THE REFERENCE IS AND IS NOT. The healed markdowns are the best human reading we have,
not ground truth. Two cautions carry through every number below:

  provenance differs   Years 1-2 were healed from Docling+Gemini output; Year 3 came from
                       pdfplumber+Claude (bronze_to_silver) and PLAN.md records that
                       years 3-5 "have not been verified". A deviation from Year 3's
                       reference is a disagreement between two machine readings, one of
                       which a human has never checked.
  the human erred too  Year 2's healed file reads "to supoprt utility-pole EV charging"
                       where the OCR reads "to support", and it silently omits the image
                       text CU picks up. Deviations are evidence to examine, not defects
                       to fix by definition.

MEMBERSHIP IS COUNTED, NOT ALIGNED. An earlier analysis used difflib and read four large
"unique" runs that were present in both files -- the engines simply emit different reading
orders, and a sequence diff reports that as insertion. Token multisets answer "is this
text present"; sequence position is a separate question and is asked separately.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

_MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s?(?:million|billion))?", re.I)
_NUM = re.compile(r"(?<![\w$])\d[\d,]*(?:\.\d+)?(?:%|MW|kW|MT)?(?![\w])", re.I)


def strip_markup(text: str) -> str:
    """Remove OUR scaffolding but keep CU-style comment payloads.

    <!-- PageFooter: ... --> carries real text; deleting every comment cost 111 words in
    an earlier comparison and made a labelling difference look like a content loss.
    """
    text = re.sub(r"<!--\s*Page(?:Footer|Header):(.*?)-->", r"\1", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)      # links -> their text
    return text


def tokens(text: str) -> Counter:
    return Counter(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’-]{1,}", text))


def compare(ours: str, healed: str, dictionary: set[str] | None = None) -> dict:
    o, h = strip_markup(ours), strip_markup(healed)
    to, th = tokens(o), tokens(h)

    missing = {w: c - to.get(w, 0) for w, c in th.items() if c > to.get(w, 0)}
    extra = {w: c - th.get(w, 0) for w, c in to.items() if c > th.get(w, 0)}

    def realness(d):
        tot = sum(d.values())
        if not tot or dictionary is None:
            return None
        return sum(c for w, c in d.items() if w in dictionary) / tot

    money_o, money_h = set(_MONEY.findall(o)), set(_MONEY.findall(h))
    nums_o = {re.sub(r"\s+", "", x) for x in _NUM.findall(o)}
    nums_h = {re.sub(r"\s+", "", x) for x in _NUM.findall(h)}

    return {
        "words_ours": len(o.split()), "words_healed": len(h.split()),
        "missing_occurrences": sum(missing.values()),
        "extra_occurrences": sum(extra.values()),
        "missing_top": sorted(missing.items(), key=lambda x: -x[1])[:20],
        "extra_top": sorted(extra.items(), key=lambda x: -x[1])[:20],
        "missing_realness": realness(missing), "extra_realness": realness(extra),
        "money_only_healed": sorted(money_h - money_o),
        "money_only_ours": sorted(money_o - money_h),
        "numbers_only_healed": sorted(nums_h - nums_o)[:25],
        "n_numbers_only_healed": len(nums_h - nums_o),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="compare a conversion to a healed reference")
    ap.add_argument("--ours", required=True)
    ap.add_argument("--healed", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    try:
        d = {w.strip().lower() for w in open("/usr/share/dict/words")}
    except OSError:
        d = None
    r = compare(Path(a.ours).read_text(), Path(a.healed).read_text(), d)
    print(f"[cmp] {a.label}: ours {r['words_ours']:,} words | healed {r['words_healed']:,}")
    print(f"[cmp]   token occurrences only in healed: {r['missing_occurrences']}"
          + (f" ({r['missing_realness']:.0%} real words)" if r["missing_realness"] else ""))
    print(f"[cmp]   token occurrences only in ours  : {r['extra_occurrences']}"
          + (f" ({r['extra_realness']:.0%} real words)" if r["extra_realness"] else ""))
    print(f"[cmp]   currency only in healed: {r['money_only_healed']}")
    print(f"[cmp]   currency only in ours  : {r['money_only_ours']}")
    print(f"[cmp]   numbers only in healed ({r['n_numbers_only_healed']}): "
          f"{r['numbers_only_healed'][:12]}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
