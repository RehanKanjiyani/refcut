"""Single-pass frame measurement + cut/shot detection.

One pass over every (scaled) frame produces:
  per-frame : luma, sharpness (Laplacian variance), HSV histogram
  per-pair  : histogram distance, mean abs gray diff, ORB/RANSAC global
              motion (scale / translation / rotation)

Then: adaptive cut detection, flash-frame merging, per-shot aggregates
(zoom, punch-ins, shake, pan, camera-move labels).
"""

import numpy as np
import cv2

from . import probe

HIST_BINS = [32, 8]          # hue x saturation
ROLLING_WIN = 31             # frames for the adaptive cut baseline
CUT_Z = 6.0                  # robust-z threshold for a cut
CUT_FLOOR = 0.10             # absolute floor on the combined cut score
FLASH_WHITE = 200.0
FLASH_BLACK = 14.0
PUNCH_UP = 1.15              # single-pair scale jump = punch-in
PUNCH_DOWN = 0.87


def frame_pass(src, sample_step=0):
    """Single decode pass: per-frame signal rows + visual sample frames."""
    orb = cv2.ORB_create(nfeatures=800)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    prev_gray = prev_hist = prev_kp = prev_desc = None
    rows, samples = [], []
    for rec in probe.decode_stream(src, sample_step=sample_step):
        im = rec["frame"]
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, HIST_BINS, [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        row = {
            "luma": float(gray.mean()),
            "blur": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            "hist": hist.flatten().astype(np.float32),
            "bhatt": None, "graydiff": None,
            "scale": None, "tx": None, "ty": None, "rot": None,
        }
        if prev_gray is not None:
            row["bhatt"] = float(cv2.compareHist(
                prev_hist, row["hist"], cv2.HISTCMP_BHATTACHARYYA))
            row["graydiff"] = float(np.abs(
                gray.astype(np.int16) - prev_gray.astype(np.int16)).mean())
            kp, desc = orb.detectAndCompute(gray, None)
            if (desc is not None and prev_desc is not None
                    and len(kp) >= 12 and len(prev_kp) >= 12):
                matches = bf.knnMatch(prev_desc, desc, k=2)
                good = [
                    m for m, n in (p for p in matches if len(p) == 2)
                    if m.distance < 0.75 * n.distance
                ]
                if len(good) >= 8:
                    src = np.float32(
                        [prev_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                    dst = np.float32(
                        [kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                    M, _ = cv2.estimateAffinePartial2D(
                        src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
                    if M is not None:
                        row["scale"] = float(np.hypot(M[0, 0], M[1, 0]))
                        row["tx"] = float(M[0, 2])
                        row["ty"] = float(M[1, 2])
                        row["rot"] = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
            prev_kp, prev_desc = kp, desc
        else:
            prev_kp = prev_desc = None
        rows.append(row)
        prev_gray, prev_hist = gray, row["hist"]
        if rec["sample"] is not None:
            samples.append({"t": rec["t"], "img": rec["sample"]})
    return rows, samples


def _rolling_robust(v, win=ROLLING_WIN):
    """Rolling (median, MAD) with edge windows."""
    n = len(v)
    med = np.empty(n)
    mad = np.empty(n)
    half = win // 2
    for i in range(n):
        seg = v[max(0, i - half): min(n, i + half + 1)]
        m = np.median(seg)
        med[i] = m
        mad[i] = np.median(np.abs(seg - m)) + 1e-4
    return med, mad


def pick_cuts(rows):
    n = len(rows)
    score = np.zeros(n)
    for i in range(1, n):
        b = rows[i]["bhatt"] or 0.0
        g = min((rows[i]["graydiff"] or 0.0) / 40.0, 1.0)
        score[i] = 0.65 * b + 0.35 * g
    med, mad = _rolling_robust(score)
    cuts = []
    for i in range(1, n):
        rz = (score[i] - med[i]) / (1.4826 * mad[i])
        big_spike = (score[i] > 0.45 and score[i] > 5 * max(med[i], 0.02)
                     and score[i] >= score[max(0, i - 2): i + 3].max())
        # clause 1: adaptive outlier (usual case)
        # clause 2: an unambiguous full-image change sitting inside a
        # high-motion neighborhood that inflated the local baseline
        if score[i] > CUT_FLOOR and (rz > CUT_Z or big_spike):
            cuts.append(i)
    # enforce a minimum gap, keeping the stronger spike — unless the frames
    # between the two spikes look like a flash (white/black interruption),
    # in which case both spikes must survive so merge_flashes can pair them
    luma = np.array([r["luma"] for r in rows])
    pruned = []
    for c in cuts:
        if pruned and c - pruned[-1] < 3:
            between = luma[pruned[-1]:c]
            looks_flash = len(between) and (between.mean() >= FLASH_WHITE
                                            or between.mean() <= FLASH_BLACK)
            if looks_flash:
                pruned.append(c)
            elif score[c] > score[pruned[-1]]:
                pruned[-1] = c
            continue
        pruned.append(c)
    return pruned, score


def merge_flashes(cuts, luma, fps):
    """Collapse 1-5 frame white/black interruptions into flash transitions.

    Cut semantics: a cut index c means frame c is the FIRST frame of new
    content.  Frames c1..c2-1 between two close cuts are the interruption.
    """
    boundaries = []
    i = 0
    while i < len(cuts):
        c1 = cuts[i]
        c2 = cuts[i + 1] if i + 1 < len(cuts) else None
        if c2 is not None and c2 - c1 <= 5:
            between = luma[c1:c2]
            if len(between) and between.mean() >= FLASH_WHITE:
                boundaries.append({"start_frame": c2, "type": "flash",
                                   "polarity": "white",
                                   "frames": int(c2 - c1)})
                i += 2
                continue
            if len(between) and between.mean() <= FLASH_BLACK:
                boundaries.append({"start_frame": c2, "type": "flash",
                                   "polarity": "black",
                                   "frames": int(c2 - c1)})
                i += 2
                continue
            if c2 - c1 <= 2:
                boundaries.append({"start_frame": c1, "type": "hard_cut"})
                i += 2
                continue
        boundaries.append({"start_frame": c1, "type": "hard_cut"})
        i += 1
    return boundaries


def _move_avg(v, k=11):
    if len(v) < k:
        return np.full_like(v, v.mean() if len(v) else 0.0)
    kernel = np.ones(k, np.float32) / k
    return np.convolve(v, kernel, mode="same")


def _shot_motion(rows, a, b, width, fps):
    """Aggregates over frame span [a..b] inclusive."""
    scales, txs, tys = [], [], []
    for i in range(a + 1, b + 1):
        scales.append(rows[i]["scale"])
        txs.append(rows[i]["tx"] if rows[i]["tx"] is not None else np.nan)
        tys.append(rows[i]["ty"] if rows[i]["ty"] is not None else np.nan)
    scales = np.array([s if s is not None else np.nan for s in scales], np.float64)
    out = {"zoom_pct": None, "scale_curve": [], "punch_in": None,
           "punch_out": None, "shake_px": None, "pan": None,
           "motion_energy": None, "camera_move": "static",
           "feature_confidence": 0.0}

    n_pairs = len(scales)
    valid = ~np.isnan(scales)
    if n_pairs and valid.mean() >= 0.4:
        s = np.where(valid, scales, 1.0)
        zoom_prod = float(np.prod(s[-min(len(s), 400):]))
        out["zoom_pct"] = round(100.0 * zoom_prod, 1)
        out["feature_confidence"] = round(float(valid.mean()), 2)

        # cumulative scale curve, downsampled to <=12 points
        cum = np.cumprod(s)
        idxs = np.unique(np.linspace(0, n_pairs - 1, min(12, n_pairs)).astype(int))
        out["scale_curve"] = [
            {"t": round((a + 1 + int(j)) / fps, 3),
             "scale_pct": round(100.0 * float(cum[j]), 1)}
            for j in idxs
        ]

        # punch: isolated large jump surrounded by calm pairs
        for j in range(n_pairs):
            if valid[j] and (scales[j] > PUNCH_UP or scales[j] < PUNCH_DOWN):
                lo, hi = max(0, j - 3), min(n_pairs, j + 4)
                neigh = [scales[k] for k in range(lo, hi)
                         if k != j and valid[k]]
                calm = (not neigh) or (np.median(neigh) < 1.06
                                       and np.median(neigh) > 0.94)
                if calm:
                    if scales[j] > PUNCH_UP and out["punch_in"] is None:
                        out["punch_in"] = {"after_pair": int(j),
                                           "scale_pct": round(100 * scales[j], 1)}
                    if scales[j] < PUNCH_DOWN and out["punch_out"] is None:
                        out["punch_out"] = {"after_pair": int(j),
                                            "scale_pct": round(100 * scales[j], 1)}

    tx, ty = np.array(txs, np.float64), np.array(tys, np.float64)
    v = ~(np.isnan(tx) | np.isnan(ty))
    if len(tx) and v.mean() >= 0.4:
        tx_f = np.where(v, tx, 0.0)
        ty_f = np.where(v, ty, 0.0)
        resid = np.sqrt((tx_f - _move_avg(tx_f)) ** 2 + (ty_f - _move_avg(ty_f)) ** 2)
        out["shake_px"] = round(float(np.std(resid)), 2)
        dx, dy = float(tx_f.sum()), float(ty_f.sum())
        out["pan"] = {"dx_pct": round(100.0 * dx / width, 1),
                      "dy_pct": round(100.0 * dy / width, 1)}

    # camera-move label
    labels = []
    if out["zoom_pct"] is not None:
        if out["zoom_pct"] > 108:
            labels.append("zoom_in")
        elif out["zoom_pct"] < 92:
            labels.append("zoom_out")
    if out["punch_in"]:
        labels.append("punch_in")
    if out["pan"]:
        if abs(out["pan"]["dx_pct"]) > 5:
            labels.append("pan_right" if out["pan"]["dx_pct"] > 0 else "pan_left")
        if abs(out["pan"]["dy_pct"]) > 5:
            labels.append("pan_down" if out["pan"]["dy_pct"] > 0 else "pan_up")
    if out["shake_px"] is not None and out["shake_px"] > 1.2:
        labels.append("handheld/shake")
    out["camera_move"] = labels or ["static"]
    return out


def detect_shot_spans(boundaries, n):
    starts = [0] + [bd["start_frame"] for bd in boundaries]
    ends = []
    for i in range(len(starts) - 1):
        bd = boundaries[i]
        # flash frames live in the transition gap, not in either shot
        gap = bd.get("frames", 0) if bd.get("type") == "flash" else 0
        ends.append(starts[i + 1] - 1 - gap)
    ends.append(n - 1)
    return list(zip(starts, ends))


def measure(src, fps, sample_step=0):
    """Full measurement pass -> (rows, boundaries, shots, samples)."""
    rows, samples = frame_pass(src, sample_step=sample_step)
    if not rows:
        raise RuntimeError("no frames decoded")
    cuts, score = pick_cuts(rows)
    luma = np.array([r["luma"] for r in rows])
    boundaries = merge_flashes(cuts, luma, fps)
    spans = detect_shot_spans(boundaries, len(rows))
    shots = []
    for i, (a, b) in enumerate(spans):
        agg = _shot_motion(rows, a, b, probe.SIGNAL_WIDTH, fps)
        blur = [rows[j]["blur"] for j in range(a, b + 1)]
        agg["blur_index"] = round(float(np.median(blur)) /
                                  (np.median([r["blur"] for r in rows]) + 1e-9), 2)
        agg["frames"] = [a, b]
        shots.append(agg)
    return rows, boundaries, shots, samples
