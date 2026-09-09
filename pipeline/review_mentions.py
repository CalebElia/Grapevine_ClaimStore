"""Read the mention queue, and record a human ruling on one entry.

WHY THIS IS A COMMAND AND NOT A SNIPPET. The queue instructions first shipped as a block of
python3 -c "..." to paste into a shell, and the quoting broke on first contact: nested
double quotes inside an f-string inside a double-quoted -c argument, which bash rewrites
before python ever sees it. A reviewer's tool has to survive being retyped.

WHAT A RULING IS. `--yes` writes one row into claim_subject_mentions with method='human',
which no automated pass overwrites. There is deliberately no `--no`: a negative is not a fact
about the world, and a table of them would invite being read as one. Refusing is dropping the
entry from the queue file, which `--drop` does.

THE SPAN IS STILL THE EVIDENCE. --yes refuses unless the phrase occurs literally in the
claim's own verbatim, exactly as every automated tier is refused. A mention that cannot be
sliced out of the sentence is not a mention, whoever asserts it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")
QUEUE = Path("processing/cap-2020/ingest/mention-queue.json")


def load(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"[review] no queue at {path}\n"
            f"        You are in {Path.cwd()}\n"
            f"        Run this from the repo root:\n"
            f"          cd ~/Desktop/Grapevine/Coding_Projects/grapevine-claim-store")
    return json.loads(path.read_text())


def about(claim_ids: list[int], dsn: str) -> dict[int, str]:
    """What each claim is already ABOUT, which is not what you are ruling on.

    Reviewers read `-> subject 177` as the claim's subject. It is not: the claim already has
    one, from its section heading, and stays that way. Showing both side by side is the only
    way the distinction survives contact with someone working the queue quickly.
    """
    if not claim_ids:
        return {}
    try:
        import psycopg
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            cur.execute("""SELECT cl.id, s.name FROM claims cl
                             JOIN subjects s ON s.id = cl.subject_id
                            WHERE cl.id = ANY(%s)""", (claim_ids,))
            return dict(cur.fetchall())
    except Exception:
        return {}   # the queue is readable without a server; this is decoration


def show(queue: list[dict], limit: int, subject: str | None, claim: int | None,
         dsn: str = DSN) -> None:
    rows = queue
    if subject:
        rows = [x for x in rows
                if any(subject.lower() in c["name"].lower() for c in x["candidates"])]
    if claim:
        rows = [x for x in rows if x["claim_id"] == claim]
    pairs = sum(len(x["candidates"]) for x in rows)
    print(f"[review] {len(rows)} claim(s), {pairs} candidate pair(s)"
          + (f"  (filtered from {len(queue)})" if rows is not queue else ""))
    shown = rows[:limit]
    ctx = about([x["claim_id"] for x in shown], dsn)
    for x in shown:
        print(f"\n  claim {x['claim_id']}")
        print(f"    {x['verbatim'][:170]}")
        if (a := ctx.get(x["claim_id"])):
            print(f"    already ABOUT: {a}   (unchanged by anything you do here)")
        for c in x["candidates"]:
            print(f"      would NAME subject {c['subject_id']:<5} {c['name']}")
        for r in x.get("core_phrases_rejected", []):
            print(f"      (a model already refused {r['phrase']!r} for {r['name']})")
    if len(rows) > limit:
        print(f"\n  ... {len(rows) - limit} more; raise --limit to see them")


def rank(queue: list[dict]) -> None:
    """Which rulings buy the most. A candidate seen many times is one decision, many rows."""
    from collections import Counter
    c = Counter(cand["name"] for x in queue for cand in x["candidates"])
    print(f"[review] {len(queue)} claim(s) queued. Most-repeated candidates first — one "
          f"ruling on a repeated name settles every instance:\n")
    for name, n in c.most_common(15):
        print(f"    {n:>4}  {name}")


class Refused(Exception):
    """A ruling that cannot be proved against the claim's own text."""


def store(cur, claim_id: int, subject_id: int, phrase: str, who: str,
          dry_run: bool = False) -> dict:
    """The one place a human mention is written. CLI and web UI both come through here.

    ONE GUARD, ONE IMPLEMENTATION. Two callers enforcing "the phrase must occur in the
    verbatim" separately is two chances to stop enforcing it; the web form is exactly where
    a shortcut would be tempting, because the click is so cheap.
    """
    cur.execute("SELECT verbatim FROM claims WHERE id=%s", (claim_id,))
    if not (row := cur.fetchone()):
        raise Refused(f"no claim {claim_id}")
    v = row[0]
    # EMPTY IS NOT A MATCH. "".find() returns 0, so a blank phrase passes a naive find() check
    # and reaches the database as a zero-length span -- caught there only by
    # CHECK (span_end > span_start), as an opaque CheckViolation. The UI can hand this over
    # whenever no contiguous phrase was found to prefill the box with.
    if not (phrase or "").strip():
        raise Refused(
            f"no phrase given for claim {claim_id}. A mention is a span of the sentence, so "
            f"type the words as the document printed them. If the sentence never names the "
            f"programme in contiguous words, there is no mention to record -- rule it No.")
    # A human ruling is a judgement about meaning, not a licence to assert a span the
    # sentence does not contain.
    a = v.find(phrase)
    if a < 0:
        low = v.lower().find(phrase.lower())
        hint = (f" Did you mean {v[low:low + len(phrase)]!r}? Case must match."
                if low >= 0 else "")
        raise Refused(f"{phrase!r} does not occur in claim {claim_id}.{hint}")
    # WORD BOUNDARIES. A span may be literally present and still not be a phrase: a review
    # session produced `oint campaign entitled "The Future is Electric"` -- provable, and a
    # slicing artifact of "joint". Occurring in the text is necessary, not sufficient; a
    # mention has to start and end where a word does.
    if (a > 0 and (v[a - 1].isalnum() and phrase[0].isalnum())) or \
       (a + len(phrase) < len(v) and v[a + len(phrase)].isalnum() and phrase[-1].isalnum()):
        lo, hi = a, a + len(phrase)
        while lo > 0 and v[lo - 1].isalnum():
            lo -= 1
        while hi < len(v) and v[hi].isalnum():
            hi += 1
        raise Refused(f"{phrase!r} starts or ends mid-word in claim {claim_id}. "
                      f"Did you mean {v[lo:hi]!r}?")
    cur.execute("SELECT name FROM subjects WHERE id=%s", (subject_id,))
    if not (sub := cur.fetchone()):
        raise Refused(f"no subject {subject_id}")
    out = {"claim_id": claim_id, "subject_id": subject_id, "subject_name": sub[0],
           "span_start": a, "span_end": a + len(phrase), "matched_text": v[a:a + len(phrase)]}
    if dry_run:
        return out | {"stored": False, "dry_run": True}
    cur.execute("""INSERT INTO claim_subject_mentions
                     (claim_id, subject_id, span_start, span_end, matched_text,
                      method, detected_by, confirmed_by, confirmed_at)
                   VALUES (%s,%s,%s,%s,%s,'human',%s,%s, now())
                   ON CONFLICT (claim_id, subject_id, span_start) DO NOTHING""",
                (claim_id, subject_id, a, a + len(phrase), out["matched_text"], who, who))
    return out | {"stored": bool(cur.rowcount), "dry_run": False}


def attribute_money(cur, claim_id: int, subject_id: int) -> int:
    """Point this claim's fiscal_references rows at the subject. Never overwrites one."""
    cur.execute("UPDATE fiscal_references SET subject_id=%s "
                "WHERE claim_id=%s AND subject_id IS NULL", (subject_id, claim_id))
    return cur.rowcount


def record(queue_path: Path, claim_id: int, subject_id: int, phrase: str, who: str,
           dsn: str = DSN, dry_run: bool = False, money: bool = False) -> None:
    import psycopg

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        try:
            r = store(cur, claim_id, subject_id, phrase, who, dry_run)
        except Refused as e:
            raise SystemExit(f"[review] {e}")
        print(f"[review] claim {claim_id} -> subject {subject_id} ({r['subject_name']})")
        print(f"         span {r['span_start']}..{r['span_end']} = {r['matched_text']!r}")
        if dry_run:
            print("[review] dry run — nothing written")
            return
        n = attribute_money(cur, claim_id, subject_id) if money else 0
        c.commit()
    print(f"[review] {'stored' if r['stored'] else 'already present — nothing changed'}")
    if n:
        print(f"[review] attributed {n} fiscal_references row(s) to this subject")
    drop(queue_path, claim_id, subject_id, quiet=True)


def drop(queue_path: Path, claim_id: int, subject_id: int | None,
         quiet: bool = False) -> None:
    """Remove a candidate (or a whole claim) from the queue, so a re-run does not re-offer it."""
    queue = load(queue_path)
    out, removed = [], 0
    for x in queue:
        if x["claim_id"] != claim_id:
            out.append(x)
            continue
        if subject_id is None:
            removed += len(x["candidates"])
            continue
        keep = [c for c in x["candidates"] if c["subject_id"] != subject_id]
        removed += len(x["candidates"]) - len(keep)
        # A REFUSAL IS REMEMBERED, or the next automated run proposes it again.
        rej = x.get("core_phrases_rejected", [])
        for c in x["candidates"]:
            if c["subject_id"] == subject_id:
                rej.append({"name": c["name"], "phrase": "(ruled by hand)"})
        if keep:
            out.append({**x, "candidates": keep, "core_phrases_rejected": rej})
    queue_path.write_text(json.dumps(out, indent=1))
    if not quiet:
        print(f"[review] removed {removed} candidate(s); {len(out)} claim(s) left in the queue")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", default=str(QUEUE))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--subject", help="show only candidates whose name contains this")
    ap.add_argument("--claim", type=int, help="show only this claim")
    ap.add_argument("--rank", action="store_true",
                    help="list candidates by how often they recur, most first")
    ap.add_argument("--yes", nargs=3, metavar=("CLAIM_ID", "SUBJECT_ID", "PHRASE"),
                    help="record a human mention; PHRASE must occur in the claim verbatim")
    ap.add_argument("--drop", nargs="+", metavar="CLAIM_ID [SUBJECT_ID]",
                    help="remove a candidate from the queue without storing anything")
    ap.add_argument("--money", action="store_true",
                    help="also point this claim's fiscal_references rows at the subject")
    ap.add_argument("--by", default=os.environ.get("USER", "human"))
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    qp = Path(a.queue)
    if a.yes:
        record(qp, int(a.yes[0]), int(a.yes[1]), a.yes[2], a.by, a.dsn, a.dry_run, a.money)
    elif a.drop:
        drop(qp, int(a.drop[0]), int(a.drop[1]) if len(a.drop) > 1 else None)
    elif a.rank:
        rank(load(qp))
    else:
        show(load(qp), a.limit, a.subject, a.claim, a.dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
