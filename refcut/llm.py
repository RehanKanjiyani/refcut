"""Optional automatic semantic pass via any OpenAI-compatible VLM API.

Configure with env vars:
    REFCUT_API_BASE  e.g. https://api.openai.com/v1  (or any compatible host)
    REFCUT_API_KEY
    REFCUT_MODEL     e.g. a vision-capable model id

If unset, refcut stays in manual mode: analysis_prompt.md is meant to be
answered by any capable VLM (or a human editor), and the reply is saved as
analysis.json before running `refcut blueprint`.
"""

import base64
import json
import os
import re
import urllib.request
from pathlib import Path


def configured():
    return all(os.environ.get(k) for k in ("REFCUT_API_BASE", "REFCUT_API_KEY", "REFCUT_MODEL"))


def _data_url(jpeg_bytes):
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()


def _extract_json(text):
    m = re.search(r"\{", text)
    if not m:
        raise ValueError("no JSON object found in model reply")
    depth = 0
    for i in range(m.start(), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[m.start(): i + 1])
    raise ValueError("unbalanced JSON in model reply")


def analyze_core(prompt, image_jpegs):
    """Semantic pass against an OpenAI-compatible VLM.

    `prompt` is the analysis-prompt text; `image_jpegs` is a list of JPEG
    bytes (contact sheet, optionally key frames). Returns the analysis dict
    (and does NOT write anything to disk).
    """
    content = [{"type": "text", "text": prompt}]
    for jpg in image_jpegs:
        content.append({"type": "image_url", "image_url": {"url": _data_url(jpg)}})
    body = {
        "model": os.environ["REFCUT_MODEL"],
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 8000,
    }
    req = urllib.request.Request(
        os.environ["REFCUT_API_BASE"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["REFCUT_API_KEY"],
        },
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        reply = json.loads(r.read().decode())
    text = reply["choices"][0]["message"]["content"]
    return _extract_json(text)


def analyze(out_dir):
    """CLI wrapper: reads analysis_prompt.md + contact_sheet.jpg from disk,
    runs the semantic pass, writes analysis.json."""
    out_dir = Path(out_dir)
    prompt = (out_dir / "analysis_prompt.md").read_text(encoding="utf-8")
    sheet = out_dir / "contact_sheet.jpg"
    images = [sheet.read_bytes()] if sheet.exists() else []
    analysis = analyze_core(prompt, images)
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2), encoding="utf-8")
    return analysis
