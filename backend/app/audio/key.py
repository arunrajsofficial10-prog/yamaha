"""Key detection (Phase 3).

  1. Estimate tuning deviation (songs are often a few cents off A440).
  2. Harmonic/percussive separation; drums add broadband noise to chroma.
  3. Constant-Q chroma → 12-bin pitch-class profile (loudness-weighted sum over the song),
     square-root compressed so the tonic/dominant don't drown out the mode-defining third.
  4. Compare against the 24 rotated Albrecht–Shanahan (2013) major/minor templates:
         score(key) = Σ profile[i] × key_template[i]            (spec §7)
     Both vectors are mean-centred and unit-normalised first, which makes the dot product the
     Pearson correlation (the Krumhansl–Schmuckler algorithm) and removes loudness bias.
  5. Repeat per ~20 s window to measure key stability / detect modulations.
"""
from __future__ import annotations

import numpy as np

from .. import config
from ..theory import PITCH_CLASSES, key_name

# Albrecht & Shanahan (2013) corpus-derived key profiles, tonic = index 0. Chosen over
# Krumhansl–Kessler after comparison on synthetic progressions and real recordings: KK confused
# A minor with A major when the minor third was quiet relative to tonic and fifth.
MAJOR_PROFILE = np.array([0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081])
MINOR_PROFILE = np.array([0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052])

WINDOW_SEC = 20.0


def _unit(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


_TEMPLATES = [(pc, mode, _unit(np.roll(t, pc))) for mode, t in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)) for pc in range(12)]


def score_keys(profile: np.ndarray) -> list[dict]:
    p = _unit(np.sqrt(np.maximum(np.asarray(profile, dtype=float), 0.0)))
    out = [{"tonic": pc, "mode": mode, "score": float(p @ tpl)} for pc, mode, tpl in _TEMPLATES]
    out.sort(key=lambda k: k["score"], reverse=True)
    return out


def _confidence(ranked: list[dict]) -> float:
    best, second = ranked[0]["score"], ranked[1]["score"]
    # Relative major/minor share the same pitch set, so they are always close; a small gap to them
    # is expected and shouldn't collapse confidence entirely.
    return float(np.clip(0.6 * max(best, 0.0) + 0.4 * min(1.0, (best - second) / 0.12), 0.0, 1.0))


def analyze_key(y: np.ndarray, sr: int, hop: int = config.HOP_LENGTH) -> dict:
    import librosa

    tuning = float(librosa.estimate_tuning(y=y, sr=sr))
    y_harm = librosa.effects.harmonic(y, margin=3.0)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=hop, tuning=tuning, n_chroma=12)

    # Weight frames by loudness so quiet intros / fades don't dominate
    rms = librosa.feature.rms(y=y_harm, hop_length=hop)[0]
    n = min(chroma.shape[1], rms.shape[0])
    chroma, rms = chroma[:, :n], rms[:n]
    profile = (chroma * rms[None, :]).sum(axis=1)
    if profile.sum() <= 1e-9:
        return {"status": "no_tonal_content", "key": None, "keyConfidence": 0.0}
    profile = profile / profile.max()

    ranked = score_keys(profile)
    best = ranked[0]

    # Windowed keys → stability / modulation detection
    frames_per_win = int(WINDOW_SEC * sr / hop)
    windows = []
    for start in range(0, n, frames_per_win):
        seg = (chroma[:, start : start + frames_per_win] * rms[None, start : start + frames_per_win]).sum(axis=1)
        if seg.sum() <= 1e-9 or (min(n, start + frames_per_win) - start) < frames_per_win // 3:
            continue
        r = score_keys(seg)[0]
        windows.append({
            "start": round(start * hop / sr, 2),
            "end": round(min(n, start + frames_per_win) * hop / sr, 2),
            "key": key_name(r["tonic"], r["mode"]),
            "tonic": r["tonic"], "mode": r["mode"], "score": round(r["score"], 3),
        })
    agree = [w for w in windows if w["tonic"] == best["tonic"] and w["mode"] == best["mode"]]
    stability = len(agree) / len(windows) if windows else None

    return {
        "status": "ok",
        "tonic": best["tonic"],
        "tonicName": PITCH_CLASSES[best["tonic"]],
        "mode": best["mode"],
        "key": key_name(best["tonic"], best["mode"]),
        "keyConfidence": round(_confidence(ranked), 3),
        "keyStability": None if stability is None else round(stability, 3),
        "tuningCents": round(tuning * 100, 1),
        "pitchClassProfile": [round(float(v), 4) for v in profile],
        "candidates": [
            {"key": key_name(k["tonic"], k["mode"]), "tonic": k["tonic"], "mode": k["mode"], "score": round(k["score"], 4)}
            for k in ranked[:6]
        ],
        "windows": windows,
    }
