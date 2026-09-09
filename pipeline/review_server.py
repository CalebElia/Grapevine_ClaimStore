"""A local review UI for the mention queue: read the context, click the verdict.

WHY A UI AND NOT A CLI. The command-line reviewer works, but it makes you retype a claim id,
a subject id and an exact phrase for every ruling -- and the ruling itself takes two seconds
of thought. When the ceremony costs more than the judgement, the queue does not get worked.

WHAT IT ADDS THAT THE CLI CANNOT. Claims carry char offsets into their document's canonical
text, so the surrounding page can be sliced out and shown, exactly as the document had it.
That is the thing you actually need for the hard calls: "on the circular economy, sustainable
housing and transportation" reads differently once you can see it sits in a list of forum
topics. The context is not reconstructed or summarised -- it is a substring of the same text
the claim was extracted from, which is why it can be trusted as evidence.

IT IS STILL THE SAME GUARD. Every yes goes through review_mentions.store(), so a click can no
more assert an unprovable span than a typed command can. The button is faster, not weaker.

LOCALHOST ONLY, and deliberately no authentication -- it writes to a scratch database with
trust auth on a single-user machine, and adding a login would imply a security property this
does not have. Do not bind it to a routable interface.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pipeline import review_mentions as rm

DSN = rm.DSN
WINDOW = 700          # characters of document shown either side of the claim


def build_stamp() -> str:
    """A short hash of this module's own source, shown in the page header.

    "Is the running server the code I just edited?" was answerable only by reading HTML with
    curl. A stale browser tab and a dead server look identical from the outside, so the page
    states which build drew it.
    """
    import hashlib
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:7]


@lru_cache(maxsize=16)
def _canonical(md_path: str) -> str:
    """The document text the claim's offsets index into. Cached: rebuilding is not free."""
    from pipeline import canonical
    md = Path(md_path).read_text()
    try:
        return canonical.build(md).text
    except Exception:
        return md


def context(cur, claim_id: int, window: int = WINDOW) -> dict:
    """Where the claim came from, and what surrounded it."""
    cur.execute("""SELECT cl.verbatim, cl.span_start, cl.span_end,
                          s.heading, s.page_start, s.page_end, s.section_topic,
                          d.title, d.doc_type, d.covers_period_start, d.markdown_path,
                          sub.name
                     FROM claims cl
                     JOIN document_sections s ON s.id = cl.document_section_id
                     JOIN documents d ON d.id = s.document_id
                     LEFT JOIN subjects sub ON sub.id = cl.subject_id
                    WHERE cl.id = %s""", (claim_id,))
    if not (r := cur.fetchone()):
        return {}
    (verbatim, a, b, heading, p0, p1, topic, title, doc_type, covers, md, about) = r
    out = {"verbatim": verbatim, "heading": heading, "page_start": p0, "page_end": p1,
           "section_topic": topic, "document": title, "doc_type": doc_type,
           "covers": str(covers) if covers else None, "about": about,
           "before": "", "after": "", "context_ok": False}
    if md and a is not None and Path(md).exists():
        text = _canonical(md)
        # PROVE IT BEFORE SHOWING IT. If the offsets no longer slice to the verbatim the
        # document has been re-rendered underneath the claim, and the surrounding text is
        # about some other part of the page. Better to show nothing than the wrong page.
        if text[a:b] == verbatim:
            out |= {"before": text[max(0, a - window):a], "after": text[b:b + window],
                    "context_ok": True}
    cur.execute("""SELECT amount_low, amount_high, purpose, award_status, subject_id
                     FROM fiscal_references WHERE claim_id = %s""", (claim_id,))
    out["money"] = [{"amount_low": float(x[0]) if x[0] is not None else None,
                     "amount_high": float(x[1]) if x[1] is not None else None,
                     "purpose": x[2], "award_status": x[3], "attributed": x[4] is not None}
                    for x in cur.fetchall()]
    return out


def items(queue_path: Path, proposals_path: Path | None, dsn: str) -> list[dict]:
    """One row per (claim, candidate) pair, with its context and any proposed verdict.

    A proposal is a suggestion with a reason attached, never a decision. It arrives
    pre-selected in the UI so agreeing is one keystroke, and it is still your click that
    writes the row.
    """
    import psycopg

    proposed: dict[tuple[int, int], dict] = {}
    if proposals_path and proposals_path.exists():
        for p in json.loads(proposals_path.read_text()):
            proposed[(p["claim_id"], p["subject_id"])] = p

    queue = rm.load(queue_path)
    seen, out = set(), []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for entry in queue:
            cid = entry["claim_id"]
            ctx = context(cur, cid)
            refused = {r["name"] for r in entry.get("core_phrases_rejected", [])}
            for cand in entry["candidates"]:
                key = (cid, cand["subject_id"])
                seen.add(key)
                p = proposed.get(key, {})
                out.append({"claim_id": cid, "subject_id": cand["subject_id"],
                            "subject_name": cand["name"],
                            "model_refused": cand["name"] in refused,
                            "proposed": p.get("verdict"), "reason": p.get("reason"),
                            "borderline": p.get("borderline", False),
                            "phrase": p.get("phrase") or _guess(ctx.get("verbatim", ""),
                                                                cand["name"]),
                            "hits": (h := word_hits(ctx.get("verbatim", ""), cand["name"])),
                            "run": enclosing_run(ctx.get("verbatim", ""), h),
                            "source": "queue", **ctx})
        # A proposal about a claim that is NOT in the queue still deserves review -- the
        # batch was built from a database sweep, which is wider than the queue file.
        for (cid, sid), p in proposed.items():
            if (cid, sid) in seen:
                continue
            ctx = context(cur, cid)
            if not ctx:
                continue
            cur.execute("SELECT name FROM subjects WHERE id=%s", (sid,))
            name = (cur.fetchone() or ["?"])[0]
            out.append({"claim_id": cid, "subject_id": sid, "subject_name": name,
                        "model_refused": False, "proposed": p.get("verdict"),
                        "reason": p.get("reason"), "borderline": p.get("borderline", False),
                        "phrase": p.get("phrase") or _guess(ctx["verbatim"], name),
                        "hits": (h := word_hits(ctx["verbatim"], name)),
                        "run": enclosing_run(ctx["verbatim"], h),
                        "source": "proposal", **ctx})
    # Borderline first: those are the ones that need a person, and burying them under twenty
    # obvious ones is how they get rubber-stamped.
    out.sort(key=lambda r: (not r["borderline"], r["proposed"] != "yes", r["claim_id"]))
    return out


def word_hits(verbatim: str, name: str) -> list[dict]:
    """Where each identity word of the name actually lands in the sentence.

    When no contiguous phrase exists, this is the difference between a dead end and an
    informed ruling: "Update Building Codes" against "updates to the Michigan building and
    energy codes" shows `building` and `codes` present, `update` absent as a whole word --
    so the reviewer can see the words are scattered and decide whether a shorter real phrase
    ("building and energy codes") denotes the programme, or whether it is a No.
    """
    low = verbatim.lower()
    out = []
    for w in dict.fromkeys(w.strip(".,:;()").lower() for w in name.split()):
        if len(w) <= 3:
            continue
        i = low.find(w)
        out.append({"word": w, "at": i, "printed": verbatim[i:i + len(w)] if i >= 0 else None})
    return out


# A run longer than this is not a phrase naming a programme, it is most of a sentence.
_MAX_RUN = 90


def enclosing_run(verbatim: str, hits: list[dict]) -> str:
    """The literal substring spanning every identity word, when there is a sensible one.

    "Update Building Codes" is not in the sentence, but "updates to the Michigan building and
    energy codes" is -- and it is a real substring, so it can be stored and later proved.
    Offered as a suggestion the reviewer can shorten or reject, never applied on its own.
    """
    found = [h for h in hits if h["at"] >= 0]
    if len(found) < 2 or len(found) != len(hits):
        return ""       # a word missing entirely means the run would prove nothing
    a = min(h["at"] for h in found)
    b = max(h["at"] + len(h["word"]) for h in found)
    return verbatim[a:b] if b - a <= _MAX_RUN else ""


def _guess(verbatim: str, name: str) -> str:
    """A starting phrase for the box: the longest run of the name's words present verbatim.

    Only a convenience -- whatever ends up in the box is checked against the claim on submit.
    """
    words = [w for w in name.split() if len(w) > 3]
    for n in range(len(words), 1, -1):
        for i in range(len(words) - n + 1):
            probe = " ".join(words[i:i + n])
            if (j := verbatim.lower().find(probe.lower())) >= 0:
                return verbatim[j:j + len(probe)]
    return ""


PAGE = r"""<!doctype html><meta charset=utf-8><title>Mention queue</title>
<style>
:root{--bg:#fbfbfa;--fg:#1c1c1a;--dim:#6b6b66;--line:#e0e0dc;--card:#fff;
      --yes:#1a7f4b;--no:#b03030;--warn:#8a6d1f;--hl:#ffe9a8;--acc:#2f6fb0}
@media(prefers-color-scheme:dark){:root{--bg:#16171a;--fg:#e8e8e4;--dim:#9a9a94;
      --line:#2e3035;--card:#1e2024;--hl:#5a4a12;--yes:#4ec27f;--no:#e07a7a;--acc:#7fb3e8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
       padding:.7rem 1.2rem;display:flex;gap:1rem;align-items:center;z-index:5;flex-wrap:wrap}
h1{font-size:1rem;margin:0;font-weight:600}
.bar{flex:1;height:6px;background:var(--line);border-radius:3px;overflow:hidden;min-width:120px}
.bar>i{display:block;height:100%;background:var(--yes);width:0;transition:width .2s}
main{max-width:none;padding:1.2rem;display:grid;
     grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:1rem;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
.card.done{opacity:.4}
.meta{font-size:.78rem;color:var(--dim);display:flex;gap:.5rem;flex-wrap:wrap;
      margin-bottom:.6rem}
.tag{border:1px solid var(--line);border-radius:99px;padding:.05rem .5rem}
.tag.bl{color:var(--warn);border-color:var(--warn)}
.tag.mr{color:var(--no);border-color:var(--no)}
.claim{font-size:1.02rem;margin:.4rem 0 .7rem;padding:.6rem .7rem;border-left:3px solid var(--acc);
       background:color-mix(in srgb,var(--acc) 6%,transparent);border-radius:0 6px 6px 0}
mark{background:var(--hl);color:inherit;padding:0 .1em;border-radius:2px}
/* NO TRANSITION ON max-height. While it animates, clientHeight is still near zero, so a
   scrollTop set in the same frame gets clamped to 0 and the panel opens on the wrong text. */
.ctx{font-size:.83rem;color:var(--dim);display:none;overflow:auto;max-height:17rem;
     white-space:pre-wrap;border-left:2px solid var(--line);padding-left:.7rem}
.ctx.open{display:block}
.ctx b{color:var(--fg);font-weight:600}
.q{margin:.7rem 0 .5rem;font-weight:600}
.q .s{color:var(--acc)}
.why{font-size:.85rem;color:var(--dim);margin:.3rem 0 .6rem;font-style:italic}
.money{font-size:.85rem;border:1px dashed var(--line);border-radius:6px;padding:.45rem .6rem;
       margin:.5rem 0}
label{display:flex;gap:.4rem;align-items:center;cursor:pointer}
input[type=text]{width:100%;padding:.4rem .5rem;font:inherit;font-size:.9rem;
                 border:1px solid var(--line);border-radius:6px;background:var(--bg);
                 color:var(--fg);margin:.3rem 0}
.btns{display:flex;gap:.5rem;margin-top:.6rem;flex-wrap:wrap}
button{font:inherit;font-size:.9rem;padding:.4rem .9rem;border-radius:7px;cursor:pointer;
       border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button.y{border-color:var(--yes);color:var(--yes)}
button.n{border-color:var(--no);color:var(--no)}
button.sel{background:var(--acc);border-color:var(--acc);color:#fff}
button:hover{filter:brightness(1.08)}
.msg{font-size:.82rem;margin-top:.45rem;min-height:1.1em}
.ok{color:var(--yes)} .err{color:var(--no)}
.link{background:none;border:0;color:var(--acc);padding:0;font-size:.82rem;
      text-decoration:underline;cursor:pointer}
</style>
<header>
  <h1>Mention queue</h1>
  <span id=count class=meta></span>
  <div class=bar><i id=prog></i></div>
  <button class=link onclick="allCtx()">toggle all context</button>
  <span class=meta id=build></span>
</header>
<main id=app></main>
<script>
const esc = s => (s??'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// Highlight every case-insensitive occurrence of the phrase, without letting the phrase
// itself become markup.
function hl(text, phrase){
  if(!phrase) return esc(text);
  const parts = text.split(new RegExp('('+phrase.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'));
  return parts.map((p,i)=> i%2 ? '<mark>'+esc(p)+'</mark>' : esc(p)).join('');
}
let ITEMS=[], done=0;
// No contiguous phrase was found. Say so, and show where the name's words actually landed --
// an empty box with no explanation reads as a bug, and pressing Yes on it is a refusal.
function nophrase(r){
  const has = (r.hits||[]).filter(h=>h.at>=0), no = (r.hits||[]).filter(h=>h.at<0);
  return `<div class=money style="border-color:var(--warn)">⚠ The sentence never names
    <b>${esc(r.subject_name)}</b> in contiguous words, so there is no span to store.
    ${has.length?`Present: ${has.map(h=>'<mark>'+esc(h.printed)+'</mark>').join(' ')}.`:''}
    ${no.length?`Absent: ${no.map(h=>'<i>'+esc(h.word)+'</i>').join(', ')}.`:''}
    <br>${r.run ? `The sentence does contain
      <button class=link onclick="use(${ITEMS.indexOf(r)},this.dataset.r)"
              data-r="${esc(r.run)}">“${esc(r.run)}”</button> — click to use it if that
      phrase denotes the programme.` : 'Type a shorter phrase that really appears'}
    Otherwise rule <b>No</b>.</div>`;
}
function draw(){
  document.getElementById('app').innerHTML = ITEMS.map((r,i)=>{
    const where = [r.document, r.covers, r.page_start?('p.'+r.page_start):null,
                   r.heading].filter(Boolean).map(x=>`<span class=tag>${esc(x)}</span>`).join('');
    const money = (r.money||[]).filter(m=>!m.attributed).map(m=>`
      <div class=money>💵 <b>$${(m.amount_low??0).toLocaleString()}</b> —
        ${esc(m.purpose||'')} <span class=tag>${esc(m.award_status)}</span>
        <label style="margin-top:.35rem"><input type=checkbox id="m${i}" checked>
          also attribute this money to the programme</label></div>`).join('');
    const ctx = r.context_ok
      ? `<button class=link onclick="tog(${i})">show surrounding page ▾</button>
         <div class=ctx id="c${i}">…${esc(r.before)}<b>${hl(r.verbatim,r.phrase)}</b>${esc(r.after)}…</div>`
      : `<span class=meta>no verifiable page context</span>`;
    return `<div class=card id="k${i}">
      <div class=meta>${where}<span class=tag>claim ${r.claim_id}</span>
        ${r.borderline?'<span class="tag bl">borderline</span>':''}
        ${r.model_refused?'<span class="tag mr">model said no</span>':''}</div>
      <div class=claim>${hl(r.verbatim, r.phrase)}</div>
      ${ctx}
      <div class=q>Does this name <span class=s>${esc(r.subject_name)}</span>?</div>
      <div class=meta>already about: ${esc(r.about||'—')}</div>
      ${r.reason?`<div class=why>Proposed <b>${esc(r.proposed)}</b> — ${esc(r.reason)}</div>`:''}
      ${money}
      ${r.phrase ? '' : nophrase(r)}
      <input type=text id="p${i}" value="${esc(r.phrase)}" placeholder="phrase as printed">
      <div class=btns>
        <button class="y ${r.proposed==='yes'?'sel':''}" onclick="go(${i},'yes')">Yes ✓</button>
        <button class="n ${r.proposed==='no'?'sel':''}" onclick="go(${i},'no')">No ✕</button>
        <button onclick="go(${i},'skip')">Skip</button>
      </div>
      <div class=msg id="s${i}"></div></div>`;
  }).join('');
  document.getElementById('count').textContent =
    `${ITEMS.length} open · ${done} ruled this session`;
  document.getElementById('prog').style.width =
    (ITEMS.length? done/(done+ITEMS.length)*100 : 0)+'%';
}
// Open scrolled TO THE CLAIM, not to the top of the window. A context panel that opens on
// text 700 characters earlier makes the reader hunt for the sentence they are ruling on,
// which is the opposite of the point.
const use = (i, v) => { const b = document.getElementById('p'+i); b.value = v; b.focus(); };
function tog(i){
  const box = document.getElementById('c'+i);
  const opened = box.classList.toggle('open');
  if(!opened) return;
  const b = box.querySelector('b');
  if(b) box.scrollTop = Math.max(0, b.offsetTop - box.offsetTop
                                    - box.clientHeight/2 + b.clientHeight/2);
}
function allCtx(){
  const anyClosed = [...document.querySelectorAll('.ctx')].some(e=>!e.classList.contains('open'));
  ITEMS.forEach((_,i)=>{
    const box = document.getElementById('c'+i);
    if(box && box.classList.contains('open') !== anyClosed) tog(i);
  });
}
async function go(i, verdict){
  const r = ITEMS[i], msg = document.getElementById('s'+i);
  const money = document.getElementById('m'+i);
  const phrase = document.getElementById('p'+i).value;
  if(verdict==='yes' && !phrase.trim()){
    msg.className='msg err';
    msg.textContent='✕ Type the phrase as the document printed it, or rule No.';
    document.getElementById('p'+i).focus(); return;
  }
  msg.className='msg'; msg.textContent='…';
  const res = await fetch('/api/decide', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({claim_id:r.claim_id, subject_id:r.subject_id, verdict,
                          phrase,
                          money: !!(money && money.checked)})});
  const j = await res.json();
  if(!j.ok){ msg.className='msg err'; msg.textContent = '✕ '+j.error; return; }
  msg.className='msg ok'; msg.textContent = '✓ '+j.message;
  document.getElementById('k'+i).classList.add('done');
  done++; draw_counts();
}
function draw_counts(){
  document.getElementById('count').textContent =
    `${ITEMS.length-done} open · ${done} ruled this session`;
  document.getElementById('prog').style.width = (done/ITEMS.length*100)+'%';
}
fetch('/api/items').then(r=>r.json()).then(j=>{
  ITEMS=j.items; draw();
  document.getElementById('build').textContent = 'build '+j.build;
});
</script>"""


class Handler(BaseHTTPRequestHandler):
    queue_path: Path
    proposals_path: Path | None
    dsn: str = DSN
    who: str = "human"

    def log_message(self, *a):    # one line per ruling is enough; requests are noise
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # NEVER CACHE. The page is generated from the module source, so a cached copy is a
        # copy of code that no longer exists -- and it looks exactly like an edit that did
        # not take effect.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/items":
            rows = items(self.queue_path, self.proposals_path, self.dsn)
            return self._send(200, json.dumps({"items": rows,
                                               "build": build_stamp()}).encode(),
                              "application/json")
        self._send(404, b"not found", "text/plain")

    def do_HEAD(self):
        """Answer HEAD like GET without a body, so health checks do not get a 501."""
        body = PAGE.encode() if self.path == "/" else b""
        self._send(200 if self.path == "/" else 404, b"", "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/api/decide":
            return self._send(404, b"not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        cid, sid = int(req["claim_id"]), int(req["subject_id"])
        verdict = req.get("verdict")
        try:
            if verdict == "yes":
                import psycopg
                with psycopg.connect(self.dsn) as c, c.cursor() as cur:
                    r = rm.store(cur, cid, sid, req.get("phrase", ""), self.who)
                    paid = rm.attribute_money(cur, cid, sid) if req.get("money") else 0
                    c.commit()
                rm.drop(self.queue_path, cid, sid, quiet=True)
                m = (f"stored {r['matched_text']!r} at {r['span_start']}..{r['span_end']}"
                     if r["stored"] else "already present")
                if paid:
                    m += f" · {paid} funding row(s) attributed"
                print(f"[server] YES  claim {cid} -> {sid} {r['subject_name']}")
            elif verdict == "no":
                rm.drop(self.queue_path, cid, sid, quiet=True)
                m = "dropped from the queue; nothing stored"
                print(f"[server] no   claim {cid} -> {sid}")
            else:
                m = "skipped; left in the queue"
            return self._send(200, json.dumps({"ok": True, "message": m}).encode(),
                              "application/json")
        except rm.Refused as e:
            return self._send(200, json.dumps({"ok": False, "error": str(e)}).encode(),
                              "application/json")
        except Exception as e:                       # a failed write must not look like a yes
            return self._send(200, json.dumps({"ok": False, "error": repr(e)}).encode(),
                              "application/json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", default=str(rm.QUEUE))
    ap.add_argument("--proposals", help="a batch of suggested verdicts to review")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--by", default=os.environ.get("USER", "human"))
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    Handler.queue_path = Path(a.queue)
    Handler.proposals_path = Path(a.proposals) if a.proposals else None
    Handler.dsn, Handler.who = a.dsn, a.by

    url = f"http://127.0.0.1:{a.port}/"
    # BIND BEFORE OPENING THE BROWSER. Previously this counted the queue with a full database
    # pass, printed, opened the browser, and only then bound the socket -- so the browser
    # raced a port with nothing listening, got connection-refused, and showed an error page
    # until the user reloaded by hand. It reads as "the server is very slow to start" when in
    # fact it was already up and the tab was stale.
    #
    # Once the constructor returns, the socket is listening: the kernel queues connections
    # in the accept backlog even before serve_forever() runs, so an early request waits
    # instead of failing.
    #
    # Localhost only. See the module docstring: this has no auth and should not acquire the
    # appearance of any.
    with ThreadingHTTPServer(("127.0.0.1", a.port), Handler) as srv:
        print(f"[server] listening on {url}   build {build_stamp()}, ruling as {a.by!r}")
        print(f"[server] (ctrl-c to stop)")
        if not a.no_open:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[server] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
