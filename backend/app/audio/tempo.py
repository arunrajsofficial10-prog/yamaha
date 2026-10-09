"""Tempo, beat, meter and downbeat analysis (Phase 2).

Pipeline:
  1. Onset-strength envelope (spectral flux, median-aggregated over mel bands).
  2. Dynamic-programming beat tracker (librosa / Ellis 2007) → beat timestamps.
  3. BPM = 60 / mean inter-beat interval (spec §4).
  4. Half/double-time check (BPM vs 2×BPM vs BPM/2) from rhythmic structure: where kick and
     snare energy lands relative to the tracked beats; re-track locked to the new tempo if needed.
  5. Meter (3 vs 4 beats per bar) and downbeat phase from per-beat accent features:
     low-frequency onsets (kick), harmonic change (chords tend to change on bar lines) and
     beat-synchronous chroma periodicity.
"""
from __future__ import annotations

import numpy as np

from .. import config
from ..grid import bpm_from_beats

PRIOR_CENTER_BPM = 115.0
PRIOR_WIDTH_OCTAVES = 1.0
# Half/double thresholds, calibrated on synthetic grooves (correct ≤ 0.3, half-tracked ≥ 1.1)
# and the librosa example recordings (0.2–0.8, all tracked plausibly).
DOUBLE_MID_RATIO = 0.9
HALF_ALTERNATION = 0.4


def _tempo_prior(bpm: float) -> float:
    return float(np.exp(-0.5 * (np.log2(bpm / PRIOR_CENTER_BPM) / PRIOR_WIDTH_OCTAVES) ** 2))


def _salience(ac: np.ndarray, bpm: float, sr: int, hop: int) -> float:
    """Autocorrelation strength of the onset envelope at the lag of `bpm` (±1 lag tolerance)."""
    lag = 60.0 * sr / (hop * bpm)
    lo, hi = int(np.floor(lag)) - 1, int(np.ceil(lag)) + 1
    lo, hi = max(lo, 1), min(hi, len(ac) - 1)
    if lo > hi:
        return 0.0
    return float(np.max(ac[lo : hi + 1]))


def _track(onset_env, sr, hop, start_bpm, tightness):
    import librosa

    _, frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=hop, start_bpm=start_bpm, tightness=tightness, trim=True
    )
    return np.asarray(frames, dtype=int)


def _per_beat(env: np.ndarray, beat_frames: np.ndarray, radius: int = 2) -> np.ndarray:
    out = np.empty(len(beat_frames))
    for i, f in enumerate(beat_frames):
        out[i] = env[max(0, f - radius) : f + radius + 1].max()
    return out


def _z(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-9 else np.zeros_like(x)


def _estimate_meter(y, sr, hop, beat_frames, onset_env):
    """Return (beats_per_bar, downbeat_phase, confidence, detail)."""
    import librosa

    n = len(beat_frames)
    if n < 16:
        return 4, 0, 0.0, {"reason": "too few beats"}

    low_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=8, fmax=180)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    # sync() pads with frame 0, so column 0 is the lead-in before the first beat; drop it so
    # column k is the segment that *starts* at beat k.
    beat_chroma = librosa.util.sync(chroma, beat_frames, aggregate=np.median)[:, 1 : n + 1]
    beat_chroma = librosa.util.normalize(beat_chroma, norm=2, axis=0)

    # Harmonic change arriving at each beat: 1 − cos(chroma[k−1], chroma[k])
    sim_prev = np.r_[1.0, np.sum(beat_chroma[:, 1:] * beat_chroma[:, :-1], axis=0)]
    harmonic_change = 1.0 - sim_prev[:n]

    accent = _z(_per_beat(low_env, beat_frames)) + _z(harmonic_change) + 0.5 * _z(_per_beat(onset_env, beat_frames))

    def phase_score(m):
        scores = []
        for p in range(m):
            on = accent[p::m]
            off = np.delete(accent, np.arange(p, n, m))
            scores.append(on.mean() - off.mean())
        best = int(np.argmax(scores))
        return best, float(scores[best])

    # Chroma self-similarity at bar-length lags: bars repeat, so lags that are multiples of the
    # bar length should be more similar than others.
    def lag_sim(L):
        return float(np.mean(np.sum(beat_chroma[:, L:] * beat_chroma[:, :-L], axis=0))) if n > L else 0.0

    sims = {L: lag_sim(L) for L in range(1, 17)}
    base = np.mean(list(sims.values()))
    period = {3: np.mean([sims[3], sims[6], sims[12]]) - base, 4: np.mean([sims[4], sims[8], sims[16]]) - base}

    results = {}
    for m in (3, 4):
        phase, acc = phase_score(m)
        results[m] = {"phase": phase, "accent": acc, "period": float(period[m]), "score": acc + 4.0 * float(period[m])}

    # Most popular music is 4/4, so 3/4 must win clearly before we switch.
    margin = results[3]["score"] - results[4]["score"]
    meter = 3 if margin > 0.25 else 4
    other = 4 if meter == 3 else 3
    gap = results[meter]["score"] - results[other]["score"]
    conf = float(0.5 + 0.5 * np.tanh(gap))  # gap 0 → 0.5 (coin flip), saturates slowly
    # Phase confidence: how much the chosen downbeat position stands out
    phase_conf = float(np.clip(results[meter]["accent"] / 1.5, 0.0, 1.0))
    return meter, results[meter]["phase"], conf, {"candidates": results, "downbeatConfidence": round(phase_conf, 3)}


def drum_envelope(y: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """Kick (<150 Hz) + snare-body (150–2000 Hz) onset strength, each normalised."""
    import librosa

    low = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=8, fmax=150)
    mid = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=32, fmin=150, fmax=2000)
    return low / max(low.max(), 1e-9) + mid / max(mid.max(), 1e-9)


def phase_envelope(onset_env: np.ndarray, drum: np.ndarray) -> np.ndarray:
    n = min(len(onset_env), len(drum))
    return 0.5 * onset_env[:n] / max(onset_env.max(), 1e-9) + drum[:n]


def analyze_tempo(y: np.ndarray, sr: int, hop: int = config.HOP_LENGTH) -> dict:
    import librosa

    duration = len(y) / sr
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)

    empty = {
        "bpm": None, "bpmConfidence": 0.0, "bpmCandidates": [], "beats": [], "downbeats": [],
        "timeSignature": None, "timeSignatureConfidence": 0.0, "downbeatConfidence": 0.0,
        "tempoStability": None, "firstDownbeat": None,
    }
    if onset_env.max() <= 1e-6:
        return {**empty, "status": "no_beat"}

    tg = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr, hop_length=hop, win_length=512)
    ac = tg.mean(axis=1)
    ac = ac / ac[0] if ac[0] > 0 else ac

    beat_frames = _track(onset_env, sr, hop, start_bpm=PRIOR_CENTER_BPM, tightness=100)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    tracked_bpm = bpm_from_beats(beat_times)
    if tracked_bpm is None or len(beat_times) < 8:
        return {**empty, "status": "no_beat"}

    # --- half / double-time resolution (spec §4) -------------------------------------------
    # Autocorrelation alone favours slower tempos (a pattern repeating every beat also repeats
    # every two beats), so the decision uses rhythmic structure: where do kick + snare land?
    #   * Midpoints between tracked beats as drum-heavy as the beats → the real pulse is twice
    #     as fast (tracker locked onto half-time, e.g. kick on "1", snare on "&").
    #   * Every other tracked beat nearly empty of kick/snare → the tracker locked onto 8th notes.
    drum = drum_envelope(y, sr, hop)
    on_beat = _per_beat(drum, beat_frames[:-1])
    midpoints = (beat_frames[:-1] + beat_frames[1:]) // 2
    mid_ratio = float(_per_beat(drum, midpoints).mean() / max(on_beat.mean(), 1e-9))
    even, odd = on_beat[0::2].mean(), on_beat[1::2].mean()
    alternation = float(min(even, odd) / max(even, odd, 1e-9))

    relation = "detected"
    if mid_ratio >= DOUBLE_MID_RATIO and tracked_bpm * 2 <= config.BPM_MAX:
        relation = "double"
    elif alternation < HALF_ALTERNATION and tracked_bpm / 2 >= config.BPM_MIN:
        relation = "half"
    mult = {"detected": 1.0, "double": 2.0, "half": 0.5}

    if relation != "detected":
        beat_frames = _track(onset_env, sr, hop, start_bpm=tracked_bpm * mult[relation], tightness=400)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    bpm = bpm_from_beats(beat_times)

    # Phase lock: with the tempo settled, re-track on kick/snare-weighted onsets. Loud off-beat
    # cymbals (open hats on the "&") otherwise pull the grid half a beat out of phase in sections.
    beat_frames = _track(phase_envelope(onset_env, drum), sr, hop, start_bpm=bpm, tightness=400)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    bpm = bpm_from_beats(beat_times)

    # Candidate list (relative to the final BPM) with autocorrelation × prior scores for display.
    cands = []
    for rel, m in (("detected", 1.0), ("double", 2.0), ("half", 0.5)):
        b = bpm * m
        if config.BPM_MIN <= b <= config.BPM_MAX:
            sal = _salience(ac, b, sr, hop)
            cands.append({"bpm": round(b, 2), "relation": rel, "score": sal * _tempo_prior(b)})
    # How decisively the structural evidence settles the octave (0 = on the fence).
    octave_clarity = float(np.clip(max(abs(mid_ratio - DOUBLE_MID_RATIO) / 0.6,
                                       (HALF_ALTERNATION - alternation) / 0.3 if relation == "half" else 0.0), 0.0, 1.0))

    # --- confidence ---------------------------------------------------------------------------
    dt = np.diff(beat_times)
    cv = float(dt.std() / dt.mean())
    regularity = float(np.clip(1.0 - 4.0 * cv, 0.0, 1.0))
    coverage = float(np.clip((beat_times[-1] - beat_times[0]) / max(duration - 2.0, 1e-6), 0.0, 1.0))
    bpm_conf = regularity * (0.7 + 0.3 * octave_clarity) * (0.5 + 0.5 * coverage)

    # Local tempo over ~8-beat windows to flag tempo drift
    local = [60.0 / np.median(dt[i : i + 8]) for i in range(0, max(1, len(dt) - 7), 4)]
    stability = float(np.clip(1.0 - np.std(local) / max(np.mean(local), 1e-6) * 10.0, 0.0, 1.0))

    # --- meter & downbeats (spec §5) -------------------------------------------------------------
    meter, phase, meter_conf, meter_detail = _estimate_meter(y, sr, hop, beat_frames, onset_env)

    return {
        "status": "ok",
        "bpm": round(bpm, 2),
        "bpmConfidence": round(float(bpm_conf), 3),
        "bpmCandidates": [
            {"bpm": c["bpm"], "relation": c["relation"], "score": round(c["score"], 4)} for c in cands
        ],
        "tempoOctave": {"trackerBpm": round(tracked_bpm, 2), "applied": relation,
                        "midpointDrumRatio": round(mid_ratio, 3), "beatAlternation": round(alternation, 3)},
        "tempoStability": round(stability, 3),
        "localTempo": [round(float(b), 2) for b in local],
        "timeSignature": f"{meter}/4",
        "timeSignatureConfidence": round(meter_conf, 3),
        "downbeatConfidence": meter_detail.get("downbeatConfidence", 0.0),
        "downbeatPhase": phase,
        "beats": [round(float(t), 4) for t in beat_times],
        "meterDetail": meter_detail,
    }


def label_beats(beat_times: list[float], beats_per_bar: int, phase: int) -> tuple[list[dict], list[float]]:
    """Assign bar / beat-in-bar numbers. Beats before the first downbeat form a pickup (bar 0)."""
    labelled, downbeats = [], []
    for i, t in enumerate(beat_times):
        rel = i - phase
        bar = rel // beats_per_bar + 1
        beat = rel % beats_per_bar + 1
        labelled.append({"time": t, "bar": int(bar), "beat": int(beat)})
        if beat == 1:
            downbeats.append(t)
    return labelled, downbeats
