"""S4b-v2 — review workbook for the Scribe + Gemini pipeline.

WHAT CHANGED FROM v1 OF THIS EXPORT. The old workbook asked a reviewer to NAME speakers:
21 anonymous clusters, 11 of them unresolved, and the human supplied the identities. This
one asks them to CONFIRM: 91% of speech arrives with a name already attached.

That inverts what the columns are for, so the sheet is organised by CONFIDENCE rather
than by time. Every turn carries the tier its name came from:

  roster     matched a member of this body, title-constrained          — verified
  persons    in the Legistar record AND evidenced present at this meeting — verified
  proposed   a plausible match with no evidence of presence            — CONFIRM THIS
  raw        Gemini produced a name nobody could corroborate            — CONFIRM THIS
  none       no name at all                                             — NAME THIS

`proposed` and `raw` are where the errors live, and the tier is not cosmetic. Gemini
attributed two stretches of this meeting to 'Erica Briggs' and 'Lisa Disch' — real
Council members who were not present — and an earlier version of the resolver marked
both VERIFIED because the person table confirmed they exist. Existence is not presence.
The reviewer's attention belongs on the unverified tiers, and this sheet puts it there.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent

TIER_NOTE = {
    "roster": "member of this body — verified",
    "persons": "in the record and evidenced present — verified",
    "proposed": "plausible, NO evidence of presence — confirm",
    "raw": "name from audio only, uncorroborated — confirm",
    "none": "no name — please supply",
}
COLS = [
    ("#", 5), ("Time", 9), ("Cluster", 15), ("Speaker (proposed)", 22),
    ("Tier", 10), ("Purity", 7), ("Flags", 18), ("Transcript text", 66),
    ("✎ Confirm?", 11), ("✎ Correct name", 22), ("✎ Text correction", 34),
    ("✎ Flag", 12), ("✎ Notes", 30), ("utterance_key", 32),
]
COL = {n: i for i, (n, _) in enumerate(COLS, start=1)}
INPUTS = tuple(COL[c] for c in
               ("✎ Confirm?", "✎ Correct name", "✎ Text correction", "✎ Flag", "✎ Notes"))


def hms(s: float) -> str:
    s = int(s)
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"


def yt(vid: str, s: float) -> str:
    return f"https://www.youtube.com/watch?v={vid}&t={int(s)}s"


def build(video_id: str, title: str, date: str, out: Path | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    d = REPO / "processing" / video_id
    src = json.loads((d / "named.json").read_text())
    turns, prov = src["turns"], src.get("_provenance", {})
    out = out or d / "review" / f"{title.lower().replace('/', '-').replace(' ', '-')}-v2.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)

    A = "Arial"
    HDR = PatternFill("solid", fgColor="2F4858")
    IN_ = PatternFill("solid", fgColor="FFF9D6")
    OK_ = PatternFill("solid", fgColor="E7F6EC")
    WARN = PatternFill("solid", fgColor="FFF3DF")
    BAD = PatternFill("solid", fgColor="FDE7E7")
    THIN = Side(style="thin", color="D9D9D9")
    B = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    TIER_FILL = {"roster": OK_, "persons": OK_, "proposed": WARN, "raw": WARN,
                 "none": BAD}

    wb = Workbook()
    ws = wb.active
    ws.title = "Transcript"
    for i, (name, w) in enumerate(COLS, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=A, size=9, bold=True, color="FFFFFF")
        c.fill = HDR
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = get_column_letter(COL["Transcript text"]) + "2"

    WRAP = {COL["Transcript text"], COL["✎ Text correction"], COL["✎ Notes"],
            COL["Flags"]}
    for r, t in enumerate(turns, start=2):
        tier = t["name_tier"]
        vals = [t["seq"], hms(t["start"]), ",".join(t["clusters"])[:28],
                t["speaker_name"] or "", tier,
                (round(t["min_purity"], 2) if t.get("min_purity") is not None else None),
                " ".join(t["flags"]), t["text"],
                None, None, None, None, None, t["key"]]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = Font(name=A, size=9.5)
            c.border = B
            c.alignment = Alignment(vertical="top", wrap_text=(i in WRAP))
            if i in INPUTS:
                c.fill = IN_
        ws.cell(row=r, column=COL["Time"]).hyperlink = yt(video_id, t["start"])
        ws.cell(row=r, column=COL["Time"]).font = Font(name=A, size=9.5,
                                                       color="1A4F8A", underline="single")
        ws.cell(row=r, column=COL["Cluster"]).font = Font(name="Menlo", size=7.5,
                                                          color="666666")
        ws.cell(row=r, column=COL["utterance_key"]).font = Font(name="Menlo", size=7,
                                                                color="AAAAAA")
        sc = ws.cell(row=r, column=COL["Speaker (proposed)"])
        sc.fill = TIER_FILL.get(tier, BAD)
        if tier in ("roster", "persons"):
            sc.font = Font(name=A, size=9.5, color="166534")
        elif tier == "none":
            sc.font = Font(name=A, size=9.5, bold=True, color="B31E1E")
        else:
            sc.font = Font(name=A, size=9.5, color="A85C00")
        ws.cell(row=r, column=COL["Tier"]).fill = TIER_FILL.get(tier, BAD)
    last = len(turns) + 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{last}"

    dv = DataValidation(type="list", formula1='"y,n"', allow_blank=True,
                        showErrorMessage=False)
    ws.add_data_validation(dv)
    dv.add(f"{get_column_letter(COL['✎ Confirm?'])}2:"
           f"{get_column_letter(COL['✎ Confirm?'])}{last}")
    dvf = DataValidation(type="list",
                         formula1='"speaker,boundary,asr,missing,unnamed,check"',
                         allow_blank=True, showErrorMessage=False)
    ws.add_data_validation(dvf)
    dvf.add(f"{get_column_letter(COL['✎ Flag'])}2:"
            f"{get_column_letter(COL['✎ Flag'])}{last}")

    # ── Speakers: one row per proposed name, sorted by how much confirming it buys
    sp = wb.create_sheet("Speakers")
    agg = defaultdict(lambda: {"secs": 0.0, "turns": 0, "tier": "", "first": None,
                               "clusters": set()})
    for t in turns:
        k = t["speaker_name"] or "(unnamed)"
        a = agg[k]
        a["secs"] += t["end"] - t["start"]
        a["turns"] += 1
        a["tier"] = t["name_tier"]
        a["clusters"].update(t["clusters"])
        if a["first"] is None:
            a["first"] = t["start"]
    hdr = ["Speaker (proposed)", "Tier", "Minutes", "Turns", "Chunk-clusters",
           "First heard", "✎ Confirm?", "✎ Correct name", "✎ Notes"]
    for i, (h, w) in enumerate(zip(hdr, (24, 11, 9, 7, 15, 12, 11, 24, 34)), start=1):
        c = sp.cell(row=1, column=i, value=h)
        c.font = Font(name=A, size=9, bold=True, color="FFFFFF")
        c.fill = HDR
        sp.column_dimensions[get_column_letter(i)].width = w
    sp.freeze_panes = "A2"
    for r, (k, a) in enumerate(sorted(agg.items(), key=lambda kv: -kv[1]["secs"]), start=2):
        for i, v in enumerate([k, a["tier"], round(a["secs"] / 60, 1), a["turns"],
                               len(a["clusters"]), hms(a["first"]), None, None, None],
                              start=1):
            c = sp.cell(row=r, column=i, value=v)
            c.font = Font(name=A, size=9.5)
            c.border = B
            if i >= 7:
                c.fill = IN_
        sp.cell(row=r, column=1).fill = TIER_FILL.get(a["tier"], BAD)
        sp.cell(row=r, column=2).fill = TIER_FILL.get(a["tier"], BAD)
    sp_last = len(agg) + 1
    dv2 = DataValidation(type="list", formula1='"y,n"', allow_blank=True,
                         showErrorMessage=False)
    sp.add_data_validation(dv2)
    dv2.add(f"G2:G{sp_last}")

    # ── Legend
    lg = wb.create_sheet("Start here", 0)
    lg.column_dimensions["A"].width = 24
    lg.column_dimensions["B"].width = 96
    r = 1

    def line(a, b, size=10, bold=False, fill=None):
        nonlocal r
        ca, cb = lg.cell(row=r, column=1, value=a), lg.cell(row=r, column=2, value=b)
        ca.font = Font(name=A, size=size, bold=True)
        cb.font = Font(name=A, size=size, bold=bold)
        cb.alignment = Alignment(vertical="top", wrap_text=True)
        if fill:
            ca.fill = fill
        r += 1

    lg.cell(row=1, column=1, value=title).font = Font(name=A, size=15, bold=True)
    r = 3
    line("Video", yt(video_id, 0))
    line("Meeting", date)
    line("Pipeline", "ElevenLabs Scribe v2 (words + timestamps) -> Gemini (names and turn "
                     "boundaries) -> registry (canonical spelling)")
    r += 1
    line("WHAT'S DIFFERENT", "Last time you NAMED speakers. This time you CONFIRM them — "
                             "91% of speech already carries a name.", size=11)
    line("", "Work the Tier column, not the clock. Green is verified; amber needs your "
             "eye; red has no name at all.", size=9.5)
    r += 1
    line("TIER MEANINGS", "", size=11)
    for k, v in TIER_NOTE.items():
        line(k, v, size=9.5, fill=TIER_FILL.get(k))
    r += 1
    line("WHY AMBER MATTERS", "An earlier version of this pipeline attributed two "
         "stretches of this meeting to 'Erica Briggs' and 'Lisa Disch' — real Council "
         "members who were not here — and marked them VERIFIED, because a 1,771-row "
         "person table confirmed such people exist. Existence is not presence. Those now "
         "land in amber. If a name looks wrong, it may well be.", size=9.5)
    r += 1
    line("Purity", "How consistently one voice cluster mapped to one name. Under 0.60 "
                   "usually means the cluster is not a single person.", size=9.5)
    line("Cluster", "Scribe's label, namespaced per 8-minute chunk (c03_speaker_1). Two "
                    "chunks' labels are unrelated by design — the NAME is the identity.", size=9.5)
    r += 1
    line("EDIT ONLY", "the yellow columns. Everything else regenerates.", size=11)
    line("Do not edit", "utterance_key — it is how a correction finds its turn.", size=9.5)

    stats = defaultdict(float)
    for t in turns:
        stats[t["name_tier"]] += t["end"] - t["start"]
    tot = sum(stats.values()) or 1
    r += 1
    line("THIS MEETING", "", size=11)
    line("Turns", f"{len(turns)}")
    line("Distinct people", f"{len([k for k in agg if k != '(unnamed)'])}")
    for k in ("roster", "persons", "proposed", "raw", "none"):
        if stats.get(k):
            line(k, f"{stats[k]/60:.1f} min — {stats[k]/tot*100:.0f}% of speech",
                 size=9.5, fill=TIER_FILL.get(k))

    wb.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="S4b-v2 review workbook")
    ap.add_argument("video_id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    p = build(a.video_id, a.title, a.date)
    print(f"[S4b-v2] {p} ({p.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
