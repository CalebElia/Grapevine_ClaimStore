"""Environment configuration, with a hard rule: secrets are never printed.

Every accessor here returns a value to CODE, or reports only whether a value is
present. Nothing in this module writes a secret to stdout, to a log, or to an
exception message — a traceback that includes an API key ends up in a terminal
scrollback, a CI log, or a chat transcript, and from there it is effectively public.

`describe()` is the diagnostic: it reports set/not-set and a length, which is enough
to tell "I forgot to fill it in" apart from "I pasted it with a trailing newline"
without revealing the value.

Loading a .env:
    set -a; source .env; set +a        # shell
    python -m pipeline.config          # verify what is visible, safely
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).parent.parent

# name -> (required_for, hint)
KNOWN = {
    # Azure's v1 API (Aug 2025+) dropped the dated api-version parameter AND the
    # AzureOpenAI() client — the standard OpenAI SDK works against an Azure resource
    # by pointing base_url at it. These therefore use the SDK's own variable names,
    # which means OpenAI() picks them up with no arguments at all.
    "OPENAI_BASE_URL":  ("S6/S7", "https://<resource>.openai.azure.com/openai/v1/ — suffix required"),
    "OPENAI_API_KEY":   ("S6/S7", "Keys and Endpoint page, KEY 1"),
    # Named by pipeline ROLE, not by model, so changing model is a .env edit only.
    "GRAPEVINE_DEPLOYMENT_CLASSIFY":    ("S6 classifier", "DEPLOYMENT name, not model id"),
    "GRAPEVINE_DEPLOYMENT_EXTRACT":     ("S7 extraction", "DEPLOYMENT name, not model id"),
    "GRAPEVINE_DEPLOYMENT_EXTRACT_ALT": ("optional benchmark", "second deployment; may be blank"),
    "HF_TOKEN":         ("re-running diarization", "accept BOTH pyannote licences first"),
    "GRAPEVINE_DSN":    ("database access", "defaults to the local socket"),

    # Text path (pipeline/convert_document.py, parse_audit.py, convert_compare.py).
    # A SEPARATE Azure resource from OPENAI_BASE_URL above -- do not conflate the two
    # endpoints when debugging a 404.
    "CU_ENDPOINT":                  ("Content Understanding", "own resource, not the OpenAI one"),
    "CU_API_KEY":                   ("Content Understanding", "Keys and Endpoint page"),
    "CU_API_VERSION":               ("Content Understanding", "GA 2025-11-01; preview unlocks agentic mode"),
    "CU_ANALYZER_ID":               ("Content Understanding", "Document Layout Analyzer's id"),
    "GRAPEVINE_DEPLOYMENT_VISION":     ("Tier 3 vision census", "deployment must accept image input"),
    "GRAPEVINE_DEPLOYMENT_VISION_ALT": ("Tier 3 cross-check", "second vision deployment; may be blank"),
    "GRAPEVINE_VISION_API_VERSION":    ("Tier 3 vision census", "chat-completions version, not CU's"),
    "FIRECRAWL_API_KEY":               ("Tier 2 fourth-read cross-check", "Collin's account credits"),
}

OPTIONAL = {
    "GRAPEVINE_DEPLOYMENT_EXTRACT_ALT", "GRAPEVINE_DSN", "HF_TOKEN",
    # Text path — every one of these gates a single optional converter/audit arm,
    # not the pdfplumber baseline, so an unfilled value is a choice, not an error.
    "CU_ENDPOINT", "CU_API_KEY", "CU_API_VERSION", "CU_ANALYZER_ID",
    "GRAPEVINE_DEPLOYMENT_VISION", "GRAPEVINE_DEPLOYMENT_VISION_ALT",
    "GRAPEVINE_VISION_API_VERSION", "FIRECRAWL_API_KEY",
}


def load_dotenv(path: Path | None = None) -> int:
    """Read .env into os.environ if present. Returns how many names were set.

    Deliberately minimal — no dependency, no interpolation, no export of values
    anywhere but os.environ. Existing environment variables win, so a value set in
    the shell is never silently overridden by a stale file.
    """
    path = path or REPO / ".env"
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


def get(name: str, default: str | None = None) -> str | None:
    load_dotenv()
    return os.environ.get(name, default)


def require(name: str) -> str:
    """Fetch a required value. The error names the variable and never the value."""
    v = get(name)
    if not v:
        need, hint = KNOWN.get(name, ("", ""))
        raise RuntimeError(
            f"{name} is not set" + (f" (needed for {need}; {hint})" if need else "")
            + ".\n  Fix: cp .env.example .env && chmod 600 .env && $EDITOR .env"
            + "\n  Then: set -a; source .env; set +a"
        )
    return v


def describe() -> str:
    """Presence and length only. Never the value."""
    load_dotenv()
    lines = ["Environment (values are never displayed):"]
    for name, (need, hint) in KNOWN.items():
        v = os.environ.get(name)
        if v:
            # A trailing newline or stray quote is a common paste error and is
            # invisible in an editor — length makes it detectable without exposure.
            flag = "  <-- has leading/trailing whitespace" if v != v.strip() else ""
            lines.append(f"  ✓ {name:<34} set, {len(v)} chars{flag}")
        elif name in OPTIONAL:
            lines.append(f"  · {name:<34} not set (optional — {need})")
        else:
            lines.append(f"  ✗ {name:<34} NOT SET  ({need})")
    return "\n".join(lines)


def check_azure_base_url() -> str | None:
    """Omitting the /openai/v1/ suffix is easy and surfaces later as an opaque 404."""
    url = get("OPENAI_BASE_URL")
    if url and "YOUR-RESOURCE" not in url and not url.rstrip("/").endswith("/openai/v1"):
        return ("OPENAI_BASE_URL must end with /openai/v1/ — Azure's v1 API needs that "
                f"suffix or every call 404s. Got: …{url[-34:]}")
    return None


if __name__ == "__main__":
    # load_dotenv returns how many names it SET, which is zero both when the file is
    # missing and when the shell already exported everything in it. Reporting those
    # identically as "no .env found" is wrong and alarming — distinguish them.
    n = load_dotenv()
    env_path = REPO / ".env"
    if not env_path.exists():
        print(f"no .env at {env_path}\n")
    elif n:
        print(f".env loaded — set {n} name(s) not already in the environment\n")
    else:
        print(".env present; every name in it was already exported by the shell\n")
    print(describe())
    warn = check_azure_base_url()
    if warn:
        print(f"\n  ⚠ {warn}")
