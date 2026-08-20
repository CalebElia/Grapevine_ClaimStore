"""Vision-model figure extraction -- the expensive Tier 3 call, gated behind the Docling
classifier so it only runs on pictures worth the cost.

WHY XML, AND WHY THIS VARIANT. The prior pipeline's DESCRIBER_PROMPT
(docling-test/pipeline/2_enrich_images.py) used a free-text <data_points> bulleted list.
That's readable but not machine-parseable -- there is no way to programmatically check
"did it get the value right" without re-parsing prose. This version keeps the same XML
container (familiar, and prior evidence it works) but makes each point a typed,
attribute-based element, so a caller can verify accuracy without an LLM re-reading the
LLM's own output.

THE ANTI-HALLUCINATION INSTRUCTION IS NOT DECORATIVE. Docling's GHG chart extraction
produced six columns identical to one decimal place across six years -- a plausible-
looking pattern-fill, not a read. A vision call making the same mistake is worse, not
better, because its prose is more convincing. So the prompt explicitly demands an
illegible/low-confidence escape hatch, and confidence is a required attribute per point,
not an afterthought.
"""
from __future__ import annotations

import argparse
import base64
import re
import time
from pathlib import Path

XML_PROMPT = """You are extracting data from a chart or dashboard image for a database \
whose entries will be cited as evidence in formal analysis. Fabricating a plausible-\
looking number is worse than reporting nothing -- a wrong number silently corrupts \
everything built on top of it.

Extract ONLY values you can actually read on this image. If a number is too small, \
blurry, low-contrast, or ambiguous to read with confidence, mark it illegible rather \
than guessing or interpolating a smooth pattern from nearby values. Do not infer a value \
from a trend line -- report only what is printed.

FIRST DECIDE WHETHER THIS FIGURE CARRIES SUBJECT-SPECIFIC DATA AT ALL. Some images in a report are ornamental: a stock photograph, a decorative graphic, or a screenshot of a third-party TOOL shown so readers know the tool exists. A screenshot of a software interface with no values particular to this document's subject is ornamental in exactly the way a stock photo of a ribbon-cutting is ornamental -- it illustrates, it does not report. Mark those <relevance>ornamental</relevance> and extract no data points; a generic interface's menu labels are not findings. Mark <relevance>substantive</relevance> only when the figure presents data about the subject of this document.

Wrap your entire response in exactly this XML structure, with no text outside it:

<figure_description>
  <relevance>substantive|ornamental</relevance>
  <relevance_reason>one sentence: what the image shows, and whether any value in it is specific to this document's subject</relevance_reason>
  <type>bar_chart|line_chart|pie_chart|scatter_chart|dashboard_callout|photo|other</type>
  <title>the chart's own title, verbatim if visible, else empty</title>
  <summary>1-2 sentence factual summary of what the figure shows -- no interpretation</summary>
  <data_points>
    <point label="..." value="..." unit="..." confidence="high|medium|low"/>
  </data_points>
  <illegible_regions>describe anything present but not readable with confidence, or "none"</illegible_regions>
</figure_description>"""

# The prior pipeline's format, unchanged, for a direct before/after comparison.
XML_PROMPT_LEGACY = """\
You are an expert data analyst. Extract all relevant information from this visual asset.
Summarize the main takeaway and list the specific data points.
Do not use Markdown formatting inside the tags.
Wrap your entire response in this exact XML structure:

<figure_description>
  <type>[Chart/Graph/Map/Table]</type>
  <title>[Infer a title if none exists]</title>
  <summary>[1-2 sentence summary of the visual]</summary>
  <data_points>
    - [Data point 1]
    - [Data point 2]
  </data_points>
</figure_description>"""


def analyze(image_path: Path, deployment_env: str, prompt: str = XML_PROMPT,
           max_completion_tokens: int = 3000) -> dict:
    """One vision call. max_completion_tokens is generous on purpose -- measured earlier
    that these are reasoning models: a 20-token budget on a trivial image returned
    finish_reason=length with EMPTY visible content, all of it spent on hidden reasoning.
    """
    from pipeline import config
    from openai import OpenAI

    depl = config.get(deployment_env)
    client = OpenAI(base_url=config.get("OPENAI_BASE_URL"), api_key=config.get("OPENAI_API_KEY"))
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    ext = image_path.suffix.lstrip(".").lower() or "png"

    t0 = time.time()
    resp = client.chat.completions.create(
        model=depl,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
        ]}],
        max_completion_tokens=max_completion_tokens,
    )
    choice = resp.choices[0]
    return {
        "deployment": depl, "seconds": round(time.time() - t0, 1),
        "finish_reason": choice.finish_reason,
        "reasoning_tokens": resp.usage.completion_tokens_details.reasoning_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "text": choice.message.content or "",
    }


def parse_relevance(xml_text: str) -> dict:
    """{"relevance": substantive|ornamental|unknown, "reason": str}.

    Absent element -> "unknown", never "substantive". Output from an older prompt has not
    been triaged, and treating untriaged as passed is how an ornamental figure's menu
    labels end up stored as data.
    """
    rel = re.search(r"<relevance>\s*(\w+)\s*</relevance>", xml_text, re.I)
    why = re.search(r"<relevance_reason>(.*?)</relevance_reason>", xml_text, re.I | re.S)
    val = (rel.group(1).lower() if rel else "unknown")
    if val not in ("substantive", "ornamental"):
        val = "unknown"
    return {"relevance": val,
            "reason": " ".join(why.group(1).split()) if why else ""}


def parse_points(xml_text: str) -> list[dict]:
    """Pull <point .../> attributes out without a full XML parser -- model output is
    not always well-formed enough for one, and a regex degrades gracefully.
    """
    out = []
    for m in re.finditer(r"<point\b([^/>]*)/?>", xml_text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if attrs:
            out.append(attrs)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="vision-model figure extraction")
    ap.add_argument("--image", required=True)
    ap.add_argument("--deployment-env", default="GRAPEVINE_DEPLOYMENT_VISION")
    ap.add_argument("--legacy-prompt", action="store_true")
    a = ap.parse_args()
    r = analyze(Path(a.image), a.deployment_env,
               XML_PROMPT_LEGACY if a.legacy_prompt else XML_PROMPT)
    print(f"[{r['deployment']}] {r['seconds']}s finish={r['finish_reason']} "
          f"reasoning={r['reasoning_tokens']} completion={r['completion_tokens']}")
    print(r["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
