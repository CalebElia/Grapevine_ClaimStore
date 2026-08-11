"""S4c — read the annotated review workbook back in.

THE CENTRAL DESIGN DECISION: turns.json IS NOT MODIFIED.

The obvious implementation edits turns.json in place. That is wrong, and the reason is
PLAN.md's invalidate-forward contract: S3's merge parameters (GAP_TOLERANCE_S,
INTERJECTION_MAX_S) will be tuned again, and when they are, S3 must be free to
regenerate turns.json. If corrections lived inside that file, re-running S3 would
either destroy two hours of human review or force us to never re-run it. Both are
unacceptable, and the second is how pipelines quietly ossify.

So the human layer is a SEPARATE, DURABLE file:

    turns.json          machine output of S3       — regenerated freely
    corrections.json    human review               — the durable asset, never regenerated
    turns.reviewed.json corrections applied to S3  — derived, disposable

RE-ANCHORING. Corrections are keyed by utterance_key, which embeds the turn's audio
boundaries (`vid:00148:3764000-3791000`). If S3 re-runs with different parameters those
keys change and a naive key lookup silently drops every correction. So each correction
also stores its own {start_ms, end_ms, text_sha1, cluster}, and re-anchoring falls back
to interval overlap (IoU >= 0.5) against the new turns. Anything that cannot be
re-anchored is REPORTED, never dropped — the same posture as the vocabulary trigger.

WHAT COUNTS AS CONFIRMATION. A blank cell is not agreement. Voiceprint enrollment
requires `y` in the Confirm column: a persistent biometric record must follow from an
affirmative human act, never from an absence of one. See `enroll_voiceprints`.

Usage:
    python -m pipeline.import_review lWvRVUMyLP4                    # parse + apply, no DB
    python -m pipeline.import_review lWvRVUMyLP4 --enroll           # + write voiceprints
    python -m pipeline.import_review lWvRVUMyLP4 --approve-link "Missy Stults=Melissa Stults"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).parent.parent
DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")

KNOWN_FLAGS = {"speaker", "unnamed", "asr", "boundary", "missing", "check"}

# The layout is OWNED BY THE EXPORTER and imported here, so the two cannot drift.
# Duplicating the list was how the reader and writer got out of step when a column was
# added. Positions are still asserted against the real header row on load — a
# reordered or hand-inserted column would otherwise silently shift every read.
from pipeline.export_review import TRANSCRIPT_COLS as _TCOLS  # noqa: E402

TRANSCRIPT_HEADER = [name for name, _ in _TCOLS]
_IDX = {name: i for i, name in enumerate(TRANSCRIPT_HEADER)}   # 0-based, for _cell
COL_TEXT = _IDX["Transcript text"]
COL_SPEAKER_FIX = _IDX["✎ Speaker correction"]
COL_TEXT_FIX = _IDX["✎ Text correction"]
COL_FLAG = _IDX["✎ Flag"]
COL_NOTE = _IDX["✎ Notes"]
COL_KEY = _IDX["utterance_key"]

SPEAKERS_HEADER = ["Cluster", "Name (auto)", "How identified", "Conf", "Minutes",
                   "Turns", "First heard", "Evidence", "✎ Confirm?",
                   "✎ Correct name", "✎ Notes"]


def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode()).hexdigest()[:12]


def norm_name(n: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", (n or "").lower())).strip()


def name_key(n: str) -> tuple[str, str] | None:
    """(first, last), dropping middle names and initials.

    The roster says 'Brooks A Curtis' and 'Sara M Nedrich'; the persons table, populated
    from the Legistar /persons endpoint, says 'Brooks Curtis' and 'Sara Nedrich'. Same
    people. Matching on the first/last pair bridges that without opening the door to
    surname-only matching, which is the dangerous one — 'Ken Garber' (a public commenter
    who is not in persons at all) would otherwise match 'Judah Garber' (who is).
    """
    parts = norm_name(n).split()
    if len(parts) < 2:
        return None
    return (parts[0], parts[-1])


# ───────────────────────── parsing ─────────────────────────

@dataclass
class TurnCorrection:
    key: str
    speaker: str | None = None
    text: str | None = None
    flag: str | None = None
    note: str | None = None
    # Everything needed to re-find this turn if the key changes. Anchor, not decoration.
    anchor: dict = field(default_factory=dict)
    row: int = 0


@dataclass
class ClusterDecision:
    cluster: str
    decision: str          # confirmed | rejected | unreviewed
    name: str | None       # final name after correction
    auto_name: str | None
    auto_method: str | None
    corrected: bool
    note: str | None = None


@dataclass
class ImportReport:
    turns: list[TurnCorrection] = field(default_factory=list)
    clusters: list[ClusterDecision] = field(default_factory=list)
    orphan_keys: list[dict] = field(default_factory=list)
    unknown_flags: list[dict] = field(default_factory=list)
    tampered: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "turn_corrections": len(self.turns),
            "speaker_overrides": sum(1 for t in self.turns if t.speaker),
            "text_corrections": sum(1 for t in self.turns if t.text),
            "flags": sum(1 for t in self.turns if t.flag),
            "clusters_confirmed": sum(1 for c in self.clusters if c.decision == "confirmed"),
            "clusters_rejected": sum(1 for c in self.clusters if c.decision == "rejected"),
            "clusters_unreviewed": sum(1 for c in self.clusters if c.decision == "unreviewed"),
            "clusters_renamed": sum(1 for c in self.clusters if c.corrected),
            "orphan_keys": len(self.orphan_keys),
            "unknown_flags": len(self.unknown_flags),
            "machine_cells_edited": len(self.tampered),
        }


def _cell(row, i):
    v = row[i].value if i < len(row) else None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _assert_header(ws, expected: list[str], sheet: str) -> None:
    got = [(c.value or "").strip() if isinstance(c.value, str) else c.value
           for c in ws[1][:len(expected)]]
    if got != expected:
        raise ValueError(
            f"{sheet} sheet header does not match what this importer expects.\n"
            f"  expected: {expected}\n  found:    {got}\n"
            f"Columns were reordered, inserted, or deleted. Reading by position now "
            f"would attribute your corrections to the wrong fields, so this is a hard "
            f"stop. Re-export with pipeline.export_review and re-apply the annotations, "
            f"or fix the header back.")


def parse_workbook(path: Path, turns: list[dict]) -> ImportReport:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    for need in ("Transcript", "Speakers"):
        if need not in wb.sheetnames:
            raise ValueError(f"{path.name} has no {need!r} sheet (found {wb.sheetnames})")
    _assert_header(wb["Transcript"], TRANSCRIPT_HEADER, "Transcript")
    _assert_header(wb["Speakers"], SPEAKERS_HEADER, "Speakers")

    by_key = {t["key"]: t for t in turns}
    rep = ImportReport()

    # ── Transcript sheet
    for n, row in enumerate(wb["Transcript"].iter_rows(min_row=2), start=2):
        key = _cell(row, COL_KEY)
        speaker, text, flag, note = (
            _cell(row, i) for i in (COL_SPEAKER_FIX, COL_TEXT_FIX, COL_FLAG, COL_NOTE))
        if not any((speaker, text, flag, note)):
            continue
        if not key:
            rep.orphan_keys.append({"row": n, "key": None,
                                    "reason": "utterance_key cell is empty",
                                    "speaker": speaker, "text": text,
                                    "flag": flag, "note": note})
            continue
        if flag and flag.lower() not in KNOWN_FLAGS:
            # Kept, not dropped. An unanticipated flag is a vocabulary proposal, and
            # discarding review work to enforce a closed list is exactly what Rule 1
            # says not to do.
            rep.unknown_flags.append({"row": n, "key": key, "flag": flag})

        base = by_key.get(key)
        # `anchor` must describe the UTTERANCE the correction is about, not its
        # container. An interjection row carries a '<turnkey>#int@<ms>' synthetic key;
        # anchoring it to the parent turn's interval makes it re-anchor onto the parent
        # on any re-export, which put "Mike Berkowitz" on Erin Donnelly's turn — the
        # exact misattribution the pipeline exists to prevent.
        anchor_src, is_int = base, False
        if base is None and "#int@" in key:
            parent_key, _, ms = key.partition("#int@")
            base = by_key.get(parent_key)
            if base is not None:
                is_int = True
                anchor_src = next(
                    (j for j in base["interjections"] if int(j["start"] * 1000) == int(ms)),
                    None)
        if base is None or anchor_src is None:
            rep.orphan_keys.append({"row": n, "key": key,
                                    "reason": "no turn in turns.json with this key"
                                              if base is None else
                                              "parent turn has no interjection at that offset",
                                    "speaker": speaker, "text": text,
                                    "flag": flag, "note": note})
            continue

        # Did they type into a machine column? Compare the sheet's text against S3's.
        sheet_text = _cell(row, COL_TEXT)
        if sheet_text and not is_int and sheet_text != (base["text"] or
                                                        "[no words assigned]"):
            rep.tampered.append({"row": n, "key": key, "column": "Transcript text",
                                 "in_sheet": sheet_text, "in_pipeline": base["text"]})

        rep.turns.append(TurnCorrection(
            key=key, speaker=speaker, text=text,
            flag=flag.lower() if flag else None, note=note, row=n,
            anchor={"cluster": anchor_src["speaker"],
                    "start_ms": int(anchor_src["start"] * 1000),
                    "end_ms": int(anchor_src["end"] * 1000),
                    "text_sha1": sha1(anchor_src["text"]),
                    "seq": base["seq"],
                    "is_interjection": is_int},
        ))

    # ── Speakers sheet
    for n, row in enumerate(wb["Speakers"].iter_rows(min_row=2), start=2):
        cluster = _cell(row, 0)
        if not cluster:
            continue
        auto_name = _cell(row, 1)
        how = _cell(row, 2)
        confirm = (_cell(row, 8) or "").lower()
        correct = _cell(row, 9)
        note = _cell(row, 10)
        auto = None if how == "UNRESOLVED" else auto_name

        if confirm == "n":
            decision, final = "rejected", None
        elif confirm == "y":
            decision, final = "confirmed", (correct or auto)
            if not final:
                rep.notes.append(
                    f"{cluster}: marked confirmed but has no name in either column — "
                    f"treated as unreviewed.")
                decision, final = "unreviewed", None
        else:
            # A name typed without `y` is a correction the reviewer did not certify.
            # Recorded, applied to the transcript, but NOT eligible for enrollment.
            decision, final = "unreviewed", (correct or auto)
            if correct:
                rep.notes.append(
                    f"{cluster}: renamed to {correct!r} but Confirm is blank — the name "
                    f"is applied to the transcript, but no voiceprint will be stored.")

        rep.clusters.append(ClusterDecision(
            cluster=cluster, decision=decision, name=final, auto_name=auto,
            auto_method=None, corrected=bool(correct and correct != auto), note=note))

    # A speaker fix repeated across many turns of one cluster almost always means the
    # reviewer did not notice the Speakers sheet. Worth saying out loud.
    per_cluster: dict[tuple[str, str], int] = {}
    for t in rep.turns:
        if t.speaker:
            per_cluster[(t.anchor["cluster"], t.speaker)] = \
                per_cluster.get((t.anchor["cluster"], t.speaker), 0) + 1
    for (cl, nm), cnt in per_cluster.items():
        if cnt >= 5:
            rep.notes.append(
                f"{cl} was renamed to {nm!r} on {cnt} separate turns. If that is the whole "
                f"cluster, one row on the Speakers sheet does the same job and enables "
                f"voiceprint enrollment.")
    return rep


# ───────────────────────── re-anchoring ─────────────────────────

def reanchor(corrections: list[dict], turns: list[dict],
             min_iou: float = 0.5) -> tuple[list[dict], list[dict]]:
    """Re-attach corrections to turns after S3 has been re-run with new parameters.

    Exact key match first. Failing that, the correction's stored interval is matched
    against the new turns by intersection-over-union. IoU rather than midpoint
    containment because merge-parameter changes mostly *lengthen* turns by absorbing
    neighbours — a midpoint test happily attaches a correction to a turn that swallowed
    three others, silently widening its scope.
    """
    by_key = {t["key"]: t for t in turns}
    matched, lost = [], []
    for c in corrections:
        if c["key"] in by_key:
            matched.append({**c, "reanchored": False, "key_now": c["key"]})
            continue
        a = c.get("anchor") or {}
        if "start_ms" not in a:
            lost.append({**c, "reason": "no anchor recorded"})
            continue
        s0, e0 = a["start_ms"], a["end_ms"]
        best, best_iou = None, 0.0
        for t in turns:
            s1, e1 = int(t["start"] * 1000), int(t["end"] * 1000)
            inter = max(0, min(e0, e1) - max(s0, s1))
            if not inter:
                continue
            iou = inter / (max(e0, e1) - min(s0, s1))
            if iou > best_iou:
                best, best_iou = t, iou
        if best and best_iou >= min_iou:
            matched.append({**c, "reanchored": True, "key_now": best["key"],
                            "reanchor_iou": round(best_iou, 3)})
        else:
            lost.append({**c, "reason": f"best interval overlap {best_iou:.2f} < {min_iou}"})
    return matched, lost


# ───────────────────────── applying ─────────────────────────

def apply(video_id: str, rep: ImportReport, turns: list[dict], speakers: dict,
          workbook: Path, out_dir: Path) -> dict:
    """Write corrections.json (durable) + speakers.reviewed.json + turns.reviewed.json.

    Reads speakers.json and turns.json; writes NEITHER. Both are S4/S3 output and are
    this stage's input.
    """
    cluster_names = {c.cluster: c for c in rep.clusters}

    # The human layer wins over the machine layer, and the evidence field says so.
    new_speakers = dict(speakers)
    for c in rep.clusters:
        if c.decision == "rejected":
            new_speakers.pop(c.cluster, None)
        elif c.name:
            prior = speakers.get(c.cluster, {})
            if c.decision == "confirmed":
                new_speakers[c.cluster] = {
                    "name": c.name, "method": "human",
                    "evidence": f"confirmed in review of {workbook.name}"
                                + (f" (was {c.auto_name!r} via {prior.get('method')})"
                                   if c.corrected and c.auto_name else ""),
                    "confidence": 1.0, "review_confirmed": True,
                }
            elif c.corrected:
                new_speakers[c.cluster] = {
                    **prior, "name": c.name,
                    "evidence": f"renamed in review of {workbook.name}, NOT confirmed",
                    "review_confirmed": False,
                }

    # turns.reviewed.json — derived. Per-turn speaker beats per-cluster, because a
    # per-turn override is how a reviewer says "this one turn is somebody else".
    per_turn = {t.key: t for t in rep.turns}
    reviewed = []
    for t in turns:
        out = dict(t)
        cd = cluster_names.get(t["speaker"])
        out["speaker_name"] = cd.name if cd else None
        tc = per_turn.get(t["key"])
        if tc:
            if tc.speaker:
                out["speaker_name"] = tc.speaker
                out["speaker_override"] = True
            if tc.text:
                out["text_original"] = t["text"]
                out["text"] = tc.text
                # WhisperX word timings were computed against the original string and
                # do not survive an edit. Saying so beats pretending they still align.
                out["word_alignment_stale"] = True
            if tc.flag:
                out["review_flag"] = tc.flag
            if tc.note:
                out["review_note"] = tc.note
        reviewed.append(out)

    corrections = {
        "video_id": video_id,
        "source_workbook": workbook.name,
        "source_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest()[:16],
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clusters": [asdict(c) for c in rep.clusters],
        "turns": [asdict(t) for t in rep.turns],
        "orphan_keys": rep.orphan_keys,
        "unknown_flags": rep.unknown_flags,
        "machine_cells_edited": rep.tampered,
        "notes": rep.notes,
        "summary": rep.summary(),
    }

    # speakers.reviewed.json, NOT speakers.json. Same argument as turns.json above, and
    # I got it wrong on the first pass: an import run overwrote S4's real output with
    # test data, destroying machine-derived identifications (Jon Mallek, chair_address
    # 0.70) that nothing in the workbook could reconstruct. S4's output is an input to
    # this stage; a stage must not overwrite its own input, or a bad import is
    # unrecoverable rather than merely wrong.
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "corrections.json").write_text(json.dumps(corrections, indent=2))
    (out_dir / "speakers.reviewed.json").write_text(json.dumps(new_speakers, indent=2))
    (out_dir / "turns.reviewed.json").write_text(
        json.dumps({"video_id": video_id, "turns": reviewed}, indent=2))
    return corrections


# ───────────────────────── person resolution ─────────────────────────

@dataclass
class PersonLink:
    name: str
    cluster: str
    person_id: int | None
    matched_name: str | None
    how: str            # exact | name_key | alias | proposed | unmatched | ambiguous
    is_public_figure: bool | None
    candidates: list = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.how in ("exact", "name_key", "alias")


def resolve_people(conn, names: dict[str, str]) -> list[PersonLink]:
    """{cluster: name} -> links. Deliberately refuses to guess.

    Surname-only matching is never confident, in either direction: it would link the
    public commenter 'Ken Garber' to the unrelated official 'Judah Garber', and a wrong
    link here does not produce a visible error — it produces a voiceprint filed under
    the wrong human being.
    """
    rows = conn.execute(
        "SELECT id, full_name, is_public_figure FROM persons").fetchall()
    aliases = conn.execute(
        "SELECT p.id, a.alias, p.full_name, p.is_public_figure "
        "FROM person_aliases a JOIN persons p ON p.id = a.person_id").fetchall()

    by_exact: dict[str, list] = {}
    by_key: dict[tuple, list] = {}
    by_last: dict[str, list] = {}
    for pid, full, pub in rows:
        by_exact.setdefault(norm_name(full), []).append((pid, full, pub))
        k = name_key(full)
        if k:
            by_key.setdefault(k, []).append((pid, full, pub))
            by_last.setdefault(k[1], []).append((pid, full, pub))
    by_alias = {norm_name(a): (pid, full, pub) for pid, a, full, pub in aliases}

    out = []
    for cluster, name in names.items():
        n = norm_name(name)
        hit = by_exact.get(n)
        if hit and len(hit) == 1:
            pid, full, pub = hit[0]
            out.append(PersonLink(name, cluster, pid, full, "exact", pub)); continue
        if n in by_alias:
            pid, full, pub = by_alias[n]
            out.append(PersonLink(name, cluster, pid, full, "alias", pub)); continue
        k = name_key(name)
        cand = by_key.get(k, []) if k else []
        if len(cand) == 1:
            pid, full, pub = cand[0]
            out.append(PersonLink(name, cluster, pid, full, "name_key", pub)); continue
        if len(cand) > 1:
            out.append(PersonLink(name, cluster, None, None, "ambiguous", None,
                                  [{"id": p, "name": f} for p, f, _ in cand]))
            continue
        # Surname + first INITIAL only: surfaced as a proposal a human must approve,
        # never auto-linked. 'Missy Stults' -> 'Melissa Stults' lands here; 'Ken Garber'
        # -> 'Judah Garber' does not land here at all, because k != j.
        weak = [(p, f, pub) for p, f, pub in by_last.get(k[1], [])
                if k and name_key(f) and name_key(f)[0][:1] == k[0][:1]] if k else []
        if weak:
            out.append(PersonLink(name, cluster, None, None, "proposed", None,
                                  [{"id": p, "name": f} for p, f, _ in weak]))
        else:
            out.append(PersonLink(name, cluster, None, None, "unmatched", None))
    return out


# ───────────────────────── voiceprint enrollment ─────────────────────────

def enroll_voiceprints(conn, video_id: str, corrections: dict, links: list[PersonLink],
                       confirmed_by: str, dry_run: bool = True,
                       embeddings: dict | None = None, model: str | None = None) -> dict:
    """Attach confirmed names to the pyannote centroids from S2.

    FOUR GATES, all of which must pass. Any one failing means no voiceprint:
      1. the reviewer typed `y` in Confirm       — an affirmative act, not a blank cell
      2. the name resolves CONFIDENTLY to a person row   — no guessed identities
      3. persons.is_public_figure is TRUE        — checked here AND by a DB trigger
      4. S2 actually produced a centroid for that cluster

    Gate 3 is deliberately redundant with `reject_private_voiceprint()`. The trigger is
    the thing that cannot be bypassed; this check exists so the refusal is legible in
    the report rather than arriving as a raised exception.
    """
    # Centroids come from S2 unless injected (tests, or re-enrolling from a cached run).
    if embeddings is None or model is None:
        diar = json.loads((REPO / "processing" / video_id / "diarization.json").read_text())
        embeddings = diar.get("speaker_embeddings") or {}
        model = diar["_provenance"]["diarization_model"]
    emb = embeddings

    by_cluster = {l.cluster: l for l in links}
    confirmed = {c["cluster"]: c for c in corrections["clusters"]
                 if c["decision"] == "confirmed" and c["name"]}

    enrolled, refused = [], []
    for cluster, c in confirmed.items():
        link = by_cluster.get(cluster)
        vec = emb.get(cluster)
        if link is None or not link.confident:
            refused.append({"cluster": cluster, "name": c["name"],
                            "reason": f"person not confidently resolved "
                                      f"({link.how if link else 'no link'})",
                            "candidates": link.candidates if link else []})
            continue
        if not link.is_public_figure:
            refused.append({"cluster": cluster, "name": c["name"],
                            "person_id": link.person_id,
                            "reason": "is_public_figure is FALSE — private individuals "
                                      "get name-based identification only"})
            continue
        if not vec:
            refused.append({"cluster": cluster, "name": c["name"],
                            "reason": "S2 produced no centroid for this cluster"})
            continue

        # A centroid is contributed by a CLUSTER WITHIN a recording, not by a recording —
        # one meeting supplies a dozen clusters to a dozen different people. Tokenising
        # at that granularity is what makes both checks below correct.
        token = f"{video_id}:{cluster}"

        # Was this exact cluster previously enrolled under a DIFFERENT person? That means
        # the attribution changed between reviews, and the earlier person's centroid now
        # contains a stranger's voice. Averaging cannot be undone, so this is reported
        # for re-enrollment rather than silently patched.
        clash = conn.execute(
            "SELECT p.full_name, v.person_id FROM person_voiceprints v "
            "JOIN persons p ON p.id = v.person_id "
            "WHERE v.embedding_model=%s AND %s = ANY(v.enrolled_from_media) "
            "AND v.person_id <> %s", (model, token, link.person_id)).fetchall()
        if clash:
            refused.append({"cluster": cluster, "name": c["name"],
                            "reason": f"cluster was previously enrolled as "
                                      f"{clash[0][0]!r}; that centroid is now polluted "
                                      f"and both people need re-enrolling from scratch",
                            "conflicts_with_person_id": clash[0][1]})
            continue

        if dry_run:
            enrolled.append({"cluster": cluster, "name": c["name"],
                             "person_id": link.person_id, "dim": len(vec),
                             "action": "would enroll"})
            continue

        prior = conn.execute(
            "SELECT id, embedding, sample_count, enrolled_from_media "
            "FROM person_voiceprints WHERE person_id=%s AND embedding_model=%s",
            (link.person_id, model)).fetchone()
        if prior is None:
            conn.execute(
                "INSERT INTO person_voiceprints (person_id, embedding, embedding_model,"
                " sample_count, enrolled_from, enrolled_from_media, last_confirmed_by)"
                " VALUES (%s,%s,%s,1,%s,%s,%s)",
                (link.person_id, str(vec), model, "human", [token], confirmed_by))
            action, n = "enrolled", 1
        elif token in (prior[3] or []):
            # THIS RECORDING ALREADY CONTRIBUTED. Re-running the importer — which will
            # happen every time a reviewer corrects one more row — must not fold the
            # same audio in a second time. Doing so inflates sample_count (a claim
            # about how many INDEPENDENT samples back this centroid) and drags the
            # centroid toward whichever meeting was processed most often. Averaging is
            # not reversible, so the only correct move is to leave the vector alone.
            conn.execute(
                "UPDATE person_voiceprints SET last_confirmed_by=%s, updated_at=now()"
                " WHERE id=%s", (confirmed_by, prior[0]))
            action, n = "already_enrolled", prior[2]
        else:
            # Incremental centroid: weighted mean of the stored centroid and the new
            # one, then re-normalised. This is what sample_count is for — the same
            # person across meetings should converge, not overwrite. Averaging is only
            # valid because the model is part of the uniqueness key, so we can never
            # average across embedding spaces.
            old = [float(x) for x in str(prior[1]).strip("[]").split(",")]
            n = prior[2] + 1
            mixed = [(o * prior[2] + v) / n for o, v in zip(old, vec)]
            mag = sum(x * x for x in mixed) ** 0.5 or 1.0
            mixed = [x / mag for x in mixed]
            media = sorted(set((prior[3] or []) + [token]))
            conn.execute(
                "UPDATE person_voiceprints SET embedding=%s, sample_count=%s,"
                " enrolled_from_media=%s, last_confirmed_by=%s, updated_at=now()"
                " WHERE id=%s",
                (str(mixed), n, media, confirmed_by, prior[0]))
            action = "updated"
        enrolled.append({"cluster": cluster, "name": c["name"],
                         "person_id": link.person_id, "action": action,
                         "sample_count": n})

    return {"model": model, "enrolled": enrolled, "refused": refused,
            "dry_run": dry_run}


def approve_link(conn, spoken: str, canonical: str) -> dict:
    """Record `spoken` as an alias of the person named `canonical`.

    This is the human gate on a `proposed` match. 'Missy Stults' is how she is addressed
    in the room; 'Melissa Stults' is what Legistar calls her. One alias row makes every
    future meeting resolve without asking again.
    """
    row = conn.execute("SELECT id, full_name FROM persons WHERE lower(full_name)=lower(%s)",
                       (canonical,)).fetchone()
    if row is None:
        raise ValueError(f"No person named {canonical!r}. Nothing was written.")
    conn.execute(
        "INSERT INTO person_aliases (person_id, alias, alias_type) VALUES (%s,%s,%s)",
        (row[0], spoken, "spoken_name"))
    return {"person_id": row[0], "canonical": row[1], "alias": spoken}


# ───────────────────────── main ─────────────────────────

def find_workbook(video_id: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    d = REPO / "processing" / video_id / "review"
    xs = sorted(d.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    xs = [p for p in xs if not p.name.startswith("~$")]
    if not xs:
        raise FileNotFoundError(f"No .xlsx in {d}. Run pipeline.export_review first.")
    return xs[0]


def run(video_id: str, workbook: str | None = None, enroll: bool = False,
        confirmed_by: str = "review", dsn: str = DSN, commit: bool = False) -> dict:
    d = REPO / "processing" / video_id
    turns = json.loads((d / "turns.json").read_text())["turns"]
    speakers = json.loads((d / "speakers.json").read_text())
    wb = find_workbook(video_id, workbook)

    print(f"[S4c] reading {wb.name}")
    rep = parse_workbook(wb, turns)
    corrections = apply(video_id, rep, turns, speakers, wb, d)

    s = rep.summary()
    print(f"[S4c] {s['turn_corrections']} turn corrections "
          f"({s['speaker_overrides']} speaker, {s['text_corrections']} text, "
          f"{s['flags']} flagged)")
    print(f"[S4c] clusters: {s['clusters_confirmed']} confirmed, "
          f"{s['clusters_renamed']} renamed, {s['clusters_rejected']} rejected, "
          f"{s['clusters_unreviewed']} unreviewed")
    for k, label in (("orphan_keys", "orphaned corrections"),
                     ("unknown_flags", "unrecognised flags"),
                     ("machine_cells_edited", "edits to machine-owned cells")):
        if s[k]:
            print(f"[S4c] WARNING {s[k]} {label} — see corrections.json")
    for n in rep.notes:
        print(f"[S4c] note: {n}")

    result = {"corrections": corrections, "summary": s, "workbook": str(wb)}
    if not enroll:
        print(f"[S4c] wrote corrections.json, speakers.reviewed.json, turns.reviewed.json")
        return result

    import psycopg
    names = {c["cluster"]: c["name"] for c in corrections["clusters"]
             if c["decision"] == "confirmed" and c["name"]}
    with psycopg.connect(dsn, autocommit=False) as conn:
        links = resolve_people(conn, names)
        vp = enroll_voiceprints(conn, video_id, corrections, links,
                                confirmed_by, dry_run=not commit)
        if commit:
            conn.commit()
        else:
            conn.rollback()
    (d / "voiceprints.json").write_text(json.dumps(
        {"links": [asdict(l) for l in links], **vp}, indent=2))
    print(f"[S4c] voiceprints: {len(vp['enrolled'])} "
          f"{'enrolled' if commit else 'eligible (dry run — pass --commit)'}, "
          f"{len(vp['refused'])} refused")
    for r in vp["refused"]:
        print(f"[S4c]   refused {r['cluster']} {r['name']!r}: {r['reason']}")
    for l in links:
        if l.how in ("proposed", "ambiguous"):
            print(f"[S4c]   {l.how} {l.name!r} -> "
                  f"{[c['name'] for c in l.candidates]} "
                  f"(approve with --approve-link \"{l.name}=<canonical>\")")
    result["voiceprints"] = vp
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="S4c import annotated review workbook")
    ap.add_argument("video_id")
    ap.add_argument("--workbook", help="defaults to the newest .xlsx in processing/<id>/review/")
    ap.add_argument("--enroll", action="store_true", help="resolve people and stage voiceprints")
    ap.add_argument("--commit", action="store_true", help="actually write to Postgres")
    ap.add_argument("--confirmed-by", default=os.environ.get("USER", "review"))
    ap.add_argument("--approve-link", metavar="SPOKEN=CANONICAL",
                    help="record a spoken name as an alias of a person, e.g. "
                         '"Missy Stults=Melissa Stults"')
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args()

    if args.approve_link:
        import psycopg
        spoken, _, canonical = args.approve_link.partition("=")
        with psycopg.connect(args.dsn, autocommit=False) as conn:
            out = approve_link(conn, spoken.strip(), canonical.strip())
            conn.commit()
        print(f"[S4c] alias {out['alias']!r} -> {out['canonical']!r} "
              f"(person {out['person_id']})")
        return 0

    run(args.video_id, args.workbook, args.enroll, args.confirmed_by,
        args.dsn, args.commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
