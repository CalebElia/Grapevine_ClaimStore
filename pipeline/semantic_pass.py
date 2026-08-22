"""The judgement pass: resolve what the document's own evidence cannot decide.

WHAT BELONGS HERE, AND WHAT EMPHATICALLY DOES NOT. Every conversion defect found in this
corpus so far had a mechanical cause -- a 2pt crop pad weaving two sentences together,
word spaces drawn on their own baseline, a bullet encoded as the letter o, a heading
printed inside a photograph. A model asked to "clean up" that output would have produced
plausible text over every one of them and hidden the fact that the geometry was wrong.
None of those cases reach this module, and none should: they are fixed where they happen.

What reaches this module is the residue that is genuinely undecidable from the document
alone, of which there are exactly two kinds:

  * an ambiguous hyphen, where "Collab-orator" splits across a line and the document
    attests neither the joined word nor the hyphenated compound anywhere else, and
  * a hyperlink whose citing sentence string matching could not locate.

THE MODEL PROPOSES; STRING MATCHING DECIDES. This is the same contract as claim
extraction, applied one layer down, and it is what keeps a judgement pass from becoming a
fabrication pass:

  * A hyphen decision must be one of exactly TWO strings -- the joined form or the
    hyphenated form. The model chooses between them; it never supplies text. There is no
    third answer it can give, so it cannot invent a word.
  * A link's citing sentence must be found CHARACTER-FOR-CHARACTER in the converted text.
    A sentence the model paraphrased, tidied or imagined is not located and is rejected,
    leaving the link exactly as unanchored as it was.

So the worst case for this module is that it changes nothing. It cannot put a word in the
document that the document does not contain.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from pipeline import config

HYPHEN_PROMPT = """You are resolving line-break hyphens in a municipal climate report.

Each case is a word that was split across a line break as "A-B". Decide whether the
original word is the JOINED form (the hyphen was only a line-break artifact) or the
HYPHENATED form (the hyphen is part of the real word).

Answer with a JSON array, one object per case, in the same order:
  {"a": "...", "b": "...", "choice": "join" | "hyphen", "why": "<8 words or fewer>"}

Choose "hyphen" for real compounds (zero-emission, plant-forward), for identifiers
(U-20836 is a utility rate-case docket number), and whenever you are unsure.
Choose "join" only when the joined form is an ordinary English word and the hyphenated
form is not something anyone would write.

Return only the JSON array."""

LINK_PROMPT = """You are locating the sentence that cites a hyperlink in a municipal
climate report.

You are given the report's text and a list of URLs whose citing sentence could not be
found by string matching. For each URL, quote the ONE sentence from the supplied text
that the link belongs to -- the sentence a reader would be reading when they clicked it.

Answer with a JSON array, one object per URL, in the same order:
  {"uri": "...", "sentence": "<quoted EXACTLY from the text>", "why": "<8 words or fewer>"}

The sentence must be copied character for character from the text I gave you. Do not
correct spelling, spacing or punctuation; a sentence I cannot find verbatim is discarded.
If no sentence in the text plausibly cites the URL -- because it is a navigation link, a
masthead, or a contents entry -- return "sentence": "" for it.

Return only the JSON array."""


def _ask(prompt: str, payload: str, deployment_env: str) -> list[dict]:
    """One structured call. Returns [] rather than raising on unusable output."""
    from openai import OpenAI

    depl = config.get(deployment_env)
    client = OpenAI(base_url=config.get("OPENAI_BASE_URL"),
                    api_key=config.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=depl,
        messages=[{"role": "system", "content": prompt},
                  {"role": "user", "content": payload}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


def resolve_hyphens(cases: list[tuple[str, str]], sentences: dict[str, str],
                    deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> list[dict]:
    """Decide join-or-hyphen for each ambiguous split.

    TWO STRINGS, NEVER FREE TEXT. The model returns a choice between "ab" and "a-b" and
    nothing else, so the resolved token is always one the split itself produced. A reply
    naming neither, or naming a case that was not asked about, is dropped.
    """
    if not cases:
        return []
    payload = json.dumps([{"a": a, "b": b, "sentence": sentences.get(f"{a}-{b}", "")}
                          for a, b in cases], indent=1)
    asked = {(a, b) for a, b in cases}
    out = []
    for r in _ask(HYPHEN_PROMPT, payload, deployment_env):
        a, b, choice = r.get("a"), r.get("b"), r.get("choice")
        if (a, b) not in asked or choice not in ("join", "hyphen"):
            continue
        out.append({"a": a, "b": b, "choice": choice,
                    "resolved": f"{a}{b}" if choice == "join" else f"{a}-{b}",
                    "why": str(r.get("why", ""))[:60],
                    "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return out


def merge_anchored(links: list[dict], anchored: list[dict]) -> list[dict]:
    """Fold model-anchored links back in, WITHOUT touching one string matching located.

    The precedence is the whole point and it is easy to lose in a merge: a deterministic
    anchor is evidence, a model's is a proposal, and a proposal never overwrites evidence.
    Keying the merge on (uri, page) alone broke that -- a page can cite the same URL twice,
    once anchored and once not, and the unanchored one's answer then replaced the anchored
    one's. Six links were rewritten that way on the first run, two of them to a materially
    different sentence.

    So the replacement is positional and conditional: only a record that is still
    `located: False` can be replaced, and only by an answer for its own uri and page.
    """
    by: dict[tuple, dict] = {}
    for a in anchored:
        by.setdefault((a["uri"], a["page_no"]), a)
    return [by.get((l["uri"], l["page_no"]), l) if not l.get("located") else l
            for l in links]


def anchor_links(links: list[dict], text: str,
                 deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> list[dict]:
    """Find the citing sentence for links string matching could not place.

    CHARACTER-FOR-CHARACTER OR NOT AT ALL. The returned sentence is located in `text` by
    exact substring search, and its offsets come from that search rather than from the
    model. A paraphrase, a tidied quote or an invention simply is not found, and the link
    stays unanchored -- which is where it already was, so a wrong answer costs nothing.
    """
    if not links:
        return []
    payload = json.dumps({"text": text,
                          "urls": [l["uri"] for l in links]}, indent=1)
    by_uri = {l["uri"]: l for l in links}
    out = []
    for r in _ask(LINK_PROMPT, payload, deployment_env):
        uri, sent = r.get("uri"), " ".join(str(r.get("sentence") or "").split())
        if uri not in by_uri or not sent:
            continue
        at = text.find(sent)
        if at < 0:                      # not verbatim -> not located, no exception made
            continue
        out.append({**by_uri[uri], "located": True, "anchored_by": "semantic_pass",
                    "context_sentence": sent, "anchor_text": sent,
                    "char_start": at, "char_end": at + len(sent),
                    "why": str(r.get("why", ""))[:60],
                    "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return out


def run(pdf: str, blocks: str, links_path: Path, hyphens_path: Path,
        deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> dict:
    """One document through both resolvers. Returns a summary; writes both files.

    A CLI, and not a notebook cell, because the last gap in this corpus was a stage that
    existed and had simply never been re-run for four of five documents.
    """
    from pipeline.convert_blocks import convert

    conv, _, report = convert(pdf, blocks)
    cases = [tuple(x) for x in (report.get("ambiguous_hyphens") or [])]
    sentences = {}
    for a, b in cases:
        m = re.search(rf"[^.]*\b{re.escape(a)}-{re.escape(b)}\b[^.]*\.", conv.text)
        if m:
            sentences[f"{a}-{b}"] = m.group(0).strip()
    decided = resolve_hyphens(cases, sentences, deployment_env)
    hyphens_path.parent.mkdir(parents=True, exist_ok=True)
    hyphens_path.write_text(json.dumps(decided, indent=2))

    links = json.loads(links_path.read_text()) if links_path.exists() else []
    before = sum(1 for l in links if l.get("located"))
    unplaced = [l for l in links if not l.get("located")]
    got = anchor_links(unplaced, conv.text, deployment_env) if unplaced else []
    if got:
        links = merge_anchored(links, got)
        links_path.write_text(json.dumps(links, indent=2))
    return {"hyphens": len(cases), "resolved": len(decided),
            "joined": sum(1 for d in decided if d["choice"] == "join"),
            "links": len(links), "located_before": before,
            "located_after": sum(1 for l in links if l.get("located"))}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="resolve what the document cannot decide")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--blocks", required=True)
    ap.add_argument("--links", required=True, help="<doc>-links.json from extract_links")
    ap.add_argument("--hyphens", required=True, help="where to write the hyphen rulings")
    ap.add_argument("--deployment-env", default="GRAPEVINE_DEPLOYMENT_EXTRACT")
    a = ap.parse_args()
    s = run(a.pdf, a.blocks, Path(a.links), Path(a.hyphens), a.deployment_env)
    print(f"[semantic] {s['resolved']}/{s['hyphens']} hyphen(s) ruled "
          f"({s['joined']} joined) · links located {s['located_before']} -> "
          f"{s['located_after']} of {s['links']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def align_window(primary: str, token: str, second: str,
                 before: int = 4, after: int = 2) -> str | None:
    """The second arm's counterpart to `token`, located by the words that precede it.

    A BARE TOKEN IS NOT A READING. "215" is not a choice anyone can make; "March 215, 2022"
    against "March 21st, 2022" is. So each side is presented as a window, and the windows
    have to be the same piece of the page or the comparison is meaningless.

    THE ANCHOR SHRINKS. The two arms differ NEAR the token -- that is why there is a
    conflict -- so the longest anchor is the likeliest to fail. Dropping the outermost word
    and retrying walks in from the part they disagree about toward the part they share.
    Returns None when nothing matches, because inventing a counterpart would put words in
    the second arm's mouth and hand the model a fabricated choice.
    """
    words = primary.split()
    # A WHOLE WORD, NOT A SUBSTRING. Found the hard way: the Year 2 conflict token "wee"
    # located itself inside "week-long" -- an unrelated, correctly-read word several
    # sentences earlier -- and would have put an entirely fabricated disagreement to the
    # model. The token is a word the second arm failed to produce, so it must be matched
    # the way the conflict was detected: bounded.
    pat = re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", re.I)
    idx = next((i for i, w in enumerate(words) if pat.search(w)), None)
    if idx is None:
        return None
    low = second.lower()
    for n in range(min(before, idx), 0, -1):
        anchor = " ".join(words[idx - n:idx]).lower()
        at = low.find(anchor)
        if at < 0:
            continue
        tail = second[at + len(anchor):].split()
        return " ".join(words[idx - n:idx] + tail[:after]).strip()
    return None


_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:[^)]*)\)")


def build_conflict_case(token: str, primary_text: str, second_text: str,
                        context_chars: int = 150) -> dict | None:
    """One adjudication case: both arms' readings of the same words, plus the sentence.

    Returns None when the second arm's counterpart cannot be located. A case with one side
    empty is not a choice -- it would ask the model to pick between a reading and nothing,
    and the only answer it could give is the one we already have.
    """
    # A URL IS NOT A READING. The CU arm emits markdown links, so the counterpart word can
    # arrive as "[program,](https://www.a2gov.org/...)". Keep the link TEXT, drop the target.
    second_text = _MD_LINK.sub(r"\1", second_text)

    pat = re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", re.I)
    words = primary_text.split()
    idx = next((i for i, w in enumerate(words) if pat.search(w)), None)
    if idx is None:
        return None
    window = align_window(primary_text, token, second_text, before=5, after=2)
    if window is None:
        return None

    primary_reading = words[idx].strip(".,;:!?)")
    # The counterpart is what the second arm has AFTER the shared anchor, and the anchor is
    # the leading part of the window -- so the counterpart is whatever the window added.
    anchor_len = len(window.split()) - 2
    tail = window.split()[max(anchor_len, 0):]
    second_reading = " ".join(tail).strip(".,;:!?)") or None
    if not second_reading:
        return None

    m = pat.search(primary_text)
    a = max(0, m.start() - context_chars)
    context = re.sub(r"\s+", " ", primary_text[a:m.end() + context_chars]).strip()
    return {"token": token, "primary": primary_reading, "second": second_reading,
            "context": context}


CONFLICT_PROMPT = """You are adjudicating a disagreement between two independent reads of \
the same printed page. One read came from the PDF's text layer, the other from OCR of the \
rendered pixels. Glyph geometry was already consulted and could not decide.

For each case you get the token as each arm read it, plus the surrounding sentence.

Reply with a JSON list. Each item: {"token": "...", "choice": "primary"|"second"|"neither", \
"why": "under 15 words"}.

Choose "primary" or "second" only when one of those exact readings is right.

Choose "neither" when NEITHER reading is correct -- for example when the characters look \
like a footnote marker, a superscript, or some other artefact next to a real word. Do not \
try to say what the text should be. Naming the pattern is more useful than guessing the \
words, because a wrong guess would hide a structural problem in the document.

You may not supply text of your own. Your only answers are those three words."""

_CHOICES = ("primary", "second", "neither")


def pick_reading(cases: list[dict], replies: list[dict]) -> list[dict]:
    """Validate the model's choice among readings the ARMS produced. Never its own text.

    THREE WORDS, NEVER FREE TEXT. This is resolve_hyphens' contract with one addition. A
    hyphen has exactly two possible resolutions, so "choose one of two" is total. A
    cross-arm conflict does not: on Year 2 the primary read "wet", OCR read "we2", and the
    truth is "we" followed by a footnote marker -- neither arm is right and no choice
    between them is either.

    So "neither" is a first-class answer that SURFACES rather than resolves. It sets
    resolved=None and surfaced=True, and the document keeps saying exactly what it said.
    Correcting "wet" to "we" would have produced clean text and erased the discovery that
    this report has footnotes the pipeline does not model -- trading a visible defect for
    an invisible one.

    Anything else is dropped: a reply supplying its own reading is fabrication wearing a
    choice's clothes.
    """
    if not cases:
        return []
    by_token = {c["token"]: c for c in cases}
    out = []
    for r in replies:
        tok, choice = r.get("token"), r.get("choice")
        if tok not in by_token or choice not in _CHOICES:
            continue
        case = by_token[tok]
        out.append({
            "token": tok,
            "choice": choice,
            "resolved": None if choice == "neither" else case[choice],
            "surfaced": choice == "neither",
            "why": str(r.get("why", ""))[:60],
            "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    return out


def resolve_conflicts(cases: list[dict],
                      deployment_env: str = "GRAPEVINE_DEPLOYMENT_EXTRACT") -> list[dict]:
    """Ask the model to choose among the arms' readings for each undecided conflict."""
    if not cases:
        return []
    payload = json.dumps([{"token": c["token"], "primary": c["primary"],
                           "second": c["second"], "context": c["context"]}
                          for c in cases], indent=1)
    return pick_reading(cases, _ask(CONFLICT_PROMPT, payload, deployment_env))
