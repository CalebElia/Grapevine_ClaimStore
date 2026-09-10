"""A local page for ruling on award_status. See pipeline/award_status.py for the reasoning.

WHY A PAGE FOR TWO ROWS. Because the two are the ones that matter. Migration 012 exists
because a playbook recommending a clawed-back grant is the spurious-transferability failure in
its purest form, and these are the only two awards in the corpus whose fate the documents
actually report. Getting them right is the whole point of the column.

The 20 verb-derived rows are shown too, but as a batch to confirm rather than decide: their
evidence is a word inside the claim's own sentence, which string matching can read.
"""
from __future__ import annotations

import argparse
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pipeline import award_status as A
from pipeline.review_server import build_stamp, context

DSN = A.DSN

PAGE = r"""<!doctype html><meta charset=utf-8><title>Award status</title>
<style>
:root{--bg:#fbfbfa;--fg:#1c1c1a;--dim:#6b6b66;--line:#e0e0dc;--card:#fff;--acc:#2f6fb0;
      --warn:#8a6d1f;--bad:#b03030;--ok:#1a7f4b;--hl:#ffe9a8}
@media(prefers-color-scheme:dark){:root{--bg:#16171a;--fg:#e8e8e4;--dim:#9a9a94;--line:#2e3035;
      --card:#1e2024;--hl:#5a4a12;--acc:#7fb3e8;--ok:#4ec27f;--bad:#e07a7a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
       padding:.7rem 1.2rem;display:flex;gap:1rem;align-items:baseline;z-index:5;flex-wrap:wrap}
h1{font-size:1rem;margin:0;font-weight:600}
h2{font-size:.95rem;margin:1.6rem 0 .2rem;padding:0 1.2rem}
.sub{padding:0 1.2rem;color:var(--dim);font-size:.86rem;margin-bottom:.7rem;max-width:70ch}
main{padding:0 1.2rem 3rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem;
      margin-bottom:1rem;max-width:75ch}
.card.done{opacity:.45}
.meta{font-size:.78rem;color:var(--dim);display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.5rem}
.tag{border:1px solid var(--line);border-radius:99px;padding:.05rem .5rem}
.amt{font-size:1.3rem;font-weight:600}
.claim{margin:.5rem 0;padding:.55rem .7rem;border-left:3px solid var(--acc);
       background:color-mix(in srgb,var(--acc) 6%,transparent);border-radius:0 6px 6px 0}
mark{background:var(--hl);color:inherit;border-radius:2px;padding:0 .1em}
.ev{border:1px dashed var(--line);border-radius:7px;padding:.5rem .6rem;margin:.4rem 0;
    font-size:.9rem;cursor:pointer}
.ev:hover{border-color:var(--acc)}
.ev.sel{border-style:solid;border-color:var(--acc);
        background:color-mix(in srgb,var(--acc) 8%,transparent)}
.ev .d{font-size:.75rem;color:var(--dim)}
.row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.6rem}
select,input[type=text],input[type=date]{font:inherit;font-size:.9rem;padding:.35rem .5rem;
     border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
input[type=text]{flex:1;min-width:16rem}
button{font:inherit;font-size:.9rem;padding:.4rem .9rem;border-radius:7px;cursor:pointer;
       border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button.go{border-color:var(--ok);color:var(--ok)}
button.big{font-size:.95rem;padding:.5rem 1.1rem}
.msg{font-size:.83rem;margin-top:.45rem;min-height:1.1em}
.ok{color:var(--ok)}.err{color:var(--bad)}
table{border-collapse:collapse;font-size:.86rem;width:100%;max-width:75ch}
td,th{border-bottom:1px solid var(--line);padding:.35rem .5rem;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500}
.n{text-align:right;white-space:nowrap}
</style>
<header>
  <h1>Award status</h1><span class=meta id=count></span><span class=meta id=build></span>
</header>
<main>
  <h2>Needs your judgement</h2>
  <div class=sub>The status is in a <b>different sentence</b> than the amount, so attaching it
    means judging that “this award” refers to the one named earlier. Nearby sentences are
    offered by proximity and <b>some do not belong</b> — click the one that is really about
    this award, or none.</div>
  <div id=reversals></div>

  <h2>Proposed from the claim’s own words</h2>
  <div class=sub>The evidence is a verb inside the quoted sentence, so string matching can read
    it. Confirm as a batch, or change any row first.</div>
  <div id=verbs></div>

  <h2>Left as unknown</h2>
  <div class=sub id=silentnote></div>
</main>
<script>
const esc=s=>(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money=v=>v==null?'—':'$'+v.toLocaleString(undefined,{maximumFractionDigits:0});
function hl(t,w){if(!w)return esc(t);
  const p=t.split(new RegExp('('+w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'));
  return p.map((x,i)=>i%2?'<mark>'+esc(x)+'</mark>':esc(x)).join('');}
let D={}, STATUSES=[], done=0;

function statusSelect(id,sel){
  return `<select id="s${id}">`+STATUSES.map(s=>
    `<option value="${s}"${s===sel?' selected':''}>${s}</option>`).join('')+`</select>`;}

function drawReversals(){
  document.getElementById('reversals').innerHTML = D.reversal.map(r=>`
    <div class=card id="k${r.fiscal_id}">
      <div class=meta><span class=tag>fiscal ${r.fiscal_id}</span>
        <span class=tag>${esc(r.document||'')}</span>
        <span class=tag>currently: ${esc(r.current_status)}</span></div>
      <div class=amt>${money(r.amount)} <span style="font-size:.9rem;font-weight:400">
        ${esc(r.funder||'')}</span></div>
      <div class=claim>${esc(r.verbatim)}</div>
      ${r.verb_would_have_said?`<div class=meta>a verb rule alone would have said
        <b>${r.verb_would_have_said}</b> — which is why this is not automated</div>`:''}
      <div style="font-size:.85rem;color:var(--dim);margin-top:.6rem">
        nearby sentences, closest first:</div>
      ${r.reversal_claims.map((n,i)=>`
        <div class="ev" id="e${r.fiscal_id}_${i}"
             onclick="pick(${r.fiscal_id},${i})">${esc(n.verbatim)}
          <div class=d>claim ${n.claim_id} · ${n.distance} characters away</div></div>`).join('')}
      <div class=row>
        ${statusSelect(r.fiscal_id,'on_hold')}
        <input type=date id="d${r.fiscal_id}" value="${r.period_end||''}" title="status as of">
        <button class="go" onclick="apply(${r.fiscal_id})">Apply</button>
      </div>
      <div class=row><input type=text id="r${r.fiscal_id}"
        placeholder="status_change_reason — quote the sentence that says so"></div>
      <div class=msg id="m${r.fiscal_id}"></div>
    </div>`).join('');
}

function pick(fid,i){
  const r=D.reversal.find(x=>x.fiscal_id===fid);
  r.reversal_claims.forEach((_,j)=>
    document.getElementById(`e${fid}_${j}`).classList.toggle('sel',i===j));
  document.getElementById('r'+fid).value=r.reversal_claims[i].verbatim;
  const t=r.reversal_claims[i].verbatim.toLowerCase();
  const guess=t.includes('disput')?'disputed':t.includes('on hold')?'on_hold':
              t.includes('terminat')?'terminated':null;
  if(guess) document.getElementById('s'+fid).value=guess;
}

function drawVerbs(){
  document.getElementById('verbs').innerHTML=`
    <div class=card style="max-width:none">
      <table><tr><th>fiscal</th><th class=n>amount</th><th>funder</th>
        <th>evidence in the claim</th><th>proposed</th></tr>
      ${D.verb.map(r=>`<tr><td>${r.fiscal_id}</td><td class=n>${money(r.amount)}</td>
        <td>${esc((r.funder||'').slice(0,26))}</td>
        <td>${hl(r.verbatim.slice(0,86),r.evidence)}</td>
        <td>${statusSelect(r.fiscal_id,r.proposed)}</td></tr>`).join('')}
      </table>
      <div class=row><button class="go big" onclick="applyAll()">
        Apply all ${D.verb.length}</button></div>
      <div class=msg id=mbatch></div>
    </div>`;
}

async function post(body){
  const res=await fetch('/api/decide',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return res.json();}

async function apply(fid){
  const msg=document.getElementById('m'+fid);
  const reason=document.getElementById('r'+fid).value;
  msg.className='msg';msg.textContent='…';
  const j=await post({fiscal_id:fid,status:document.getElementById('s'+fid).value,
    reason,as_of:document.getElementById('d'+fid).value});
  if(!j.ok){msg.className='msg err';msg.textContent='✕ '+j.error;return;}
  msg.className='msg ok';msg.textContent='✓ '+j.message;
  document.getElementById('k'+fid).classList.add('done');done++;counts();}

async function applyAll(){
  const msg=document.getElementById('mbatch');msg.className='msg';msg.textContent='…';
  let n=0,bad=[];
  for(const r of D.verb){
    const j=await post({fiscal_id:r.fiscal_id,
      status:document.getElementById('s'+r.fiscal_id).value,
      reason:r.verbatim,as_of:r.period_end});
    if(j.ok){n++;done++;}else bad.push(r.fiscal_id+': '+j.error);}
  msg.className=bad.length?'msg err':'msg ok';
  msg.textContent=(bad.length?'✕ ':'✓ ')+`${n} applied`+(bad.length?'; '+bad.join('; '):'');
  counts();}

function counts(){document.getElementById('count').textContent=
  `${D.reversal.length} to judge · ${D.verb.length} proposed · ${D.silent.length} left unknown`
  + (done?` · ${done} applied`:'');}

fetch('/api/items').then(r=>r.json()).then(j=>{
  D=j;STATUSES=j.statuses;
  document.getElementById('build').textContent='build '+j.build;
  document.getElementById('silentnote').textContent=
    `${j.silent.length} rows state an amount and nothing about its fate. 'unknown' is the true `
    +`answer for those and they are not touched.`;
  drawReversals();drawVerbs();counts();});
</script>"""


class Handler(BaseHTTPRequestHandler):
    dsn: str = DSN
    who: str = "human"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        import psycopg
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/items":
            with psycopg.connect(self.dsn) as c, c.cursor() as cur:
                g = A.classify(cur)
                cur.execute("SELECT term FROM vocabulary_terms "
                            "WHERE vocabulary='award_status' ORDER BY term")
                g["statuses"] = [r[0] for r in cur.fetchall()]
            g["build"] = build_stamp()
            return self._send(200, json.dumps(g).encode(), "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        import psycopg
        if self.path != "/api/decide":
            return self._send(404, b"not found", "text/plain")
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            with psycopg.connect(self.dsn) as c, c.cursor() as cur:
                r = A.apply_status(cur, int(req["fiscal_id"]), req["status"],
                                   req.get("reason"), req.get("as_of") or None, self.who)
                c.commit()
            print(f"[award] fiscal {r['fiscal_id']} -> {r['status']}")
            return self._send(200, json.dumps(
                {"ok": True, "message": f"{r['status']}"}).encode(), "application/json")
        except Exception as e:
            return self._send(200, json.dumps({"ok": False, "error": str(e)}).encode(),
                              "application/json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--by", default=os.environ.get("USER", "human"))
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    Handler.dsn, Handler.who = a.dsn, a.by
    url = f"http://127.0.0.1:{a.port}/"
    # Bind before opening the browser; see review_server for why.
    with ThreadingHTTPServer(("127.0.0.1", a.port), Handler) as srv:
        print(f"[award] listening on {url}   build {build_stamp()}, ruling as {a.by!r}")
        if not a.no_open:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[award] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
