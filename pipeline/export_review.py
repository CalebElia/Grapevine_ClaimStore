"""S4b — export the diarized, named transcript as an ANNOTATABLE document.

The published HTML artifact is read-only. This produces two writable formats so a
human can review the transcript with the video playing, and — critically — so those
corrections can be read back in as data.

    .docx   Word/Google Docs/Pages. Comments and Track Changes. Best for reading in
            flow and marking prose problems ("this is two speakers", "wrong word").
    .xlsx   One row per turn with dedicated correction columns. Best for systematic
            speaker relabelling, because the correction lands in a known cell rather
            than in free text a parser has to interpret.

WHY THE utterance_key IS ON EVERY ROW AND EVERY PARAGRAPH

PLAN.md Part V: "S3+S4 jointly are the human-correctable diarized transcript. All
speaker relabels and transcript fixes land there once and amortize across every
re-extraction." That only works if a correction can be attached to the exact turn it
corrects, across format round-trips and across re-runs. `seq` alone is not enough —
it renumbers if the merge parameters change. The key
(`media_asset:sequence:start_ms-end_ms`) pins the turn to its audio boundaries, which
is what makes it survivable.

WHAT THE REVIEWER IS ACTUALLY LOOKING FOR, in descending value:

  1. UNRESOLVED clusters       11 of 21, ~27 minutes. Naming one is pure gain.
  2. LOW-CONFIDENCE labels     chair_address at 0.6-0.7 is the weakest evidence class
                               and the one that produced a wrong match during
                               development ("Council Member Malik" -> Mallika Kothari).
  3. TURN BOUNDARY errors      two people welded into one turn. Costly downstream:
                               a claim gets attributed to whoever owns the turn.
  4. ASR errors on named terms measured against registries/ann_arbor/asr_vocabulary.json

The document is ordered and styled to put 1 and 2 in front of the eye, rather than
presenting 227 turns as an undifferentiated wall.

Usage:
    python -m pipeline.export_review lWvRVUMyLP4 \
        --title "Sustainability Commission Meeting 3/10/2026" --date 2026-03-10
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent

# Evidence classes, weakest last. Drives the visual weight in both formats: the
# reviewer's attention is a budget and it should be spent on the weak end.
METHOD_LABEL = {
    "human": "human-confirmed",
    "self_id": "self-identified",
    "roll_call": "roll call",
    "chair_address": "addressed by chair",
}
NEEDS_EYES = {"chair_address"}          # 0.6-0.7 — always worth a check
UNRESOLVED = "—"

# CLUSTER AND NAME ARE SEPARATE COLUMNS, and this is not cosmetic.
#
# The first version showed one column holding "the name if resolved, else SPEAKER_NN".
# That hides the cluster for every speaker that HAS a name, which destroys the only
# signal that catches a bad identification: two turns that a reviewer knows are
# different people, sharing one cluster id. Found in review — SPEAKER_11 is a residual
# bin of 15 sub-second fragments (5.7s total, spread across 110 minutes) that S4 had
# labelled 'Sara M Nedrich'. The reviewer correctly assigned two different names to two
# of its turns, but had to work that out from the audio, because the sheet showed only
# 'Sara M Nedrich' in both rows. With the cluster visible it is a two-second read.
#
# Column order is defined ONCE here and every index is derived from it. The previous
# layout had positions hard-coded in six places across two modules, which is how a
# reordered column silently reassigns corrections to the wrong field.
TRANSCRIPT_COLS = [
    ("#",                    5),
    ("Time",                 9),
    ("Cluster",             12),
    ("Speaker (auto)",      20),
    ("How identified",      17),
    ("Conf",                 6),
    ("Transcript text",     74),
    ("✎ Speaker correction", 20),
    ("✎ Text correction",   40),
    ("✎ Flag",              13),
    ("✎ Notes",             34),
    ("utterance_key",       34),
]
COL = {name: i for i, (name, _) in enumerate(TRANSCRIPT_COLS, start=1)}
TRANSCRIPT_INPUT_COLS = (COL["✎ Speaker correction"], COL["✎ Text correction"],
                         COL["✎ Flag"], COL["✎ Notes"])


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def yt_url(video_id: str, seconds: float) -> str:
    # YouTube honours &t=NNNs on watch URLs and seeks on load.
    return f"https://www.youtube.com/watch?v={video_id}&t={int(seconds)}s"


def load(video_id: str) -> tuple[list[dict], dict]:
    d = REPO / "processing" / video_id
    turns = json.loads((d / "turns.json").read_text())["turns"]
    speakers = json.loads((d / "speakers.json").read_text())
    return turns, speakers


def speaker_stats(turns: list[dict], speakers: dict) -> list[dict]:
    """Per-cluster totals. Minutes is the right sort key, not turn count — a cluster
    with 200 one-word roll-call answers matters far less than one with 9 unbroken
    minutes of substantive speech."""
    secs: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    first: dict[str, float] = {}
    for t in turns:
        secs[t["speaker"]] += t["end"] - t["start"]
        count[t["speaker"]] += 1
        first.setdefault(t["speaker"], t["start"])
        for i in t["interjections"]:
            secs[i["speaker"]] += i["end"] - i["start"]
            count[i["speaker"]] += 1
            first.setdefault(i["speaker"], i["start"])
    rows = []
    for cluster in sorted(secs, key=lambda c: -secs[c]):
        info = speakers.get(cluster, {})
        rows.append({
            "cluster": cluster,
            "name": info.get("name", UNRESOLVED),
            "method": info.get("method", ""),
            "evidence": info.get("evidence", ""),
            "confidence": info.get("confidence"),
            "minutes": secs[cluster] / 60,
            "turns": count[cluster],
            "first_at": first[cluster],
            "resolved": bool(info.get("name")),
        })
    return rows


# ───────────────────────────── DOCX ─────────────────────────────

def build_docx(video_id: str, turns: list[dict], speakers: dict,
               stats: list[dict], meta: dict, out: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, RGBColor, Inches
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    GREY = RGBColor(0x6B, 0x6B, 0x6B)
    RED = RGBColor(0xB3, 0x1E, 0x1E)
    AMBER = RGBColor(0xA8, 0x5C, 0x00)
    BLUE = RGBColor(0x1A, 0x4F, 0x8A)

    def hyperlink(par, url: str, text: str, color=BLUE, bold=True, size=8.5):
        """python-docx has no hyperlink API — the relationship has to be created on
        the part and referenced by id from a raw w:hyperlink element."""
        r_id = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        for tag, val in (("w:color", f"{color}"), ("w:sz", str(int(size * 2)))):
            el = OxmlElement(tag)
            el.set(qn("w:val"), val)
            rPr.append(el)
        f = OxmlElement("w:rFonts"); f.set(qn("w:ascii"), "Arial"); f.set(qn("w:hAnsi"), "Arial")
        rPr.append(f)
        if bold:
            rPr.append(OxmlElement("w:b"))
        u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rPr.append(u)
        run.append(rPr)
        t = OxmlElement("w:t"); t.text = text; t.set(qn("xml:space"), "preserve")
        run.append(t)
        link.append(run)
        par._p.append(link)

    def shade(cell, hexcolor: str):
        el = OxmlElement("w:shd")
        el.set(qn("w:val"), "clear"); el.set(qn("w:fill"), hexcolor)
        cell._tc.get_or_add_tcPr().append(el)

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for m in ("top", "bottom"):
        setattr(sec, f"{m}_margin", Inches(0.7))
    sec.left_margin, sec.right_margin = Inches(0.8), Inches(0.8)

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(0)

    def para(text="", size=10.5, bold=False, italic=False, color=None,
             before=0, after=0, indent=0.0, align=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if indent:
            p.paragraph_format.left_indent = Inches(indent)
        if align:
            p.alignment = align
        if text:
            r = p.add_run(text)
            r.font.size, r.bold, r.italic = Pt(size), bold, italic
            if color:
                r.font.color.rgb = color
        return p

    # ── Title block
    para(meta["title"], size=17, bold=True, after=2)
    para(f"Diarized transcript for review · {meta['date']} · "
         f"{meta['duration_min']:.0f} minutes · {len(turns)} turns",
         size=10, color=GREY, after=6)
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(10)
    r = p.add_run("Video: "); r.font.size = Pt(9.5); r.font.color.rgb = GREY
    hyperlink(p, yt_url(video_id, 0), f"youtube.com/watch?v={video_id}", size=9.5)

    # ── How to use
    para("HOW TO ANNOTATE", size=9, bold=True, color=GREY, before=4, after=3)
    for line in [
        "Click any timestamp to open the video at that moment (opens a new tab).",
        "Select text and add a COMMENT (Word: ⌘⌥A / Google Docs: ⌘⌥M) for anything "
        "you want flagged — wrong speaker, wrong word, two people in one turn.",
        "Turn on TRACK CHANGES (Word: Review › Track Changes / Docs: Editing › Suggesting) to "
        "correct wording directly. Both comments and tracked edits are read back in.",
        "Every turn carries a #number. That number is the anchor a correction attaches to — "
        "leave it in place.",
    ]:
        para("•  " + line, size=9.5, color=GREY, after=2, indent=0.12)

    para("WHERE TO LOOK FIRST", size=9, bold=True, color=GREY, before=10, after=3)
    n_unres = sum(1 for s in stats if not s["resolved"])
    unres_min = sum(s["minutes"] for s in stats if not s["resolved"])
    weak = [s for s in stats if s["resolved"] and s["method"] in NEEDS_EYES]
    for line, col in [
        (f"{n_unres} of {len(stats)} voice clusters are UNNAMED ({unres_min:.0f} min). "
         f"They appear in red as SPEAKER_NN. Naming one is the highest-value edit in this document.",
         RED),
        (f"{len(weak)} speaker(s) were identified only by being ADDRESSED BY THE CHAIR "
         f"(confidence 0.6–0.7) — shown in amber. This is the evidence class that has "
         f"produced a wrong match before. Verify these.", AMBER),
        ("Turn boundaries: if one turn contains two voices, say so in a comment. A claim "
         "inherits the speaker of the turn it sits in, so this error propagates.", GREY),
    ]:
        para("•  " + line, size=9.5, color=col, after=3, indent=0.12)

    # ── Speaker key
    doc.add_page_break()
    para("SPEAKER KEY", size=13, bold=True, after=2)
    para("Sorted by speaking time. Correct a name here and it applies to every turn "
         "by that cluster — you do not need to fix it in the body.",
         size=9, italic=True, color=GREY, after=8)

    tbl = doc.add_table(rows=1, cols=6)
    tbl.style = "Table Grid"
    widths = [Inches(1.05), Inches(1.85), Inches(1.35), Inches(0.62), Inches(0.62), Inches(1.41)]
    hdr = ["Cluster", "Name", "How identified", "Conf.", "Minutes", "First heard"]
    for i, (c, w) in enumerate(zip(tbl.rows[0].cells, widths)):
        c.width = w
        shade(c, "E8E8E8")
        r = c.paragraphs[0].add_run(hdr[i]); r.bold = True; r.font.size = Pt(8.5)
    for s in stats:
        cells = tbl.add_row().cells
        resolved = s["resolved"]
        vals = [
            s["cluster"],
            s["name"] if resolved else "?  ← name this",
            METHOD_LABEL.get(s["method"], "") if resolved else "unresolved",
            f"{s['confidence']:.2f}" if s["confidence"] is not None else "",
            f"{s['minutes']:.1f}",
            hms(s["first_at"]),
        ]
        for i, (c, v) in enumerate(zip(cells, vals)):
            c.width = widths[i]
            r = c.paragraphs[0].add_run(v)
            r.font.size = Pt(8.5)
            if not resolved:
                r.font.color.rgb = RED
                if i == 1:
                    r.bold = True
            elif s["method"] in NEEDS_EYES:
                r.font.color.rgb = AMBER
        if not resolved:
            shade(cells[1], "FFF0F0")
        elif s["method"] in NEEDS_EYES:
            shade(cells[1], "FFF7E8")

    para("Evidence for each identification is recorded in speakers.json and reproduced "
         "in the accompanying spreadsheet.", size=8.5, italic=True, color=GREY, before=6)

    # ── Transcript
    doc.add_page_break()
    para("TRANSCRIPT", size=13, bold=True, after=2)
    para("Interjections are indented. They were spoken across another speaker's turn "
         "and are attributed separately.", size=9, italic=True, color=GREY, after=10)

    for t in turns:
        info = speakers.get(t["speaker"], {})
        name = info.get("name")
        method = info.get("method", "")
        color = RED if not name else (AMBER if method in NEEDS_EYES else GREY)

        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(9)
        head.paragraph_format.space_after = Pt(1)
        head.paragraph_format.keep_with_next = True
        hyperlink(head, yt_url(video_id, t["start"]), hms(t["start"]))
        r = head.add_run("   " + (name or t["speaker"]))
        r.bold, r.font.size = True, Pt(10)
        r.font.color.rgb = color
        # Cluster id is shown even when the speaker HAS a name — see TRANSCRIPT_COLS.
        tail = f"   ·  {t['speaker']}" if name else ""
        tail += f"   ·  #{t['seq']}"
        if name and method:
            tail += f"  ·  {METHOD_LABEL.get(method, method)}"
            if info.get("confidence") is not None:
                tail += f" {info['confidence']:.2f}"
        elif not name:
            tail += "  ·  UNIDENTIFIED — who is this?"
        r = head.add_run(tail)
        r.font.size, r.font.color.rgb = Pt(8), color

        body = doc.add_paragraph()
        body.paragraph_format.space_after = Pt(0)
        body.paragraph_format.left_indent = Inches(0.18)
        r = body.add_run(t["text"] or "[no words assigned to this turn]")
        r.font.size = Pt(10.5)
        if not t["text"]:
            r.italic, r.font.color.rgb = True, RED

        for j in t["interjections"]:
            jinfo = speakers.get(j["speaker"], {})
            jname = jinfo.get("name")
            ip = doc.add_paragraph()
            ip.paragraph_format.left_indent = Inches(0.55)
            ip.paragraph_format.space_before = Pt(2)
            ip.paragraph_format.space_after = Pt(0)
            r = ip.add_run("↳ ")
            r.font.size, r.font.color.rgb = Pt(8), GREY
            # Interjections need their own seek target, not the parent turn's. Roll-call
            # answers arrive as interjections, and they are the evidence behind every
            # `roll_call` identification — the reviewer has to be able to jump to them.
            hyperlink(ip, yt_url(video_id, j["start"]), hms(j["start"]), size=8)
            label = f"{jname}  ({j['speaker']})" if jname else j["speaker"]
            r = ip.add_run(f"  {label}:  ")
            r.font.size, r.bold = Pt(8), True
            r.font.color.rgb = RED if not jname else GREY
            r = ip.add_run(j["text"])
            r.font.size, r.italic = Pt(9.5), True
            r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    # ── Provenance
    doc.add_page_break()
    para("PROVENANCE", size=13, bold=True, after=6)
    for k, v in meta["provenance"].items():
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(f"{k}: "); r.bold, r.font.size = True, Pt(9)
        r = p.add_run(str(v)); r.font.size = Pt(9); r.font.color.rgb = GREY

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


# ───────────────────────────── XLSX ─────────────────────────────

def build_xlsx(video_id: str, turns: list[dict], speakers: dict,
               stats: list[dict], meta: dict, out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ARIAL = "Arial"
    HDR_FILL = PatternFill("solid", fgColor="2F4858")
    INPUT_FILL = PatternFill("solid", fgColor="FFF9D6")     # yellow = you edit this
    ALERT_FILL = PatternFill("solid", fgColor="FDE7E7")
    WARN_FILL = PatternFill("solid", fgColor="FFF3DF")
    THIN = Side(style="thin", color="D9D9D9")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    def header(ws, row, labels, widths):
        for i, (lab, w) in enumerate(zip(labels, widths), start=1):
            c = ws.cell(row=row, column=i, value=lab)
            c.font = Font(name=ARIAL, size=9, bold=True, color="FFFFFF")
            c.fill = HDR_FILL
            c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[row].height = 30

    wb = Workbook()

    # ── Sheet 1: Transcript
    ws = wb.active
    ws.title = "Transcript"
    header(ws, 1, [c for c, _ in TRANSCRIPT_COLS], [w for _, w in TRANSCRIPT_COLS])
    ws.freeze_panes = get_column_letter(COL["Transcript text"]) + "2"

    INPUT_COLS = TRANSCRIPT_INPUT_COLS
    WRAP = {COL["Transcript text"], COL["✎ Text correction"], COL["✎ Notes"]}
    row = 2
    for t in turns:
        info = speakers.get(t["speaker"], {})
        name = info.get("name")
        method = info.get("method", "")
        vals = [
            t["seq"], hms(t["start"]),
            t["speaker"],                    # cluster, ALWAYS shown
            name or UNRESOLVED,              # name, blank-marked when unresolved
            METHOD_LABEL.get(method, "") if name else "UNRESOLVED",
            info.get("confidence"),
            t["text"] or "[no words assigned]",
            None, None, None, None, t["key"],
        ]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(row=row, column=i, value=v)
            c.font = Font(name=ARIAL, size=9.5)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=(i in WRAP))
            if i in INPUT_COLS:
                c.fill = INPUT_FILL
            if i == COL["Conf"] and v is not None:
                c.number_format = "0.00"
        # The time cell is a live link into the video at that second.
        c = ws.cell(row=row, column=COL["Time"])
        c.hyperlink = yt_url(video_id, t["start"])
        c.font = Font(name=ARIAL, size=9.5, color="1A4F8A", underline="single")
        # Cluster is monospaced so a repeated id is recognisable by shape while scrolling.
        ws.cell(row=row, column=COL["Cluster"]).font = Font(name="Menlo", size=8.5,
                                                            color="555555")
        # Colour the NAME cell by how much it needs checking.
        sc = ws.cell(row=row, column=COL["Speaker (auto)"])
        if not name:
            sc.fill = ALERT_FILL
            sc.font = Font(name=ARIAL, size=9.5, bold=True, color="B31E1E")
        elif method in NEEDS_EYES:
            sc.fill = WARN_FILL
            sc.font = Font(name=ARIAL, size=9.5, color="A85C00")
        ws.cell(row=row, column=COL["utterance_key"]).font = Font(
            name="Menlo", size=7.5, color="999999")
        row += 1

        for j in t["interjections"]:
            jinfo = speakers.get(j["speaker"], {})
            jname = jinfo.get("name")
            ivals = [
                None, hms(j["start"]),
                j["speaker"], jname or UNRESOLVED,
                "interjection", jinfo.get("confidence"),
                "↳ " + j["text"], None, None, None, None,
                f"{t['key']}#int@{int(j['start']*1000)}",
            ]
            for i, v in enumerate(ivals, start=1):
                c = ws.cell(row=row, column=i, value=v)
                c.font = Font(name=ARIAL, size=8.5, italic=True, color="666666")
                c.border = BORDER
                c.alignment = Alignment(vertical="top", wrap_text=(i in WRAP))
                if i in INPUT_COLS:
                    c.fill = INPUT_FILL
            c = ws.cell(row=row, column=COL["Time"])
            c.hyperlink = yt_url(video_id, j["start"])
            c.font = Font(name=ARIAL, size=8.5, italic=True,
                          color="1A4F8A", underline="single")
            ws.cell(row=row, column=COL["Cluster"]).font = Font(
                name="Menlo", size=7.5, italic=True, color="777777")
            if not jname:
                ic = ws.cell(row=row, column=COL["Speaker (auto)"])
                ic.fill = ALERT_FILL
                ic.font = Font(name=ARIAL, size=8.5, bold=True, color="B31E1E")
            ws.cell(row=row, column=COL["utterance_key"]).font = Font(
                name="Menlo", size=7, color="BBBBBB")
            row += 1

    last_row = row - 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(TRANSCRIPT_COLS))}{last_row}"

    # ── Sheet 2: Speakers
    # `✎ Confirm?` is not redundant with `✎ Correct name`. A BLANK correction cell is
    # ambiguous between "the name is right" and "I never got to this row", and that
    # distinction is the gate for enrolling a voiceprint — a persistent biometric
    # record should require an affirmative human act, never an absence of one.
    sp = wb.create_sheet("Speakers")
    header(sp, 1, ["Cluster", "Name (auto)", "How identified", "Conf",
                   "Minutes", "Turns", "First heard", "Evidence",
                   "✎ Confirm?", "✎ Correct name", "✎ Notes"],
           [13, 22, 18, 6, 9, 7, 11, 46, 11, 22, 34])
    sp.freeze_panes = "A2"
    r = 2
    for s in stats:
        vals = [s["cluster"], s["name"], METHOD_LABEL.get(s["method"], "") if s["resolved"]
                else "UNRESOLVED", s["confidence"], round(s["minutes"], 1), s["turns"],
                hms(s["first_at"]), s["evidence"], None, None, None]
        for i, v in enumerate(vals, start=1):
            c = sp.cell(row=r, column=i, value=v)
            c.font = Font(name=ARIAL, size=9.5)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=(i in (8, 11)))
            if i in (9, 10, 11):
                c.fill = INPUT_FILL
            if i == 4 and v is not None:
                c.number_format = "0.00"
            if i == 5:
                c.number_format = "0.0"
        if not s["resolved"]:
            sp.cell(row=r, column=2).fill = ALERT_FILL
            sp.cell(row=r, column=2).font = Font(name=ARIAL, size=9.5, bold=True, color="B31E1E")
        elif s["method"] in NEEDS_EYES:
            sp.cell(row=r, column=2).fill = WARN_FILL
            sp.cell(row=r, column=2).font = Font(name=ARIAL, size=9.5, color="A85C00")
        r += 1
    sp_last = r - 1

    # Dropdowns. Two hours of review is long enough that free-typing 'y' and flag names
    # produces variants ('Y', 'yes', 'ASR'), and the importer would then have to guess.
    # Constraining at entry is cheaper than normalising at parse time — and `showErrorMessage
    # =False` keeps it a suggestion, so an unanticipated flag is still accepted rather
    # than blocked, which is the same posture as the vocabulary trigger.
    from openpyxl.worksheet.datavalidation import DataValidation
    _spk = get_column_letter(COL["✎ Speaker correction"])
    _txt = get_column_letter(COL["✎ Text correction"])
    _flg = _flag = get_column_letter(COL["✎ Flag"])
    dv_yn = DataValidation(type="list", formula1='"y,n"', allow_blank=True,
                           showErrorMessage=False)
    sp.add_data_validation(dv_yn)
    dv_yn.add(f"I2:I{sp_last}")
    dv_flag = DataValidation(
        type="list", formula1='"speaker,unnamed,asr,boundary,missing,check"',
        allow_blank=True, showErrorMessage=False)
    ws.add_data_validation(dv_flag)
    dv_flag.add(f"{_flag}2:{_flag}{last_row}")

    # ── Sheet 3: Legend + live review progress
    lg = wb.create_sheet("Legend", 0)
    lg.column_dimensions["A"].width = 26
    lg.column_dimensions["B"].width = 96

    def line(row, a, b, bold=False, size=10, color="222222"):
        ca = lg.cell(row=row, column=1, value=a)
        cb = lg.cell(row=row, column=2, value=b)
        ca.font = Font(name=ARIAL, size=size, bold=True, color=color)
        cb.font = Font(name=ARIAL, size=size, bold=bold, color=color)
        cb.alignment = Alignment(vertical="top", wrap_text=True)
        return row + 1

    r = 1
    c = lg.cell(row=r, column=1, value=meta["title"])
    c.font = Font(name=ARIAL, size=15, bold=True)
    r += 2
    r = line(r, "Video", f"{yt_url(video_id, 0)}")
    r = line(r, "Meeting date", meta["date"])
    r = line(r, "Duration", f"{meta['duration_min']:.0f} minutes")
    r = line(r, "Turns", f"{len(turns)} merged speaker turns from "
                         f"{meta['provenance'].get('diarization_segments', 'n/a')} "
                         f"diarization segments")
    r += 1

    r = line(r, "WHICH CELLS DO I EDIT?", "", size=11)
    r = line(r, "", "Only the YELLOW columns. Everything else is machine output and is "
                    "regenerated when the pipeline re-runs — edits there will be lost.", size=9.5)
    r = line(r, "Transcript sheet", "✎ Speaker correction · ✎ Text correction · "
                                    "✎ Flag · ✎ Notes", size=9.5)
    r = line(r, "Speakers sheet", "✎ Confirm? · ✎ Correct name · ✎ Notes", size=9.5)
    r = line(r, "Do not edit", "utterance_key — it is how a correction is matched back to "
                               "the turn. Deleting it orphans your note.", size=9.5)
    r += 1

    r = line(r, "THE CONFIRM COLUMN", "Speakers sheet, column I. This one carries real "
                                      "consequences — read this.", size=11)
    for a, b in [
        ("y", "This cluster's final name is correct — either the auto name, or the one you "
              "typed in ✎ Correct name. ONLY confirmed speakers get a stored voiceprint."),
        ("n", "The auto name is wrong and you do not know who this is. The name is discarded "
              "and the cluster goes back to unresolved."),
        ("(blank)", "Not reviewed. Treated as unknown, never as agreement — leaving a row "
                    "blank can never cause a voiceprint to be stored."),
    ]:
        r = line(r, a, b, size=9.5, color="444444")
    r = line(r, "", "A voiceprint is a persistent biometric record that lets this speaker be "
                    "recognised in future meetings. It is stored only for public figures on "
                    "the official roster, and only when you affirmatively confirm the name. "
                    "Public commenters are identified by name within a meeting and are never "
                    "voiceprinted.", size=9.5, color="B31E1E")
    r += 1

    # Laid out as field -> value down column B rather than as a wide row: this sheet's
    # columns are sized for prose (A=26, B=96), so an 8-column specimen row would render
    # lopsided and unreadable.
    r = line(r, "EXAMPLE", "What a filled-in correction looks like. Illustrative only — "
                           "this is not a row from the transcript.", size=11)
    for field, val, is_input in [
        ("#", "148", False),
        ("Time", "1:02:44", False),
        ("Speaker (auto)", "SPEAKER_14", False),
        ("Transcript text", "and the A20 goal for twenty thirty", False),
        ("✎ Speaker correction", "Missy Stults", True),
        ("✎ Text correction", "and the A2Zero goal for 2030", True),
        ("✎ Flag", "asr", True),
        ("✎ Notes", "also the wrong speaker — this is Stults, not the chair. "
                    "Verified against video at 1:02:44.", True),
    ]:
        ca = lg.cell(row=r, column=1, value="      " + field)
        ca.font = Font(name=ARIAL, size=8.5, bold=is_input, color="444444")
        cb = lg.cell(row=r, column=2, value=val)
        cb.font = Font(name=ARIAL, size=8.5)
        cb.border = BORDER
        cb.alignment = Alignment(vertical="top", wrap_text=True)
        if is_input:
            cb.fill = INPUT_FILL
        r += 1
    r += 1

    r = line(r, "FLAG VALUES", "Use these words in the Flag column so the corrections can be "
                               "counted by kind. Anything else is fine too — it will surface "
                               "as an unknown flag rather than be dropped.", size=11)
    for flag, desc in [
        ("speaker", "wrong person attributed to this turn"),
        ("unnamed", "this cluster needs a name (do it once on the Speakers sheet instead)"),
        ("asr", "words are wrong — misheard term, name, or number"),
        ("boundary", "this turn contains more than one person, or is cut mid-sentence"),
        ("missing", "something was said here that is not in the transcript"),
        ("check", "not sure — look at this again"),
    ]:
        r = line(r, flag, desc, size=9.5, color="444444")
    r += 1

    r = line(r, "COLOUR MEANING", "", size=11)
    for fill, lab, desc in [
        (ALERT_FILL, "red", "unresolved speaker — no name was derivable from the transcript"),
        (WARN_FILL, "amber", "identified only by the chair addressing them (confidence 0.6–0.7); "
                             "weakest evidence class, verify"),
        (INPUT_FILL, "yellow", "your input"),
    ]:
        ca = lg.cell(row=r, column=1, value=lab)
        ca.fill = fill
        ca.font = Font(name=ARIAL, size=9.5, bold=True)
        ca.border = BORDER
        cb = lg.cell(row=r, column=2, value=desc)
        cb.font = Font(name=ARIAL, size=9.5)
        r += 1
    r += 1

    # Live counters. Formulas, not baked numbers — the point is that they move as the
    # review is done, so progress is visible without re-running anything.
    r = line(r, "REVIEW PROGRESS", "Updates as you fill the sheet in.", size=11)
    prog_start = r
    # Every formula's column letter is derived from TRANSCRIPT_COLS, so inserting a
    # column re-points these automatically instead of silently counting the wrong one.
    _seq = get_column_letter(COL["#"])
    for lab, formula in [
        # `seq` is only present on turns — interjection rows leave it blank. So this
        # counts turns, not sheet rows, and is labelled accordingly.
        ("Turns in transcript", f"=COUNTA(Transcript!{_seq}2:{_seq}{last_row})"),
        ("Speaker corrections made",
         f"=COUNTA(Transcript!{_spk}2:{_spk}{last_row})"),
        ("Text corrections made", f"=COUNTA(Transcript!{_txt}2:{_txt}{last_row})"),
        ("Rows flagged", f"=COUNTA(Transcript!{_flg}2:{_flg}{last_row})"),
        ("  └ flagged 'asr'",
         f'=COUNTIF(Transcript!{_flg}2:{_flg}{last_row},"asr")'),
        ("  └ flagged 'speaker'",
         f'=COUNTIF(Transcript!{_flg}2:{_flg}{last_row},"speaker")'),
        ("  └ flagged 'boundary'",
         f'=COUNTIF(Transcript!{_flg}2:{_flg}{last_row},"boundary")'),
        # SUMPRODUCT of two conditions, not a subtraction of two independent counts:
        # correcting an already-named speaker would otherwise decrement this and could
        # drive it negative.
        ("Clusters still unnamed",
         f'=SUMPRODUCT((Speakers!C2:C{sp_last}="UNRESOLVED")*(Speakers!J2:J{sp_last}=""))'),
        ("Minutes of speech unnamed",
         f'=SUMIF(Speakers!C2:C{sp_last},"UNRESOLVED",Speakers!E2:E{sp_last})'),
        ("Clusters confirmed", f'=COUNTIF(Speakers!I2:I{sp_last},"y")'),
        ("  └ eligible for voiceprint",
         f'=SUMPRODUCT((Speakers!I2:I{sp_last}="y")*(Speakers!E2:E{sp_last}>0))'),
        ("Clusters left to review",
         f'=SUMPRODUCT((Speakers!I2:I{sp_last}<>"y")*(Speakers!I2:I{sp_last}<>"n"))'),
    ]:
        ca = lg.cell(row=r, column=1, value=lab)
        ca.font = Font(name=ARIAL, size=9.5, bold=not lab.startswith("  "))
        cb = lg.cell(row=r, column=2, value=formula)
        cb.font = Font(name=ARIAL, size=9.5)
        cb.number_format = "0.0" if "Minutes" in lab else "0"
        cb.alignment = Alignment(horizontal="left")
        r += 1
    lg.cell(row=prog_start - 1, column=1).font = Font(name=ARIAL, size=11, bold=True)
    r += 1

    r = line(r, "PROVENANCE", "", size=11)
    for k, v in meta["provenance"].items():
        r = line(r, k, str(v), size=9, color="666666")

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


# ───────────────────────────── main ─────────────────────────────

def refill(video_id: str, workbook: Path, corrections_path: Path | None = None) -> dict:
    """Write a saved corrections.json back into a freshly exported workbook.

    Needed whenever the workbook has to be regenerated under a reviewer: a column was
    added (as happened here), or S3 was re-run and every turn boundary moved. Without
    this, a format change costs the reviewer their whole pass, which in practice means
    the format never gets fixed.

    This is the read side of the same contract export/import already rely on — the
    corrections file is authoritative and the workbook is a disposable view of it.
    Corrections whose turn no longer exists are re-anchored by audio overlap; anything
    that still cannot be placed is REPORTED, never dropped.
    """
    from openpyxl import load_workbook
    from pipeline.import_review import reanchor

    d = REPO / "processing" / video_id
    corrections_path = corrections_path or d / "corrections.json"
    if not corrections_path.exists():
        return {"restored": 0, "lost": [], "note": "no corrections.json to restore"}
    saved = json.loads(corrections_path.read_text())
    turns = json.loads((d / "turns.json").read_text())["turns"]

    wb = load_workbook(workbook)
    ws, sp = wb["Transcript"], wb["Speakers"]
    row_of = {ws.cell(row=r, column=COL["utterance_key"]).value: r
              for r in range(2, ws.max_row + 1)}

    # Exact key against the SHEET first. reanchor() matches against turns.json, which
    # holds no interjection keys, so every interjection correction fell through to
    # interval matching and landed on its parent turn — putting a roll-call answer in
    # the clerk's mouth. Only genuinely missing keys need re-anchoring.
    direct = [c for c in saved.get("turns", []) if c["key"] in row_of]
    missing = [c for c in saved.get("turns", []) if c["key"] not in row_of]
    matched, lost = reanchor(missing, turns)
    matched = [{**c, "reanchored": False, "key_now": c["key"]} for c in direct] + matched

    n = 0
    for c in matched:
        r = row_of.get(c.get("key_now") or c["key"])
        if r is None:
            lost.append({**c, "reason": "re-anchored turn is not on the new sheet"})
            continue
        for field, col in (("speaker", "✎ Speaker correction"), ("text", "✎ Text correction"),
                           ("flag", "✎ Flag"), ("note", "✎ Notes")):
            if c.get(field):
                ws.cell(row=r, column=COL[col], value=c[field])
        n += 1

    # Speakers sheet: confirm / correct name / notes, matched on cluster id, which is
    # stable across re-exports in a way that row position is not.
    srow = {sp.cell(row=r, column=1).value: r for r in range(2, sp.max_row + 1)}
    decided = 0
    for cl in saved.get("clusters", []):
        r = srow.get(cl["cluster"])
        if r is None or cl["decision"] == "unreviewed" and not cl.get("corrected"):
            continue
        if cl["decision"] in ("confirmed", "rejected"):
            sp.cell(row=r, column=9, value="y" if cl["decision"] == "confirmed" else "n")
        if cl.get("corrected") and cl.get("name"):
            sp.cell(row=r, column=10, value=cl["name"])
        if cl.get("note"):
            sp.cell(row=r, column=11, value=cl["note"])
        decided += 1

    wb.save(workbook)
    return {"restored": n, "clusters": decided, "lost": lost,
            "reanchored": sum(1 for c in matched if c.get("reanchored"))}


def export(video_id: str, title: str, date: str, out_dir: Path | None = None,
           refill_existing: bool = True) -> dict:
    turns, speakers = load(video_id)
    stats = speaker_stats(turns, speakers)
    proc = REPO / "processing" / video_id
    out_dir = out_dir or proc / "review"

    tprov = json.loads((proc / "transcript.json").read_text())["_provenance"]
    dprov = json.loads((proc / "diarization.json").read_text())["_provenance"]
    meta = {
        "title": title,
        "date": date,
        "duration_min": max(t["end"] for t in turns) / 60,
        "provenance": {
            "video_id": video_id,
            "asr_model": tprov["asr_model"],
            "word_alignment": tprov["word_alignment"],
            "asr_vocabulary": tprov["vocabulary_jurisdiction"],
            "diarization_model": dprov["diarization_model"],
            "pyannote_version": dprov["pyannote_version"],
            "diarization_segments": dprov["n_segments_exclusive"],
            "voice_clusters": dprov["n_clusters"],
            "merged_turns": len(turns),
            "speakers_named": sum(1 for s in stats if s["resolved"]),
            "transcribed_at": tprov["transcribed_at"],
            "diarized_at": dprov["diarized_at"],
        },
    }

    slug = title.lower().replace("/", "-").replace(" ", "-")
    docx = build_docx(video_id, turns, speakers, stats, meta, out_dir / f"{slug}.docx")
    xlsx = build_xlsx(video_id, turns, speakers, stats, meta, out_dir / f"{slug}.xlsx")
    # Re-applied by DEFAULT. A regenerated workbook that silently drops a reviewer's
    # existing pass is the failure that makes people refuse to re-export.
    rf = refill(video_id, xlsx) if refill_existing else {}
    if rf.get("restored"):
        print(f"[S4b] restored {rf['restored']} turn corrections and "
              f"{rf['clusters']} cluster decisions from corrections.json"
              + (f" ({rf['reanchored']} re-anchored)" if rf.get("reanchored") else ""))
    for l in rf.get("lost", []):
        print(f"[S4b] WARNING could not restore correction on {l.get('key')}: {l['reason']}")
    print(f"[S4b] {docx}  ({docx.stat().st_size/1024:.0f} KB)")
    print(f"[S4b] {xlsx}  ({xlsx.stat().st_size/1024:.0f} KB)")
    return {"docx": docx, "xlsx": xlsx, "meta": meta}


def main() -> int:
    ap = argparse.ArgumentParser(description="S4b export annotatable transcript")
    ap.add_argument("video_id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--no-refill", action="store_true",
                    help="do not re-apply an existing corrections.json")
    args = ap.parse_args()
    export(args.video_id, args.title, args.date, refill_existing=not args.no_refill)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
