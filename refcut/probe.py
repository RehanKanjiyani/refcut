"""Video probing and frame decoding via PyAV (bundled FFmpeg libraries).

No subprocesses, no system ffmpeg — the same code path runs locally and on
serverless (Vercel).  ffmpeg is never used to edit anything: decoding here is
a read-only MEASUREMENT instrument for the analysis phase.  The editing
itself, in prototype 2, happens inside real editing software (DaVinci
Resolve / After Effects / Premiere) via their official automation interfaces.
"""

from pathlib import Path

import numpy as np

SIGNAL_WIDTH = 640   # working resolution for the measurement pass
SAMPLE_WIDTH = 960   # resolution of stills handed to the visual (VLM) pass


def _open(src):
    import av
    # file-like sources (BytesIO uploads) are opened several times —
    # probe, video decode, audio decode — always rewind first
    if hasattr(src, "seek"):
        try:
            src.seek(0)
        except Exception:  # noqa: BLE001 - non-seekable streams: take our chances
            pass
    return av.open(src)


def probe(src):
    """Metadata dict for a video path, file-like object, or bytes-backed IO."""
    with _open(src) as c:
        vs = c.streams.video[0]
        fps = float(vs.average_rate) if vs.average_rate else 30.0
        duration = None
        if c.duration:
            duration = float(c.duration) / 1e6
        elif vs.duration and vs.time_base:
            duration = float(vs.duration) * float(vs.time_base)
        if not duration or duration <= 0:
            duration = 0.0
        name = getattr(src, "name", None) or str(src)
        return {
            "path": str(name),
            "duration": round(duration, 3),
            "fps": round(fps, 3),
            "width": int(vs.width),
            "height": int(vs.height),
            "has_audio": len(c.streams.audio) > 0,
        }


def frame_size(width, height, scale_width=SIGNAL_WIDTH):
    """Even height used when scaling to `scale_width` (matches -2 semantics)."""
    h = int(round(height * scale_width / width / 2.0)) * 2
    return scale_width, max(h, 2)


def decode_stream(src, signal_width=SIGNAL_WIDTH, sample_width=SAMPLE_WIDTH,
                  sample_step=0):
    """Single decode pass.

    Yields dicts:
      idx, t          frame index and timestamp (CFR assumption: t = idx/fps)
      frame           BGR ndarray scaled to `signal_width` (for signals)
      sample          BGR ndarray scaled to `sample_width`, or None
                      (one every `sample_step` frames)
    """
    import cv2
    meta = probe(src)
    fps = meta["fps"]
    sw, sh = frame_size(meta["width"], meta["height"], signal_width)
    with _open(src) as c:
        idx = 0
        for frame in c.decode(video=0):
            img = frame.to_ndarray(format="bgr24")
            small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
            sample = None
            if sample_step and idx % sample_step == 0:
                tw = min(sample_width, meta["width"])
                th = int(meta["height"] * tw / meta["width"])
                sample = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
            yield {"idx": idx, "t": idx / fps, "frame": small, "sample": sample}
            idx += 1


def decode_audio(src, sr=22050):
    """Decode the first audio track to mono float32 at `sr`.

    Returns (signal, sr) or (None, sr) when there is no usable audio.
    """
    import av
    with _open(src) as c:
        if not c.streams.audio:
            return None, sr
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        chunks = []
        try:
            for frame in c.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
        except Exception:  # noqa: BLE001 - keep what decoded before the error
            pass
    if not chunks:
        return None, sr
    x = np.concatenate(chunks).astype(np.float32) / 32768.0
    return x, sr


def estimate_frame_count(meta):
    return max(1, int(round(meta["duration"] * meta["fps"])))


def sample_step_for(meta, sample_fps=4.0, max_samples=80):
    """Frames between visual samples, honoring both sample_fps and a cap on
    total sample memory (important for long videos on serverless)."""
    n = estimate_frame_count(meta)
    base = max(1, int(round(meta["fps"] / max(sample_fps, 0.05))))
    capped = max(1, int(np.ceil(n / max_samples)))
    return max(base, capped)
