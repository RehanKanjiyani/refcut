"""Create a synthetic 'reference-style' clip used ONLY as input material to
test the refcut analyzer. This is fixture generation, not video editing.

The clip contains, by construction:
  s1 0.00-1.50  testsrc2 with a smooth ~25% zoom-in (animated crop)
  s2 1.50-2.20  mandelbrot with strong camera shake (jittered crop)
  FL 2.20-2.27  2-frame white flash
  s3 2.27-3.27  static smptebars
  s4 3.27-4.77  testsrc2 zoom-out (~25%)
  s5 4.77-5.77  animated gradients (smooth, feature-poor on purpose)
  s6 5.77-6.47  classic testsrc
Audio: click track every 0.5 s (120 BPM); the click at 1.50 s is a heavy
bass impact and the zoom-in shot ends exactly on it.
"""

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

W, H, FPS = 1280, 720, 30
SR = 22050
OUT = Path(__file__).resolve().parent.parent / "fixtures"
DUR = 6.5


def run(cmd):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("FAILED: " + " ".join(str(c) for c in cmd) + "\n" + p.stderr[-2500:])


def make_clicks(path):
    n = int(DUR * SR)
    x = np.zeros(n, np.float32)
    click_t = np.arange(0.0, DUR, 0.5)
    for k, t0 in enumerate(click_t):
        s = int(t0 * SR)
        length = int(0.15 * SR)
        tt = np.arange(length) / SR
        heavy = abs(t0 - 1.5) < 1e-6
        gain = 1.8 if heavy else 0.8
        freq = 52.0 if heavy else 70.0 + (k % 3) * 25.0
        env = np.exp(-tt * (28 if heavy else 70))
        burst = np.sin(2 * np.pi * freq * tt) * env
        burst += np.random.RandomState(k).randn(length) * np.exp(-tt * 300) * 0.6
        x[s: s + length] += burst * gain
    x = np.clip(x * 0.7, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def segment(name, src, dur, vf=None, frames=None):
    out = OUT / name
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src]
    if vf:
        cmd += ["-vf", vf]
    if frames:
        cmd += ["-frames:v", str(frames)]
    else:
        cmd += ["-t", str(dur)]
    cmd += ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", out]
    run(cmd)
    print("  made", name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    zoom_in = (f"zoompan=z='1+0.25*on/45':x='iw/2-(iw/zoom/2)'"
               f":y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
    zoom_out = (f"zoompan=z='1.25-0.25*on/45':x='iw/2-(iw/zoom/2)'"
                f":y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
    shake = (f"crop=1200:660:x='(in_w-out_w)/2+24*sin(47*t)'"
             f":y='(in_h-out_h)/2+24*cos(53*t)',scale={W}:{H}")

    segment("s1.mp4", f"testsrc2=size=1600x900:rate={FPS}", 1.5, vf=zoom_in)
    segment("s2.mp4", f"mandelbrot=size={W}x{H}:rate={FPS}", 0.7, vf=shake)
    segment("flash.mp4", f"color=c=white:s={W}x{H}:r={FPS}", None, frames=2)
    segment("s3.mp4", f"smptebars=size={W}x{H}:rate={FPS}", 1.0)
    segment("s4.mp4", f"testsrc2=size=1600x900:rate={FPS}", 1.5, vf=zoom_out)
    segment("s5.mp4", f"gradients=size={W}x{H}:rate={FPS}:speed=0.5", 1.0)
    segment("s6.mp4", f"testsrc=size={W}x{H}:rate={FPS}", 0.7)

    print("concatenating ...")
    lst = OUT / "concat.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in
                           ["s1.mp4", "s2.mp4", "flash.mp4", "s3.mp4",
                            "s4.mp4", "s5.mp4", "s6.mp4"]), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", OUT / "video_only.mp4"])

    print("audio click track ...")
    make_clicks(OUT / "clicks.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", OUT / "video_only.mp4",
         "-i", OUT / "clicks.wav", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-shortest", OUT / "test_reference.mp4"])
    print("fixture ready:", OUT / "test_reference.mp4")


if __name__ == "__main__":
    main()
