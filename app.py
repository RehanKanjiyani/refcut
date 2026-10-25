"""refcut web app — reference video -> measured evidence -> editing blueprint.

Runs locally (`python app.py`) and on Vercel's Python runtime (root `app`
variable + flask in requirements.txt = framework preset, all routes served).

Endpoints:
  GET  /               single-page UI (public/index.html)
  GET  /api/ping       health
  POST /api/analyze    {url} | {demo:true} [, sample_fps] -> evidence +
                       contact sheet (b64) + analysis prompt; runs the VLM
                       semantic pass automatically when REFCUT_* env is set
  POST /api/blueprint  {evidence, analysis?} -> blueprint.json + markdown
"""

import base64
import io
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from refcut import __version__
from refcut import blueprint as bp_mod
from refcut import evidence as evidence_mod
from refcut import llm
from refcut import probe

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 80 * 1024 * 1024
MAX_DURATION_S = 120.0

app = Flask(__name__)


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.get("/")
def index():
    return send_from_directory(ROOT / "public", "index.html")


@app.get("/api/ping")
def ping():
    return _cors(jsonify(ok=True, version=__version__))


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return _cors(jsonify(ok=True))
    body = request.get_json(silent=True) or {}
    sample_fps = min(max(float(body.get("sample_fps", 4.0)), 0.5), 8.0)
    try:
        src = _resolve_source(body)
    except ValueError as e:
        return _cors(jsonify(error=str(e))), 400

    try:
        meta = probe.probe(src)
        if meta["duration"] > MAX_DURATION_S:
            return _cors(jsonify(error=(
                f"video is {meta['duration']:.0f}s; the web limit is "
                f"{MAX_DURATION_S:.0f}s — trim the clip or use the CLI"))), 400
        evidence, sheet = evidence_mod.build(src, sample_fps=sample_fps)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - report analysis failures to the client
        return _cors(jsonify(error=f"analysis failed: {type(e).__name__}: {e}")), 500

    if not isinstance(src, (str, Path)):
        evidence["video"]["path"] = body.get("url") or "upload"

    result = {
        "evidence": evidence,
        "prompt": evidence_mod.render_prompt(evidence),
        "contact_sheet_jpeg_b64":
            base64.b64encode(sheet).decode() if sheet else None,
    }

    if llm.configured():
        images = [sheet] if sheet else []
        try:
            result["analysis"] = llm.analyze_core(result["prompt"], images)
            result["auto_analyzed"] = True
        except Exception as e:  # noqa: BLE001
            result["auto_analyzed"] = False
            result["auto_error"] = f"{type(e).__name__}: {e}"
    return _cors(jsonify(result))


@app.route("/api/blueprint", methods=["POST", "OPTIONS"])
def compile_blueprint():
    if request.method == "OPTIONS":
        return _cors(jsonify(ok=True))
    body = request.get_json(silent=True) or {}
    evidence = body.get("evidence")
    if not evidence:
        return _cors(jsonify(error="evidence is required")), 400
    try:
        bp = bp_mod.build(evidence, body.get("analysis"))
        bp_mod._validate(bp)
    except Exception as e:  # noqa: BLE001
        return _cors(jsonify(error=f"blueprint failed: {type(e).__name__}: {e}")), 500
    return _cors(jsonify({"blueprint": bp, "markdown": bp_mod.to_markdown(bp)}))


def _resolve_source(body):
    if body.get("demo"):
        fixture = ROOT / "fixtures" / "test_reference.mp4"
        if fixture.exists():
            return str(fixture)
        raise ValueError("demo fixture is not bundled in this deployment")
    url = (body.get("url") or "").strip()
    if not url:
        raise ValueError('provide {"url": "<direct video url>"} or {"demo": true}')
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "refcut/" + __version__})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_BYTES + 1)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not download video: {type(e).__name__}: {e}")
    if len(data) > MAX_BYTES:
        raise ValueError(f"video exceeds the {MAX_BYTES // (1024 * 1024)}MB web limit")
    if not data:
        raise ValueError("downloaded file is empty")
    return io.BytesIO(data)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5577, threaded=True)
