"""Beat grid construction and quantization (spec §5, §6, §15, §16).

Times are in seconds unless noted. A "beat grid" is the tracked (possibly non-uniform) beat
timeline; subdividing it gives the 8th / 16th grid that detected events are snapped to.
"""
from __future__ import annotations

import numpy as np

QUANTIZE_STRENGTHS = (0.25, 0.5, 0.75, 1.0)


def bpm_from_beats(beat_times: np.ndarray) -> float | None:
    """BPM = 60 / Δt_avg, where Δt_avg = ΣΔt_i / (n − 1)  (spec §4).

    Intervals far from the median (dropped / doubled beats) are excluded first so a single
    tracking glitch doesn't skew the average.
    """
    if len(beat_times) < 2:
        return None
    dt = np.diff(beat_times)
    med = np.median(dt)
    inliers = dt[(dt > 0.75 * med) & (dt < 1.25 * med)]
    if inliers.size == 0:
        inliers = dt
    return float(60.0 / inliers.mean())


def seconds_per_beat(bpm: float) -> float:
    return 60.0 / bpm


def ticks_for_bars(bars: int, ppq: int, beats_per_bar: int = 4) -> int:
    """ticks = N × beats_per_bar × PPQ  (spec §15)."""
    return bars * beats_per_bar * ppq


def seconds_to_ticks(t: float, bpm: float, ppq: int) -> int:
    return int(round(t / seconds_per_beat(bpm) * ppq))


def subdivide(beat_times: np.ndarray, per_beat: int) -> np.ndarray:
    """Split each beat interval into `per_beat` equal steps (2 = 8ths, 4 = 16ths in 4/4)."""
    beat_times = np.asarray(beat_times, dtype=float)
    if len(beat_times) < 2 or per_beat <= 1:
        return beat_times.copy()
    # Extend by one interval so events after the last beat still have a grid to snap to.
    ext = np.append(beat_times, beat_times[-1] + (beat_times[-1] - beat_times[-2]))
    steps = np.arange(per_beat) / per_beat
    grid = (ext[:-1, None] + np.diff(ext)[:, None] * steps[None, :]).ravel()
    return np.append(grid, ext[-1])


def quantize_uniform(t: float, grid_interval: float, strength: float = 1.0) -> float:
    """nearest_grid = round(t / grid_interval) × grid_interval;
    quantized = t + strength × (nearest_grid − t)  (spec §6)."""
    nearest = round(t / grid_interval) * grid_interval
    return t + strength * (nearest - t)


def quantize_to_grid(times: np.ndarray, grid: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Same formula as `quantize_uniform`, but against a tracked (non-uniform) grid."""
    times = np.asarray(times, dtype=float)
    grid = np.asarray(grid, dtype=float)
    idx = np.clip(np.searchsorted(grid, times), 1, len(grid) - 1)
    left, right = grid[idx - 1], grid[idx]
    nearest = np.where(times - left <= right - times, left, right)
    return times + strength * (nearest - times)
