"""Validate every configured text-path arm before the real bake-off.

WHY THIS EXISTS. Same reasoning as pipeline/bench/preflight.py for the ASR vendors: a
typo'd key, a wrong deployment name, or a region mismatch is cheap to find in ten seconds
and expensive to find at page 20 of a real run. Four independent services means four
independent ways to be misconfigured, and this catches all of them in one pass for a
fraction of a cent.

Each check is the SMALLEST real call that proves the credential and the endpoint shape are
both correct -- not a documents-page fetch, not a real PDF. A one-pixel PNG round-trips
through Content Understanding and both vision deployments in under a second and costs
nothing worth measuring.

A missing credential SKIPS that arm. Nothing fails, matching the ASR bench convention --
you may be testing three of four arms today and that is a normal, not a broken, state.

Usage:
    python -m pipeline.convert_check
"""
from __future__ import annotations

import base64
import io
import json
import time

from pipeline import config

# The smallest real image every arm gets tested against: a solid white 4x4 PNG. Big
# enough that no service special-cases it as empty; small enough that the request body
# is a few hundred bytes.
def _test_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    return buf.getvalue()


def explain(e: Exception) -> str:
    """Surface the API's OWN error body. Copied from bench/preflight.py -- the response
    body is where every one of these services says exactly what it rejected, and
    discarding it turns a ten-second fix into a documentation hunt.
    """
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = json.dumps(resp.json(), separators=(",", ":"))
        except Exception:
            body = (getattr(resp, "text", "") or "")
    body = " ".join(body.split())
    head = f"{type(e).__name__}"
    if resp is not None:
        head += f" {getattr(resp, 'status_code', '')}"
    return f"{head}: {body[:400]}" if body else f"{head}: {' '.join(str(e).split())[:300]}"


def check_pdfplumber() -> dict:
    """Local, free, no credential. Confirms the import and the version we already pinned."""
    try:
        import pdfplumber
        import importlib.metadata as m
        return {"arm": "pdfplumber", "status": "ok",
                "detail": f"v{m.version('pdfplumber')}, local — no credential needed"}
    except Exception as e:
        return {"arm": "pdfplumber", "status": "FAIL", "detail": explain(e)}


def check_content_understanding() -> dict:
    name = "content_understanding"
    endpoint, key, ver, analyzer = (config.get("CU_ENDPOINT"), config.get("CU_API_KEY"),
                                    config.get("CU_API_VERSION"), config.get("CU_ANALYZER_ID"))
    if not (endpoint and key and ver and analyzer):
        return {"arm": name, "status": "skip", "detail": "CU_ENDPOINT/CU_API_KEY not set"}
    import httpx
    t0 = time.time()
    try:
        url = f"{endpoint.rstrip('/')}/contentunderstanding/analyzers/{analyzer}:analyzeBinaryInline"
        r = httpx.post(url, params={"api-version": ver},
                       headers={"Content-Type": "application/octet-stream",
                                "Ocp-Apim-Subscription-Key": key},
                       content=_test_png(), timeout=30)
        r.raise_for_status()
        d = r.json()
        pages = len(d.get("result", {}).get("contents", []) or d.get("contents", []) or [])
        return {"arm": name, "status": "ok",
                "detail": f"{r.elapsed.total_seconds():.1f}s, analyzer={analyzer}, "
                          f"HTTP {r.status_code}, {pages} content block(s) back"}
    except Exception as e:
        return {"arm": name, "status": "FAIL", "detail": explain(e)}


def check_vision(deployment_env: str, label: str) -> dict:
    depl = config.get(deployment_env)
    base, key = config.get("OPENAI_BASE_URL"), config.get("OPENAI_API_KEY")
    if not depl:
        return {"arm": label, "status": "skip", "detail": f"{deployment_env} not set"}
    if not (base and key):
        return {"arm": label, "status": "skip", "detail": "OPENAI_BASE_URL/OPENAI_API_KEY not set"}
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base, api_key=key)
        b64 = base64.b64encode(_test_png()).decode()
        t0 = time.time()
        resp = client.chat.completions.create(
            model=depl,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What color is this image? One word."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            max_completion_tokens=300,  # reasoning models spend some of this on hidden reasoning
        )
        answer = (resp.choices[0].message.content or "").strip()
        return {"arm": label, "status": "ok",
                "detail": f"{time.time()-t0:.1f}s, deployment={depl}, replied: {answer[:40]!r}"}
    except Exception as e:
        return {"arm": label, "status": "FAIL", "detail": explain(e)}


def check_firecrawl() -> dict:
    name = "firecrawl"
    key = config.get("FIRECRAWL_API_KEY")
    if not key:
        return {"arm": name, "status": "skip", "detail": "FIRECRAWL_API_KEY not set"}
    import httpx
    t0 = time.time()
    try:
        # A stable, tiny, always-public page -- this checks auth and endpoint shape
        # only, not document parsing (that's proven on the real PDFs separately).
        r = httpx.post("https://api.firecrawl.dev/v2/scrape",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"url": "https://example.com", "formats": ["markdown"]},
                       timeout=30)
        r.raise_for_status()
        d = r.json()
        ok = bool(d.get("success", True))
        return {"arm": name, "status": "ok" if ok else "FAIL",
                "detail": f"{time.time()-t0:.1f}s, HTTP {r.status_code}, success={ok}"}
    except Exception as e:
        return {"arm": name, "status": "FAIL", "detail": explain(e)}


def run() -> list[dict]:
    checks = [check_pdfplumber, check_content_understanding,
             lambda: check_vision("GRAPEVINE_DEPLOYMENT_VISION", "vision (primary)"),
             lambda: check_vision("GRAPEVINE_DEPLOYMENT_VISION_ALT", "vision (alt)"),
             check_firecrawl]
    results = [c() for c in checks]
    print(f"\n{'arm':<24}{'status':<8}detail")
    print("-" * 100)
    for r in results:
        mark = {"ok": "OK", "skip": "skip", "FAIL": "FAIL"}[r["status"]]
        print(f"{r['arm']:<24}{mark:<8}{r['detail'][:72]}")
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    print(f"\n{n_ok} ok, {n_fail} failed, {n_skip} skipped (no credential)")
    return results


def main() -> int:
    results = run()
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
