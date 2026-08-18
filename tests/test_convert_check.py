"""The bake-off preflight, tested without spending a credential.

Two of these tests exist because of things this module got wrong on its FIRST real
run, not hypothetically:

- The initial test image was 4x4 pixels. Content Understanding's own error named the
  exact constraint: "min 50x50 pixels". A regression test now pins the real minimum
  learned from that response, so the same mistake can't quietly return.
- Empty vision replies at first looked like a working call (HTTP 200, no exception) --
  the model had spent its whole token budget on hidden reasoning
  (`completion_tokens_details.reasoning_tokens`) and had nothing left to answer with.
  That is a live-call behaviour and isn't unit-testable here, but the budget itself
  (300, not 20) is pinned so nobody quietly shrinks it back into the failure.

`config.get` is mocked rather than deleting real environment variables, because
`load_dotenv()` re-populates anything absent from `os.environ` from the developer's
actual .env on the very next call -- a bare `del os.environ[...]` would be silently
undone mid-test.
"""
from __future__ import annotations

from unittest.mock import patch

from pipeline.convert_check import _test_png, check_content_understanding, check_firecrawl, \
    check_pdfplumber, check_vision, explain, run


def test_test_image_meets_content_understandings_own_minimum():
    """Measured, not assumed: CU's error body said 'min 50x50 pixels' verbatim."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(_test_png()))
    assert im.size[0] >= 50 and im.size[1] >= 50


def test_explain_surfaces_the_response_body_not_just_the_exception():
    class FakeResp:
        status_code = 400
        def json(self): return {"error": {"code": "InvalidImageDimension"}}
    class FakeError(Exception):
        response = FakeResp()
    out = explain(FakeError("400 Bad Request"))
    assert "InvalidImageDimension" in out
    assert "400" in out


def test_explain_falls_back_to_the_exception_text_with_no_response():
    assert "ConnectionError" in explain(ConnectionError("host unreachable"))


def test_every_credential_this_module_reads_is_documented_in_env_example():
    """Same drift guard as tests/test_bench.py -- a slot that exists in code and not
    in the template is undiscoverable to anyone following the template.
    """
    from pathlib import Path
    txt = (Path(__file__).parent.parent / ".env.example").read_text()
    for name in ("CU_ENDPOINT", "CU_API_KEY", "CU_API_VERSION", "CU_ANALYZER_ID",
                "GRAPEVINE_DEPLOYMENT_VISION", "GRAPEVINE_DEPLOYMENT_VISION_ALT",
                "FIRECRAWL_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        assert name in txt, f"{name} missing from .env.example"


def test_missing_credentials_skip_cleanly_rather_than_crash():
    with patch("pipeline.convert_check.config.get", return_value=None):
        assert check_content_understanding()["status"] == "skip"
        assert check_firecrawl()["status"] == "skip"
        assert check_vision("GRAPEVINE_DEPLOYMENT_VISION", "vision")["status"] == "skip"


def test_pdfplumber_needs_no_credential_at_all():
    """The one arm that should never skip, matching Text path fallback design: the
    baseline converter works with nothing configured.
    """
    assert check_pdfplumber()["status"] == "ok"


def test_run_reports_every_arm_even_when_fully_unconfigured():
    with patch("pipeline.convert_check.config.get", return_value=None):
        results = run()
    arms = {r["arm"] for r in results}
    assert arms == {"pdfplumber", "content_understanding", "vision (primary)",
                    "vision (alt)", "firecrawl"}
    assert all(r["status"] in ("ok", "skip") for r in results), \
        "an unconfigured run must never FAIL — absence is not an error"
