"""Drum transcription from a full mix (Phase 6, drums only).

No source separation model is used. The percussive component (HPSS) is split into frequency
bands with characteristic drum energy, and onsets are picked per band:

    kick        40–130 Hz      body of the bass drum
    snare       1.2–5 kHz      snare wires / clap noise (+ 150–350 Hz body as support)
    hats        7–11 kHz       closed / open hi-hat, cymbals

Cross-talk rules stop one physical hit from producing several instruments (a snare also emits
energy above 7 kHz, a kick beater click reaches the snare band). After an onset, the decay of
the high band separates closed hat (< ~100 ms), open hat and crash (long, loud, broadband).

Output events use General MIDI drum notes (spec §11) and velocity from onset strength (spec §17).
Confidence per instrument reflects onset prominence and how consistently hits fall on a 16th grid.
"""
from __future__ import annotations

import numpy as np

HOP = 256  # 11.6 ms at 22.05 kHz: finer timing than the tempo stage, needed for groove capture

GM = {"kick": 36, "snare": 38, "closed_hat": 42, "open_hat": 46, "crash": 49}
INSTRUMENTS = list(GM)

BANDS = {"kick": (40, 130), "snare": (1200, 5000), "snare_body": (150, 350), "hat": (7000, 11000)}

# Peak-pick thresholds on band flux normalised to its robust maximum.
THRESHOLDS = {"onset": 0.10, "band": 0.25}
# Classification of an onset from normalised band flux (calibrated in tools/eval_drums.py).
KICK_MIN, KICK_WEAK, KICK_WITH_SNARE = 0.45, 0.25, 0.65
SNARE_MIN, SNARE_MID_HIGH_RATIO = 0.25, 0.85
HAT_MIN, HAT_OVER_SNARE = 0.25, 1.4
CRASH_SUSTAIN, OPEN_HAT_SUSTAIN = 0.18, 0.25
MIN_GAP_SEC = 0.06
VEL_MIN, VEL_MAX = 30, 120  # spec §17

# Drum-kit presence. Cymbals and snare wires are noise (flat spectrum); piano, celesta or string
# attacks that also produce onsets are tonal. Calibrated on synthetic drums + 7 real recordings:
# hat-band flatness (measured up to the file's bandwidth) 0.73–0.85 with a kit, 0.47–0.67 without.
KIT_HAT_FLATNESS = 0.70
KIT_SNARE_FLATNESS = 0.78
KIT_MIN_HAT_EVENTS = 8


def _flux(S: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Positive spectral flux of a band on sqrt magnitudes (log scaling amplifies cross-talk)."""
    band = np.sqrt(S[(freqs >= lo) & (freqs < hi)])
    if band.size == 0:
        return np.zeros(S.shape[1])
    return np.maximum(0.0, np.diff(band, axis=1, prepend=band[:, :1])).sum(axis=0)


def _energy(S, freqs, lo, hi):
    return S[(freqs >= lo) & (freqs < hi)].sum(axis=0)


def _norm(x: np.ndarray) -> np.ndarray:
    ref = np.percentile(x, 99.5)
    return x / ref if ref > 1e-9 else x * 0


def _peaks(x: np.ndarray, delta: float, sr: int) -> np.ndarray:
    import librosa

    wait = max(1, int(MIN_GAP_SEC * sr / HOP))
    return np.asarray(librosa.util.peak_pick(x, pre_max=3, post_max=3, pre_avg=12, post_avg=6,
                                             delta=delta, wait=wait), dtype=int)


def _velocities(strengths: np.ndarray) -> np.ndarray:
    """Normalise by the instrument's loud hits so dynamics are relative within the part."""
    if strengths.size == 0:
        return strengths
    ref = np.percentile(strengths, 90)
    a = np.clip(strengths / ref if ref > 0 else strengths, 0.0, 1.0) ** 0.8
    return np.round(VEL_MIN + a * (VEL_MAX - VEL_MIN)).astype(int)


def transcribe_drums(y: np.ndarray, sr: int) -> dict:
    import librosa

    yp = librosa.effects.percussive(y, margin=2.0)
    S = np.abs(librosa.stft(yp, n_fft=1024, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    nfrm = S.shape[1]

    fk = _norm(_flux(S, freqs, *BANDS["kick"]))
    fs = _norm(_flux(S, freqs, *BANDS["snare"]))
    fh = _norm(_flux(S, freqs, *BANDS["hat"]))
    eh = _energy(S, freqs, *BANDS["hat"])
    e_all = S.sum(axis=0)

    def near(arr, f):
        return float(arr[max(0, f - 1): f + 3].max())

    # 1. One onset list for all instruments: peaks of the summed band flux. Each band also gets
    #    its own peak list so quiet hits masked in the sum (e.g. ghost hats) still register.
    combined = _norm(fk + fs + fh)
    onsets = set(_peaks(combined, THRESHOLDS["onset"], sr).tolist())
    for band in (fk, fs, fh):
        for f in _peaks(band, THRESHOLDS["band"], sr):
            if all(abs(f - g) > 2 for g in onsets):
                onsets.add(int(f))

    # 2. Classify each onset by spectral shape (multi-label: kick + snare can coincide).
    d60, d100, d300 = int(0.06 * sr / HOP), int(0.10 * sr / HOP), int(0.30 * sr / HOP)
    events = []
    for f in sorted(onsets):
        k, s_, h = near(fk, f), near(fs, f), near(fh, f)
        snare_like = s_ >= SNARE_MIN and s_ >= SNARE_MID_HIGH_RATIO * h
        # A snare's own low end reaches the kick band; a kick under a snare needs more evidence.
        if (k >= KICK_WITH_SNARE) if snare_like else (k >= KICK_MIN or (k >= KICK_WEAK and k >= 1.5 * s_)):
            events.append((f, "kick", k))
        if snare_like:
            events.append((f, "snare", s_))
        # Hats/cymbals: high band clearly present and not just the top end of a snare hit
        if h >= HAT_MIN and (not snare_like or h >= HAT_OVER_SNARE * s_):
            peak = eh[f: f + 3].max() + 1e-12
            # Minimum over a window ignores re-triggering by the next hit.
            sustain_short = eh[min(nfrm - 1, f + d60): min(nfrm, f + d100 + 1)].min() / peak
            sustain_long = eh[min(nfrm - 1, f + d100): min(nfrm, f + d300 + 1)].min() / peak
            if sustain_long > CRASH_SUSTAIN:
                inst = "crash"
            elif sustain_short > OPEN_HAT_SUSTAIN:
                inst = "open_hat"
            else:
                inst = "closed_hat"
            events.append((f, inst, h))

    events.sort()
    times = librosa.frames_to_time(np.array([e[0] for e in events], dtype=int), sr=sr, hop_length=HOP) if events else []
    out, conf, counts = [], {}, {}
    for inst in INSTRUMENTS:
        idx = [i for i, e in enumerate(events) if e[1] == inst]
        if not idx:
            counts[inst], conf[inst] = 0, 0.0
            continue
        strengths = np.array([events[i][2] for i in idx])
        vels = _velocities(strengths)
        for i, v in zip(idx, vels):
            out.append({"time": round(float(times[i]), 4), "inst": inst, "note": GM[inst], "velocity": int(v)})
        counts[inst] = len(idx)
        conf[inst] = round(float(np.clip(np.median(strengths), 0.0, 1.0)), 3)  # refined in groove stage
    out.sort(key=lambda e: e["time"])

    duration = len(y) / sr
    density = len(out) / max(duration, 1e-6)

    def flatness(f, lo, hi):
        b = S[(freqs >= lo) & (freqs < hi), f: f + 3].mean(axis=1) + 1e-10
        return float(np.exp(np.mean(np.log(b))) / np.mean(b))

    # Lossy encoders low-pass the top band; empty bins would wreck the flatness measure, so it
    # only spans the frequencies the file actually contains.
    power_db = 10 * np.log10((S ** 2).mean(axis=1) + 1e-20)
    present = freqs[power_db > power_db.max() - 60]
    bandwidth = float(present.max()) if present.size else 11000.0
    hat_hi = min(11000.0, 0.95 * bandwidth)
    hat_lo = min(5000.0, 0.6 * hat_hi)
    hat_flat = [flatness(f, hat_lo, hat_hi) for f, inst, _ in events if inst in ("closed_hat", "open_hat", "crash")]
    snare_flat = [flatness(f, 1200, 5000) for f, inst, _ in events if inst == "snare"]
    hat_med = float(np.median(hat_flat)) if hat_flat else 0.0
    snare_med = float(np.median(snare_flat)) if snare_flat else 0.0
    if len(hat_flat) >= KIT_MIN_HAT_EVENTS:
        kit = hat_med >= KIT_HAT_FLATNESS
        kit_conf = float(np.clip((hat_med - 0.6) / 0.2, 0.0, 1.0))
    else:
        kit = snare_med >= KIT_SNARE_FLATNESS
        kit_conf = float(np.clip((snare_med - 0.6) / 0.3, 0.0, 1.0))
    enough = counts.get("kick", 0) + counts.get("snare", 0) >= 8

    return {
        "status": "ok" if kit and enough else "no_drums",
        "kitPresent": bool(kit),
        "kitConfidence": round(kit_conf, 3),
        "kitEvidence": {"hatFlatness": round(hat_med, 3), "snareFlatness": round(snare_med, 3),
                        "hatEvents": len(hat_flat), "bandwidthHz": int(bandwidth)},
        "events": out,
        "counts": counts,
        "confidence": conf,
        "hitsPerSecond": round(density, 2),
        "method": "hpss+band-onsets",
    }
