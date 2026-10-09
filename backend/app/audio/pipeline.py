"""Analysis pipeline orchestrator.

Each stage is a function (ctx) -> None that reads/writes the shared context dict. Stages that
belong to later phases are registered as `planned` so the API and UI can show the full roadmap
without pretending to run them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config
from . import key as key_mod
from . import drums as drums_mod
from . import loader, tempo

ProgressFn = Callable[[str, float, str], None]


@dataclass
class Stage:
    id: str
    label: str
    phase: int
    run: Callable[[dict], None] | None  # None = not implemented yet
    weight: float = 1.0


def _decode(ctx):
    ctx["y"] = loader.load_mono(ctx["path"], sr=config.ANALYSIS_SR)
    ctx["sr"] = config.ANALYSIS_SR


def _tempo(ctx):
    ctx["result"]["tempo"] = tempo.analyze_tempo(ctx["y"], ctx["sr"])


def _key(ctx):
    ctx["result"]["key"] = key_mod.analyze_key(ctx["y"], ctx["sr"])


RHYTHM_MIN_ALIGNMENT = 0.6  # random hit placement scores ≈ 0.3


def _drums(ctx):
    from ..groove import rhythm_alignment

    d = drums_mod.transcribe_drums(ctx["y"], ctx["sr"])
    beats = ctx["result"].get("tempo", {}).get("beats") or []
    d["gridAlignment"] = rhythm_alignment(d["events"], beats)
    if not d["kitPresent"]:
        d["reason"] = "The detected hits are tonal (piano, strings, etc.), not a drum kit."
    elif d["gridAlignment"]["best"] < RHYTHM_MIN_ALIGNMENT:
        d["status"] = "no_drums"
        d["reason"] = "The percussive sounds don't follow the beat grid, so they aren't a drum groove."
    ctx["result"]["drums"] = d


STAGES: list[Stage] = [
    Stage("decode", "Decoding audio", 1, _decode, 1.0),
    Stage("tempo", "Detecting tempo, beats & meter", 2, _tempo, 2.0),
    Stage("key", "Detecting key", 3, _key, 3.0),
    Stage("drums", "Extracting the drum groove", 6, _drums, 3.0),
    Stage("chords", "Chord detection", 4, None),
    Stage("bass", "Bass / chord-part extraction", 6, None),
    Stage("sections", "Section detection", 8, None),
    Stage("style", "Style generation", 10, None),
]


def _warnings(result: dict) -> list[dict]:
    w = []
    t, k = result.get("tempo", {}), result.get("key", {})
    if t.get("status") == "no_beat":
        w.append({"code": "no_beat", "severity": "error",
                  "message": "No steady beat was found. This may be a free-tempo or ambient recording. Enter the BPM manually."})
    else:
        if t.get("bpmConfidence", 1) < config.LOW_CONFIDENCE:
            w.append({"code": "low_bpm_confidence", "severity": "warning",
                      "message": "Tempo confidence is low. Check the BPM. Half or double time may fit better."})
        if (t.get("tempoStability") or 1) < 0.5:
            w.append({"code": "variable_tempo", "severity": "info",
                      "message": "The tempo drifts during the song (a live recording or rubato?). The beat grid follows the drift."})
        if t.get("timeSignatureConfidence", 1) < config.LOW_CONFIDENCE:
            w.append({"code": "low_meter_confidence", "severity": "warning",
                      "message": "The time signature and downbeat are uncertain. Listen with the metronome and correct them if needed."})
    if k.get("status") == "no_tonal_content":
        w.append({"code": "no_key", "severity": "error",
                  "message": "No tonal content was found (drums only?). Set the key manually."})
    else:
        if k.get("keyConfidence", 1) < config.LOW_CONFIDENCE:
            w.append({"code": "low_key_confidence", "severity": "warning",
                      "message": "Key confidence is low. Compare the top candidates and correct the key if needed."})
        if k.get("keyStability") is not None and k["keyStability"] < 0.6:
            w.append({"code": "modulation", "severity": "info",
                      "message": "The key changes during the song. The main key is the one heard most often."})
    d = result.get("drums")
    if d is not None and d.get("status") != "ok":
        w.append({"code": "no_drums", "severity": "warning",
                  "message": "No drum groove was found. " + (d.get("reason") or "")
                             + " A rhythm style can't be built from this recording."})
    return w


def run_analysis(path: Path, on_progress: ProgressFn | None = None) -> dict:
    import librosa

    active = [s for s in STAGES if s.run]
    total = sum(s.weight for s in active)
    done = 0.0
    ctx = {"path": path, "result": {}}
    timings = {}

    for s in active:
        if on_progress:
            on_progress(s.id, done / total, s.label)
        t0 = time.perf_counter()
        s.run(ctx)
        timings[s.id] = round(time.perf_counter() - t0, 2)
        done += s.weight
    if on_progress:
        on_progress("done", 1.0, "Analysis complete")

    result = ctx["result"]
    result.update({
        "version": 1,
        "analyzedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration": round(len(ctx["y"]) / ctx["sr"], 3),
        "engine": {"name": "librosa", "version": librosa.__version__, "numpy": np.__version__, "sampleRate": ctx["sr"]},
        "timings": timings,
        "stages": [{"id": s.id, "label": s.label, "phase": s.phase, "status": "done" if s.run else "planned"} for s in STAGES],
    })
    result["warnings"] = _warnings(result)
    return result


def retrack_beats(path: Path, bpm: float) -> list[float]:
    """Beat grid at a user-supplied tempo (BPM corrections). The user's tempo always wins:
    the tracker supplies the phase; if it settles at a multiple of the requested tempo, its beats
    are subdivided or thinned, and for any other ratio a uniform grid is laid from its first beat."""
    import librosa

    from ..grid import bpm_from_beats, subdivide

    y = loader.load_mono(path, sr=config.ANALYSIS_SR)
    sr = config.ANALYSIS_SR
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=config.HOP_LENGTH, aggregate=np.median)
    env = tempo.phase_envelope(env, tempo.drum_envelope(y, sr, config.HOP_LENGTH))
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=config.HOP_LENGTH,
                                        start_bpm=bpm, tightness=800, trim=True)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=config.HOP_LENGTH)
    got = bpm_from_beats(beats) if len(beats) >= 2 else None

    if got is None:
        start, end = 0.0, len(y) / sr
        beats = np.arange(start, end, 60.0 / bpm)
    else:
        ratio = bpm / got
        k = round(ratio) if ratio >= 1 else round(1 / ratio)
        if abs(ratio - 1) <= 0.03:
            pass
        elif ratio > 1 and k in (2, 3, 4) and abs(ratio - k) / k <= 0.03:
            beats = subdivide(beats, k)[:-1]
        elif ratio < 1 and k in (2, 3, 4) and abs(1 / ratio - k) / k <= 0.03:
            beats = beats[::k]
        else:
            beats = np.arange(beats[0], len(y) / sr, 60.0 / bpm)
    return [round(float(t), 4) for t in beats]
