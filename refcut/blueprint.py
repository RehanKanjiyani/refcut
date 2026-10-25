"""Compile measured evidence + semantic analysis into the editing blueprint.

The blueprint is the contract between analysis and execution:
  - timeline-based (per-shot entries with exact start/duration at source fps)
  - separates CONTENT (footage_required) from CRAFT (everything else)
  - every value carries provenance: measured vs inferred
  - structured so prototype 2 can compile it into real NLE projects
    (DaVinci Resolve via its official scripting API, After Effects via .jsx,
    Premiere/FCP via OTIO/EDL/FCPXML interchange) — no ffmpeg compositing.
"""

import json
from pathlib import Path

ZOOM_TOL = 8.0  # percent


def _zoom_label(zp):
    if zp is None:
        return None
    if zp > 100 + ZOOM_TOL:
        return {"direction": "in", "end_scale_pct": zp}
    if zp < 100 - ZOOM_TOL:
        return {"direction": "out", "end_scale_pct": zp}
    return {"direction": "none", "end_scale_pct": zp}


def _speed_curve(creative):
    c = creative or {}
    if c.get("speed_curve"):
        return c["speed_curve"]
    feel = c.get("speed_feel") or "none"
    if feel == "ramp_in":
        return [{"t": 0.0, "v": 60}, {"t": 1.0, "v": 220}]
    if feel == "ramp_out":
        return [{"t": 0.0, "v": 220}, {"t": 1.0, "v": 70}]
    if feel == "ramp_in_out":
        return [{"t": 0.0, "v": 100}, {"t": 0.5, "v": 240}, {"t": 1.0, "v": 100}]
    if feel == "freeze":
        return [{"t": 0.0, "v": 100}, {"t": 0.5, "v": 0}, {"t": 1.0, "v": 100}]
    return [{"t": 0.0, "v": 100}, {"t": 1.0, "v": 100}]


def build(evidence, analysis=None):
    sem_shots = {}
    style = None
    if analysis:
        style = analysis.get("global_style")
        for s in analysis.get("shots", []):
            sem_shots[s.get("id")] = s

    timeline = []
    for sh in evidence["shots"]:
        sem = sem_shots.get(sh["id"], {})
        content = sem.get("content", {})
        creative = sem.get("creative", {})
        fx = sem.get("effects_inferred", {})
        mm = sh["measured_motion"]
        trans = sh["transition_in"]
        sem_trans = sem.get("transition_in", {})

        measured, inferred = [], []

        transform = {"scale": _zoom_label(mm["zoom_pct"])}
        if mm["zoom_pct"] is not None:
            measured.append(f"scale {mm['zoom_pct']}% over shot")
        if mm.get("scale_curve"):
            transform["scale_curve"] = mm["scale_curve"]
        if mm.get("pan") and (abs(mm["pan"]["dx_pct"]) > 5 or abs(mm["pan"]["dy_pct"]) > 5):
            transform["pan_pct"] = mm["pan"]
            measured.append(f"pan dx={mm['pan']['dx_pct']}% dy={mm['pan']['dy_pct']}%")

        effects = {}
        shake = mm.get("shake_px")
        if shake is not None and shake > 1.2:
            lvl = "light" if shake < 2.5 else ("medium" if shake < 5 else "heavy")
            effects["camera_shake"] = {"level": lvl, "measured_px": shake}
            measured.append(f"shake {lvl} ({shake}px)")
        if fx.get("camera_shake") and fx["camera_shake"] != "none" and "camera_shake" not in effects:
            effects["camera_shake"] = {"level": fx["camera_shake"]}
            inferred.append(f"shake {fx['camera_shake']}")
        if mm.get("blur_index") is not None and mm["blur_index"] < 0.6:
            effects["motion_blur"] = {"hint": "shot softness below median",
                                      "blur_index": mm["blur_index"]}
        if fx.get("motion_blur") and fx["motion_blur"] != "none":
            effects.setdefault("motion_blur", {})["level"] = fx["motion_blur"]
            inferred.append(f"motion blur {fx['motion_blur']}")
        for k in ("glitch", "rgb_split", "other"):
            if fx.get(k) and fx[k] not in ("none", [], None):
                effects[k] = fx[k]
                inferred.append(f"{k}: {fx[k]}")

        timing = {
            "speed_curve": _speed_curve(creative),
            "speed_feel": creative.get("speed_feel", "none"),
        }
        if timing["speed_feel"] != "none":
            inferred.append(f"speed feel: {timing['speed_feel']}")

        audio_ops = []
        sync = sh.get("audio_sync")
        fps = evidence["video"]["fps"]
        if sync:
            if sync.get("bass_impact"):
                audio_ops.append({"op": "impact_sfx", "at": trans_time(trans, sh, fps),
                                  "note": "bass impact measured at cut", "source": "measured"})
            if sync.get("on_beat"):
                audio_ops.append({"op": "align_to_beat", "beat_time": sync["onset_at"],
                                  "delta_s": sync["delta_s"], "source": "measured"})
        sfx_palette = (style or {}).get("sound", {}).get("sfx_palette") or []
        if sem_trans.get("type") in ("whip_pan", "zoom_transition"):
            audio_ops.append({"op": "whoosh", "at": sh["start"], "source": "inferred"})

        entry = {
            "id": sh["id"],
            "start": sh["start"],
            "duration": sh["duration"],
            "content": {
                "footage_required": content.get("footage_required"),
                "framing": content.get("framing"),
                "subject_motion": content.get("subject_motion"),
                "on_screen_text": content.get("on_screen_text"),
            },
            "timing": timing,
            "transform": transform,
            "effects": effects,
            "transition_in": {
                "type": sem_trans.get("type", trans["type"]),
                "measured": trans if trans["type"] != "hard_cut" else None,
                "note": sem_trans.get("note"),
            },
            "text": ([{"content": content["on_screen_text"],
                       "animation": fx.get("text_animation", "none"),
                       "placement": (style or {}).get("text_style", {}).get("placement")} ]
                     if content.get("on_screen_text") else []),
            "audio": audio_ops,
            "color": {
                "measured": sh.get("color"),
                "grade": "inherit_global",
            },
            "pattern_refs": sem.get("pattern_refs", []),
            "confidence": sem.get("confidence"),
            "provenance": {"measured": measured, "inferred": inferred},
        }
        timeline.append(entry)

    blueprint = {
        "schema": "refcut-blueprint-0.1",
        "meta": {
            "reference": evidence["video"]["path"],
            "fps": evidence["video"]["fps"],
            "resolution": [evidence["video"]["width"], evidence["video"]["height"]],
            "duration": evidence["video"]["duration"],
            "shot_count": len(timeline),
        },
        "global_style": style or {
            "summary": "MISSING — run the semantic pass (analysis.json) for style",
        },
        "audio_map": None if not evidence.get("audio") else {
            "bpm": evidence["audio"]["bpm"],
            "beat_times": evidence["audio"]["beat_times"],
            "impacts": evidence["audio"]["impacts"],
            "silences": evidence["audio"]["silences"],
        },
        "timeline": timeline,
        "compilation": {
            "status": "not implemented in prototype 1",
            "planned_targets": {
                "davinci_resolve": "primary: build timeline via official Python API "
                                   "(media pool, clips, speed, pan/zoom keyframes, markers)",
                "after_effects": "effects-heavy shots compiled to .jsx and run by After Effects",
                "premiere_fcp": "interchange via OTIO/EDL/FCPXML export of the blueprint",
            },
            "policy": "edits are executed by real editing software only; "
                      "ffmpeg/python are measurement and glue, never the renderer",
        },
    }
    return blueprint


def trans_time(trans, sh, fps):
    t = sh["transition_in"]
    if t.get("type") == "flash":
        return round(sh["start"] - t.get("frames", 0) / fps, 3)
    return sh["start"]


def to_markdown(bp):
    lines = [
        f"# Editing blueprint — {Path(bp['meta']['reference']).name}",
        "",
        f"{bp['meta']['shot_count']} shots · {bp['meta']['duration']}s · "
        f"{bp['meta']['fps']} fps · {bp['meta']['resolution'][0]}x{bp['meta']['resolution'][1]}",
        "",
    ]
    st = bp.get("global_style") or {}
    if st.get("summary"):
        lines += ["## Global style", st["summary"], ""]
    am = bp.get("audio_map")
    if am and am.get("bpm"):
        lines += [f"Audio: ~{am['bpm']} BPM · {len(am.get('beat_times') or [])} beats · "
                  f"{len(am.get('impacts') or [])} bass impacts", ""]
    for e in bp["timeline"]:
        lines.append(f"## {e['id']}  {e['start']:.3f}–{e['start'] + e['duration']:.3f} "
                     f"({e['duration']:.2f}s)")
        c = e["content"]
        if c.get("footage_required"):
            lines.append(f"- content: {c['footage_required']}"
                         + (f" — {c['framing']}" if c.get("framing") else ""))
        if c.get("subject_motion"):
            lines.append(f"- subject: {c['subject_motion']}")
        tr = e["transition_in"]
        tt = tr["type"]
        if tr.get("measured") and tr["measured"].get("type") == "flash":
            n_frames = tr['measured']['frames']
            tt += f" ({tr['measured']['polarity']}, {n_frames} frame" + ("s" if n_frames != 1 else "") + ")"
        if tr.get("note"):
            tt += f" — {tr['note']}"
        lines.append(f"- transition_in: {tt}")
        sc = e["transform"].get("scale") or {}
        if sc and sc.get("direction") != "none":
            lines.append(f"- transform: scale → {sc['end_scale_pct']}% ({sc['direction']})")
        if e["transform"].get("pan_pct"):
            p = e["transform"]["pan_pct"]
            lines.append(f"- transform: pan dx={p['dx_pct']}% dy={p['dy_pct']}%")
        if e["timing"]["speed_feel"] != "none":
            lines.append(f"- timing: speed {e['timing']['speed_feel']}"
                         + (f" curve={e['timing']['speed_curve']}" if e["timing"].get("speed_curve") else ""))
        for k, v in (e["effects"] or {}).items():
            if k == "motion_blur" and isinstance(v, dict):
                lines.append(f"- effect: motion blur {v.get('level', v.get('hint', ''))}"
                             + (f" (index {v['blur_index']})" if "blur_index" in v else ""))
            elif k == "camera_shake":
                lines.append(f"- effect: camera shake {v.get('level', v)}")
            else:
                lines.append(f"- effect: {k} {v}")
        for txt in e["text"]:
            lines.append(f"- text: “{txt['content']}” ({txt.get('animation', 'none')})")
        for a in e["audio"]:
            lines.append(f"- audio: {a['op']} @ {a.get('at', a.get('beat_time'))} ({a['source']})")
        col = (e.get("color") or {}).get("measured") or {}
        if col.get("avg_hex"):
            lines.append(f"- color: {col['avg_hex']} sat={col.get('saturation_mean')} "
                         f"contrast={col.get('luma_contrast')}")
        if e["pattern_refs"]:
            lines.append(f"- patterns: {', '.join(e['pattern_refs'])}")
        lines.append("")
    lines.append("---")
    lines.append("Execution: real editing software only (DaVinci Resolve API / AE jsx / OTIO). "
                 "ffmpeg & python were used to MEASURE the reference, never to edit.")
    return "\n".join(lines) + "\n"


def _validate(bp, schema_path=Path(__file__).resolve().parent.parent / "schema" / "blueprint.schema.json"):
    try:
        import jsonschema
    except ImportError:
        return
    if not schema_path.exists():
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(bp, schema)


def compile_blueprint(out_dir, analysis_path=None, out_path=None):
    out_dir = Path(out_dir)
    evidence = json.loads((out_dir / "evidence.json").read_text(encoding="utf-8"))
    analysis = None
    ap = Path(analysis_path) if analysis_path else out_dir / "analysis.json"
    if ap.exists():
        analysis = json.loads(ap.read_text(encoding="utf-8"))
    bp = build(evidence, analysis)
    _validate(bp)
    op = Path(out_path) if out_path else out_dir / "blueprint.json"
    op.write_text(json.dumps(bp, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "blueprint.md").write_text(to_markdown(bp), encoding="utf-8")
    return bp
