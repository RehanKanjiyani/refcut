# refcut

**Reference video in → timeline-level editing blueprint out.**

AI-assisted deconstruction of reference edits: frame-accurate measured
evidence + a VLM semantic pass, compiled into a machine-executable editing
blueprint. Prototype 1 of an editing system whose purpose is to create
extremely creative, reference-driven video edits.

```
REFERENCE VIDEO
   │
   ├─► MEASURED EVIDENCE      (signal pass, frame-accurate — PyAV + OpenCV)
   │      cuts · shot spans · cumulative zoom + scale curves · punch-ins
   │      shake · pan · blur · flashes · onsets · bass impacts · BPM
   │      beat grid · silences · cut-to-beat sync · per-shot color
   │
   ├─► SEMANTIC ANALYSIS      (any VLM, guided by the generated prompt)
   │      transition naming · footage description · text · SFX identity
   │      creative intent · style summary · recurring patterns
   │
   └─► EDITING BLUEPRINT      (blueprint.json + human-readable blueprint.md)
          per-shot: timing / speed curves / transforms / effects /
          transitions / audio ops / color — every value tagged
          measured vs inferred
```

## Web app (Vercel-ready)

One Flask app serves both the UI and the API. Deploy with one click:

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FRehanKanjiyani%2Frefcut)

Or with the CLI:

```bash
npm i -g vercel
vercel login
vercel --prod
```

After deploying:

1. Open the site, paste a **direct video URL** (≤80 MB, ≤120 s) or click
   **Use demo clip**.
2. **Analyze** — the server measures everything (cuts, zoom curves, shake,
   flashes, beats, BPM, sync) and returns the evidence + a ready-made
   analysis prompt.
3. Run the **semantic pass**:
   - *automatic* — set env vars on the Vercel project (Settings →
     Environment Variables): `REFCUT_API_BASE` (OpenAI-compatible),
     `REFCUT_API_KEY`, `REFCUT_MODEL` (vision-capable). Redeploy; the
     analysis then happens server-side automatically.
   - *manual* — copy the prompt, send it with the contact sheet to any
     vision model, paste the JSON reply.
4. **Compile blueprint** — download `blueprint.json` / `blueprint.md`.

Run it locally instead:

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5577
```

### API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/ping` | — | `{ok, version}` |
| `POST /api/analyze` | `{"url": "..."}` or `{"demo": true}`, optional `"sample_fps": 4.0` | `{evidence, prompt, contact_sheet_jpeg_b64, analysis?}` |
| `POST /api/blueprint` | `{"evidence": …, "analysis": …}` (analysis optional) | `{blueprint, markdown}` |

## CLI (same engine, no size limits)

```bash
pip install -r requirements.txt

python -m refcut analyze path/to/reference.mp4
#   -> refcut_out/reference/evidence.json          measured, frame-accurate
#   -> refcut_out/reference/contact_sheet.jpg      visual index for the VLM
#   -> refcut_out/reference/samples/               full-res stills, 4 fps
#   -> refcut_out/reference/analysis_prompt.md     ready-made VLM task

python -m refcut blueprint refcut_out/reference
#   -> blueprint.json   (validated against schema/blueprint.schema.json)
#   -> blueprint.md     (readable timeline, editor's notation)
```

Self-test on the synthetic fixture (needs ffmpeg locally, fixture generation
only):

```bash
python tools/make_test_reference.py     # builds fixtures/test_reference.mp4
python -m refcut analyze fixtures/test_reference.mp4
```

A complete worked example lives in [`examples/test_reference/`](examples/test_reference/):
the measured evidence, the semantic analysis, and the final blueprint for the
bundled demo clip.

## Design decisions

**Two knowledge layers.** Signal processing owns *when* (frame-accurate cuts,
zoom curves, beats); the VLM owns *what it means* (transition names, footage
descriptions, intent). The VLM is forbidden from inventing timestamps — the
measured skeleton is authoritative, so hallucinated timelines can't happen.

**Style separated from content.** Every shot's `content.footage_required`
describes what footage the slot needs; everything else (pacing, transforms,
effects, transitions, audio ops) is craft. The same blueprint is applicable
to entirely different footage — that is prototype 2.

**Provenance.** Every blueprint value is tagged measured or inferred, and
uncertain semantics carry a confidence number. Nothing is silently guessed.

## Execution layer policy (important)

Edits are executed by **real editing software only**:

| Target | Role | Interface |
|---|---|---|
| DaVinci Resolve | primary build/render: timeline, clips, speed, keyframes, markers | official Python/Lua scripting API (free version includes it) |
| After Effects | effect-heavy shots (glitch, RGB split, text animation) | generated `.jsx` run by After Effects itself |
| Premiere / FCP | interchange | OTIO / EDL / FCPXML export of the blueprint |

The analysis pipeline never assembles, composites, or renders video: it
*decodes and measures* the reference (PyAV + OpenCV, read-only instruments,
like a ruler). The blueprint's `compilation` section records this policy;
the NLE compilers themselves are prototype 2.

## Web limits

- 80 MB download cap, 120 s duration cap per analysis (CLI: none)
- `maxDuration: 300` (Vercel Fluid compute; a 2-minute 1080p clip analyzes
  in well under that)
- the demo fixture is bundled; any direct MP4/MOV/WebM URL works otherwise

## Roadmap

1. **Prototype 1 — this repo.** Reference → blueprint. Done: CLI + web app.
2. **Prototype 2.** Blueprint → apply to user footage → build the timeline
   inside DaVinci Resolve via its scripting API. Map `footage_required`
   slots onto supplied clips, compile speed curves, scale keyframes,
   transitions and audio ops into native timeline items.
3. **Later.** Analyze what footage the blueprint needs → search/select or
   generate assets → build → render → compare against reference → iterate.
   Explicitly out of scope until 1 and 2 work.

## Known limits (prototype 1)

- Cut detection is histogram+motion based; it can miss cuts inside very
  similar-looking footage and can be tuned via `CUT_Z`/`CUT_FLOOR` in
  `refcut/signals.py`.
- Global-motion measurement needs texture; flat shots report
  `feature_confidence: 0` and null motion instead of guessing.
- Tempo estimation can land on the half/double octave; the beat grid is
  exposed so the semantic layer can correct it.
- Text/overlay detection is delegated to the VLM (no OCR dependency yet).
- VFR sources are treated as CFR (frame i = i/fps).
