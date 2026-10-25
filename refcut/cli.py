"""refcut CLI.

  python -m refcut analyze VIDEO [-o OUT_DIR]   measured evidence + VLM prompt
  python -m refcut blueprint OUT_DIR           evidence (+analysis.json) -> blueprint
"""

import argparse
import json
import sys
from pathlib import Path

from . import evidence as evidence_mod
from . import llm
from .blueprint import compile_blueprint


def cmd_analyze(args):
    video = Path(args.video)
    if not video.exists():
        sys.exit(f"video not found: {video}")
    out = Path(args.out) if args.out else Path("refcut_out") / video.stem
    print(f"analyzing {video} -> {out}")
    ev, _sheet = evidence_mod.build(video, out_dir=out, sample_fps=args.sample_fps)

    shots = ev["shots"]
    cuts = [s["start"] for s in shots[1:]]
    print()
    print(f"  duration   : {ev['video']['duration']}s @ {ev['video']['fps']} fps")
    print(f"  shots      : {len(shots)}")
    if cuts:
        print(f"  cut times  : {', '.join(f'{c:.2f}' for c in cuts)}")
    if ev.get("audio"):
        a = ev["audio"]
        print(f"  audio      : ~{a['bpm']} bpm · {len(a['onsets'])} onsets · "
              f"{len(a['impacts'])} bass impacts")
    zoomed = [f"{s['id']}:{s['measured_motion']['zoom_pct']}%"
              for s in shots if s["measured_motion"]["zoom_pct"]
              and (s["measured_motion"]["zoom_pct"] > 108 or s["measured_motion"]["zoom_pct"] < 92)]
    if zoomed:
        print(f"  zooms      : {', '.join(zoomed)}")
    print()
    print(f"wrote {out / 'evidence.json'}")
    print(f"wrote {out / 'analysis_prompt.md'}  (answer it with any VLM -> analysis.json)")

    if llm.configured():
        print("REFCUT_* env configured — calling VLM for the semantic pass ...")
        try:
            llm.analyze(out)
            print(f"wrote {out / 'analysis.json'}")
        except Exception as e:  # noqa: BLE001
            print(f"automatic semantic pass failed ({e}); "
                  f"fill analysis.json manually, then rerun blueprint")
    else:
        print("no REFCUT_* env — manual mode: give analysis_prompt.md + "
              "contact_sheet.jpg to a VLM, save its JSON as analysis.json")

    print("then run: python -m refcut blueprint " + str(out))


def cmd_blueprint(args):
    out_dir = Path(args.out_dir)
    if not (out_dir / "evidence.json").exists():
        sys.exit(f"no evidence.json in {out_dir}; run `refcut analyze` first")
    bp = compile_blueprint(out_dir, analysis_path=args.analysis, out_path=args.out)
    print(f"wrote {out_dir / 'blueprint.json'}")
    print(f"wrote {out_dir / 'blueprint.md'}")
    print(f"{bp['meta']['shot_count']} shots compiled"
          + (" (measured-only: no analysis.json present)"
             if not (Path(args.analysis) if args.analysis else out_dir / "analysis.json").exists()
             else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="refcut", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="reference video -> measured evidence + VLM prompt")
    a.add_argument("video")
    a.add_argument("-o", "--out", default=None)
    a.add_argument("--sample-fps", type=float, default=4.0)
    a.set_defaults(func=cmd_analyze)

    b = sub.add_parser("blueprint", help="evidence (+analysis.json) -> editing blueprint")
    b.add_argument("out_dir")
    b.add_argument("--analysis", default=None,
                   help="path to semantic analysis JSON (default OUT_DIR/analysis.json)")
    b.add_argument("-o", "--out", default=None)
    b.set_defaults(func=cmd_blueprint)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
