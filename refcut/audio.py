"""Audio measurement: onset envelope, bass impacts, BPM/beat grid, silence.

Pure numpy (no librosa dependency). Everything here is measured evidence —
the semantic layer (which SFX, what music) is the VLM's job.
"""

import numpy as np

FFT_FRAME = 1024
HOP = 512


def _stft_log_mag(x):
    win = np.hanning(FFT_FRAME).astype(np.float32)
    n = max(0, (len(x) - FFT_FRAME) // HOP)
    S = np.empty((n, FFT_FRAME // 2), np.float32)
    for i in range(n):
        seg = x[i * HOP: i * HOP + FFT_FRAME] * win
        S[i] = np.abs(np.fft.rfft(seg))[: FFT_FRAME // 2]
    return np.log1p(S * 1000.0)


def _smooth(v, k):
    if k <= 1 or len(v) < k:
        return v
    kernel = np.ones(k, np.float32) / k
    return np.convolve(v, kernel, mode="same")


def _peaks(env, sr_per_step, min_gap_s=0.08):
    """Adaptive-threshold peak picking on a 1-D envelope."""
    if len(env) < 5:
        return []
    e = _smooth(env, 3)
    sd = float(np.std(e)) + 1e-9
    base = _smooth(e, int(1.0 / sr_per_step))  # ~1 s local baseline
    floor = 0.15 * sd
    min_gap = max(1, int(min_gap_s / sr_per_step))
    peaks = []
    last = -min_gap
    for i in range(1, len(e) - 1):
        if e[i] <= e[i - 1] or e[i] < e[i + 1]:
            continue
        if e[i] - base[i] < floor:
            continue
        if i - last < min_gap:
            if peaks and e[i] > e[peaks[-1]]:
                peaks[-1] = i
                last = i
            continue
        peaks.append(i)
        last = i
    return peaks


def measure_audio(x, sr):
    """Return measured audio evidence dict, or None if signal is silent."""
    if len(x) < sr // 2:
        return None
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms < 1e-4:
        return None

    L = _stft_log_mag(x)
    if L.shape[0] < 4:
        return None

    flux = np.maximum(0.0, np.diff(L, axis=0)).sum(axis=1)
    freqs = np.fft.rfftfreq(FFT_FRAME, 1.0 / sr)[: FFT_FRAME // 2]
    low_mask = freqs < 200.0
    flux_low = np.maximum(0.0, np.diff(L, axis=0))[:, low_mask].sum(axis=1)

    step = HOP / float(sr)
    t = (np.arange(1, L.shape[0]) * HOP + FFT_FRAME / 2.0) / sr

    def z(v):
        return (v - v.mean()) / (v.std() + 1e-9)

    on_idx = _peaks(z(flux), step)
    onsets = [
        {"t": round(float(t[i]), 3), "strength": round(float(z(flux)[i]), 2)}
        for i in on_idx
    ]

    # bass-heavy impacts: strong low-band flux with a coincident onset
    zl = z(flux_low)
    impacts = []
    for i in on_idx:
        j = min(i, len(zl) - 1)
        if zl[j] > 1.2 and z(flux)[i] > 1.2:
            impacts.append({"t": round(float(t[i]), 3), "bass": round(float(zl[j]), 2)})

    # tempo: autocorrelation of the onset envelope
    env = z(flux)
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lo_lag, hi_lag = int(0.24 / step), min(int(1.25 / step), len(ac) - 1)
    bpm = None
    beat_period = None
    if hi_lag > lo_lag + 2:
        lag = lo_lag + int(np.argmax(ac[lo_lag:hi_lag]))
        # tempo-octave correction: if the pulse at half the lag is nearly as
        # strong, prefer the faster tempo (edits sync to the faster pulse)
        half = lag // 2
        if half >= lo_lag and ac[half] >= 0.35 * ac[lag]:
            lag = half
        # parabolic refinement: beat periods rarely land on integer lags
        # (0.5 s at 43 Hz straddles lags 21/22), which otherwise biases the
        # estimate toward the nearest harmonic
        if 0 < lag < len(ac) - 1:
            y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
            denom = y0 - 2 * y1 + y2
            delta = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0
            lag = lag + float(np.clip(delta, -1.0, 1.0))
        beat_period = lag * step
        bpm = round(60.0 / beat_period, 1)

    # beat grid phase-locked to the strongest onset
    beats = []
    if bpm and onsets:
        strongest = max(onsets, key=lambda o: o["strength"])
        k = 0
        while strongest["t"] - k * beat_period > 0:
            k += 1
        tt = strongest["t"] - k * beat_period
        while tt < len(x) / sr:
            beats.append(round(tt, 3))
            tt += beat_period
        beats = [b for b in beats if b >= 0]

    # silence spans (>=0.15 s below -38 dBFS in 50 ms windows)
    win = int(0.05 * sr)
    nw = max(1, len(x) // win)
    rms_w = np.sqrt(np.mean(x[: nw * win].reshape(nw, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms_w + 1e-9)
    quiet = db < -38
    silences = []
    i = 0
    while i < nw:
        if quiet[i]:
            j = i
            while j < nw and quiet[j]:
                j += 1
            if (j - i) * 0.05 >= 0.15:
                silences.append([round(i * 0.05, 3), round(j * 0.05, 3)])
            i = j
        else:
            i += 1

    return {
        "bpm": bpm,
        "beat_period_s": round(beat_period, 4) if beat_period else None,
        "beat_times": beats,
        "onsets": onsets,
        "impacts": impacts,
        "silences": silences,
        "note": "onsets/impacts/bpm are measured; musical meaning is inferred in the semantic layer",
    }
