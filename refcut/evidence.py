"""Assemble measured evidence + contact sheet + the semantic-analysis prompt.

`build(src, out_dir=None)` returns (evidence_dict, contact_sheet_jpeg).
With `out_dir`, files are also written for the local CLI workflow.  The API
path keeps everything in memory.
"""

import json
from pathlib import Path

import cv2
import numpy as np

from . import probe, signals, audio as audio_mod

THUMB_W = 320


def _nearest_onset(onsets, t, tol=0.08):
    best, bd = None, tol
    for o in onsets:
        d = abs(o["t"] - t)
        if d <= bd:
            best, bd = o, d
    return best, bd


def _nearest_beat(beat_times, t, tol=0.08):
    if not beat_times:
        return None, None
    b = min(beat_times, key=lambda bt: abs(bt - t))
    if abs(b - t) <= tol:
        return b, round(t - b, 3)
    return None, None


def contact_sheet_jpeg(samples):
    """Timestamped thumbnail grid from in-memory sample frames."""
    ims = []
    for s in samples:
        im = s["img"]
        h, w = im.shape[:2]
        nh = int(h * THUMB_W / w)
        thumb = cv2.resize(im, (THUMB_W, nh))
        label = f"{s['t']:.2f}s"
        cv2.putText(thumb, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(thumb, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 1, cv2.LINE_AA)
        ims.append(thumb)
    if not ims:
        return None
    cols = 5
    rows = (len(ims) + cols - 1) // cols
    cell_h = max(im.shape[0] for im in ims) + 4
    sheet = np.zeros((rows * cell_h, cols * (THUMB_W + 4), 3), np.uint8)
    for k, im in enumerate(ims):
        r, c = divmod(k, cols)
        sheet[r * cell_h: r * cell_h + im.shape[0],
              c * (THUMB_W + 4): c * (THUMB_W + 4) + im.shape[1]] = im
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None


def _shot_color(samples, a, b, fps):
    """Color stats from the sample frames inside the shot span."""
    t0, t1 = a / fps, (b + 1) / fps
    sat, luma, bgr = [], [], []
    for s in samples:
        if s["t"] < t0 - 0.01 or s["t"] > t1 + 0.01:
            continue
        small = cv2.resize(s["img"], (160, 90))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        sat.append(float(hsv[..., 1].mean()))
        luma.append(float(hsv[..., 2].std()))
        bgr.append(small.reshape(-1, 3).mean(axis=0))
    if not bgr:
        return None
    b, g, r = (float(np.mean([x[k] for x in bgr])) for k in range(3))
    return {
        "avg_hex": "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b)),
        "saturation_mean": round(float(np.mean(sat)), 1) if sat else None,
        "luma_contrast": round(float(np.mean(luma)), 1) if luma else None,
    }


def build(src, out_dir=None, sample_fps=4.0):
    """Run the full measurement pass. Returns (evidence, sheet_jpeg|None)."""
    src = str(src) if isinstance(src, Path) else src
    meta = probe.probe(src)
    fps = meta["fps"]
    step = probe.sample_step_for(meta, sample_fps=sample_fps)

    print(f"[1/4] measuring frames @ {meta['width']}x{meta['height']} {fps} fps ...")
    rows, boundaries, shot_aggs, samples = signals.measure(src, fps, sample_step=step)
    n = len(rows)

    print("[2/4] measuring audio ...")
    audio_ev = None
    if meta["has_audio"]:
        try:
            x, sr = probe.decode_audio(src)
            if x is not None:
                audio_ev = audio_mod.measure_audio(x, sr)
        except Exception as e:  # noqa: BLE001 - evidence must survive bad audio
            print(f"      audio measurement failed: {e}")

    print("[3/4] building contact sheet ...")
    sheet = contact_sheet_jpeg(samples)

    print("[4/4] assembling evidence ...")
    onsets = audio_ev["onsets"] if audio_ev else []
    beats = audio_ev["beat_times"] if audio_ev else []
    impacts = audio_ev["impacts"] if audio_ev else []

    shots = []
    spans = signals.detect_shot_spans(boundaries, n)
    for i, (agg, (a, b)) in enumerate(zip(shot_aggs, spans)):
        start_t = round(a / fps, 3)
        end_t = round(min((b + 1) / fps, meta["duration"]), 3)
        # boundaries[i-1] describes the transition INTO shot i; shot 0 starts the video
        if i == 0:
            trans = {"type": "start"}
        else:
            bd = boundaries[i - 1]
            trans = {"type": bd.get("type", "hard_cut")}
            if bd.get("type") == "flash":
                trans["polarity"] = bd.get("polarity")
                trans["frames"] = bd.get("frames")
        onset, delta = _nearest_onset(onsets, start_t)
        beat, bdelta = _nearest_beat(beats, start_t)
        entry = {
            "id": f"SHOT_{i + 1:02d}",
            "start": start_t,
            "end": end_t,
            "duration": round(end_t - start_t, 3),
            "frames": [a, b],
            "measured_motion": {
                "camera_move": agg["camera_move"],
                "zoom_pct": agg["zoom_pct"],
                "scale_curve": agg["scale_curve"],
                "punch_in": agg["punch_in"],
                "punch_out": agg["punch_out"],
                "shake_px": agg["shake_px"],
                "pan_pct": agg["pan"],
                "motion_energy": round(float(np.mean(
                    [rows[j]["graydiff"] or 0.0 for j in range(a + 1, b + 1)])), 2),
                "blur_index": agg["blur_index"],
                "feature_confidence": agg["feature_confidence"],
            },
            "transition_in": trans,
            "color": _shot_color(samples, a, b, fps),
            "audio_sync": None,
        }
        if onset:
            entry["audio_sync"] = {
                "onset_at": onset["t"], "delta_s": delta,
                "on_beat": beat is not None,
                "bass_impact": any(abs(im["t"] - onset["t"]) < 0.05 for im in impacts),
            }
        shots.append(entry)

    # standalone punch events (mid-shot, not at boundaries)
    events = []
    for i, (agg, (a, b)) in enumerate(zip(shot_aggs, spans)):
        if agg["punch_in"]:
            events.append({"t": round((a + 1 + agg["punch_in"]["after_pair"]) / fps, 3),
                           "type": "punch_in",
                           "scale_pct": agg["punch_in"]["scale_pct"],
                           "shot": f"SHOT_{i + 1:02d}"})
        if agg["punch_out"]:
            events.append({"t": round((a + 1 + agg["punch_out"]["after_pair"]) / fps, 3),
                           "type": "punch_out",
                           "scale_pct": agg["punch_out"]["scale_pct"],
                           "shot": f"SHOT_{i + 1:02d}"})

    evidence = {
        "schema": "refcut-evidence-0.1",
        "video": meta,
        "analysis_inputs": {
            "contact_sheet": "contact_sheet.jpg",
            "samples_dir": "samples",
            "sample_fps": round(fps / step, 3),
            "sample_count": len(samples),
            "how_to_look_closer": "read individual files in samples/ for detail",
        },
        "audio": audio_ev,
        "shots": shots,
        "events": events,
        "measurement_notes": [
            "all timestamps are seconds from video start, frame-accurate at the source fps",
            "zoom_pct is cumulative measured content scale over the shot (100 = none)",
            "shake_px is high-frequency translation jitter after detrending",
            "blur_index < 1 means the shot is softer/more motion-blurred than the video median",
            "feature_confidence is the fraction of frame pairs where global motion was measurable; "
            "low values mean flat/textureless content, not absence of motion",
            "text, overlays, transitions semantics, subject content and SFX identity are NOT measured here — "
            "they belong to the semantic (VLM) layer",
        ],
    }

    if out_dir:
        out_dir = Path(out_dir)
        (out_dir / "samples").mkdir(parents=True, exist_ok=True)
        for k, s in enumerate(samples):
            cv2.imwrite(str(out_dir / "samples" / f"sample_{k:04d}.jpg"),
                        s["img"], [cv2.IMWRITE_JPEG_QUALITY, 90])
        if sheet:
            (out_dir / "contact_sheet.jpg").write_bytes(sheet)
        (out_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8")
        (out_dir / "analysis_prompt.md").write_text(
            render_prompt(evidence), encoding="utf-8")
    return evidence, sheet


ANALYSIS_CONTRACT = """\
{
  "global_style": {
    "summary": "one paragraph: what editing language defines this video",
    "pacing": {"character": "e.g. frantic / driving / breathing", "avg_shot_s": 0.0},
    "color": {"treatment": "e.g. high-contrast teal & orange", "saturation": "...", "contrast": "...", "grain": "..."},
    "sound": {"music": "genre/feel/bpm if audible", "sfx_palette": ["..."], "sync_policy": "what lands on beats"},
    "motion_language": "zoom/punch/shake habits of the edit",
    "text_style": {"placement": "...", "animation": "...", "font_feel": "..."},
    "recurring_patterns": [{"id": "P1", "name": "punch-in on bass impact", "description": "..."}]
  },
  "shots": [
    {
      "id": "SHOT_01",
      "content": {
        "footage_required": "concrete, shootable description of REQUIRED footage",
        "framing": "medium close-up / wide / macro ...",
        "subject_motion": "what the subject does in this shot",
        "on_screen_text": "exact text visible, or null"
      },
      "transition_in": {"type": "hard_cut|match_cut|whip_pan|flash|glitch|dissolve|zoom_transition|wipe|other",
                         "note": "what makes it work, e.g. shape continuity"},
      "creative": {
        "speed_feel": "none|ramp_in|ramp_out|ramp_in_out|freeze|stutter",
        "speed_curve": [{"t": 0.0, "v": 100}, {"t": 0.2, "v": 250}, {"t": 0.4, "v": 100}],
        "intent": "why this shot exists in the sequence"
      },
      "effects_inferred": {
        "motion_blur": "none|light|medium|heavy",
        "camera_shake": "none|light|medium|heavy",
        "glitch": "none|...|describe",
        "rgb_split": "none|light|heavy",
        "text_animation": "pop|slide|typewriter|none|...",
        "other": []
      },
      "pattern_refs": ["P1"],
      "confidence": 0.85
    }
  ]
}
"""


def render_prompt(evidence):
    ev_json = json.dumps(evidence, indent=2)
    sample_fps = (evidence.get("analysis_inputs") or {}).get("sample_fps", 4.0)
    return f"""# Task: timeline-level editing deconstruction

You are a senior commercial editor deconstructing a reference edit. You are given:

1. MEASURED EVIDENCE (below) — frame-accurate numbers from a signal pass:
   cuts, shot spans, cumulative zoom, punch-ins, shake, pan, blur, onsets,
   bass impacts, BPM, beat grid, silences, cut-to-beat sync.
2. A CONTACT SHEET (image attached) of frames sampled at {sample_fps} fps;
   the burned-in label on each thumbnail is its timestamp. The single-frame
   flashes will not appear in it — trust the measured evidence for those.

## Rules

- Times: NEVER invent or re-estimate timestamps. Use the measured ones.
  Your job is SEMANTICS and CRAFT on top of the measured skeleton.
- Separate CONTENT from CRAFT. `content.footage_required` describes what
  footage the shot needs so the same edit language can be applied to
  different material. Everything else is craft/style.
- Name transitions and effects like an editor (match cut, whip-pan into,
  2-frame white flash, punch-in on the bass hit, speed ramp into the
  impact...). If measured motion already shows it (zoom_pct, punch_in,
  shake_px), say it consistently with the numbers.
- Where the evidence is ambiguous, say what you inferred and set
  confidence < 0.6. Mark genuinely invisible properties as null, not guesses.
- Recurring creative patterns get ids (P1, P2...) and shots reference them
  via `pattern_refs`.

## Output

Return ONLY strict JSON matching this contract (no markdown fence, no prose):

{ANALYSIS_CONTRACT}

## Measured evidence

```json
{ev_json}
```
"""
