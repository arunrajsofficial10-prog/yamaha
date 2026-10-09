"""Real recordings (librosa's CC-licensed example tracks), re-encoded to MP3 and analysed through
the full decode path. Skipped when the examples can't be downloaded."""
import io

import numpy as np
import pytest
import soundfile as sf

from app.audio import loader
from app.audio.key import analyze_key
from app.audio.tempo import analyze_tempo

librosa = pytest.importorskip("librosa")
SR = 22050


def _example_as_mp3(name, tmp_path, duration=None):
    try:
        path = librosa.ex(name)
    except Exception as e:  # offline
        pytest.skip(f"example {name!r} unavailable: {e}")
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    if duration:
        y = y[: int(duration * sr)]
    mp3 = tmp_path / f"{name}.mp3"
    sf.write(mp3, y, sr, format="MP3")
    return mp3


@pytest.mark.parametrize("name,expected", [
    ("nutcracker", (4, "minor")),   # Dance of the Sugar Plum Fairy — E minor
    ("brahms", (7, "minor")),       # Hungarian Dance No. 5, orchestral arrangement — G minor
])
def test_key_on_real_mp3(tmp_path, name, expected):
    mp3 = _example_as_mp3(name, tmp_path)
    assert loader.sniff_format(mp3.read_bytes()[:16]) == "mp3"
    meta = loader.read_metadata(mp3, "mp3")
    y = loader.load_mono(mp3)
    assert len(y) / SR == pytest.approx(meta.duration_sec, abs=0.2)
    r = analyze_key(y, SR)
    assert (r["tonic"], r["mode"]) == expected


@pytest.mark.parametrize("name", ["choice", "fishin", "pistachio", "vibeace"])
def test_tempo_on_real_mp3_is_steady(tmp_path, name):
    """No published ground truth for these tracks, so check internal consistency: a steady grid
    that covers the track, BPM agreeing with the beat timestamps, and sane confidence."""
    mp3 = _example_as_mp3(name, tmp_path, duration=90)
    y = loader.load_mono(mp3)
    r = analyze_tempo(y, SR)
    assert r["status"] == "ok"
    assert 50 <= r["bpm"] <= 200
    dt = np.diff(r["beats"])
    assert 60.0 / np.median(dt) == pytest.approx(r["bpm"], rel=0.03)
    assert r["beats"][-1] - r["beats"][0] > 0.6 * len(y) / SR  # intros/outros may be beatless
    assert r["timeSignature"] in ("3/4", "4/4")


@pytest.mark.parametrize("name,has_drums", [
    ("choice", True),        # drum & bass loop
    ("vibeace", True),       # electronic groove
    ("fishin", True),        # shuffle with drums
    ("pistachio", False),    # solo piano ragtime: tonal attacks only
    ("brahms", False),       # string orchestra
    ("humpback", False),     # whale song + ocean noise: noisy but not rhythmic
])
def test_drum_groove_gate_on_real_recordings(tmp_path, name, has_drums):
    from app.audio.pipeline import run_analysis

    mp3 = _example_as_mp3(name, tmp_path, duration=90)
    d = run_analysis(mp3)["drums"]
    assert (d["status"] == "ok") is has_drums, d.get("reason")


def test_shuffle_uses_triplet_grid(tmp_path):
    from app.groove import extract_style
    from app.keyboards.profiles import LAYOUT_PORTABLE

    mp3 = _example_as_mp3("fishin", tmp_path, duration=90)
    y = loader.load_mono(mp3)
    from app.audio.drums import transcribe_drums

    t = analyze_tempo(y, SR)
    st = extract_style(transcribe_drums(y, SR)["events"], t["beats"], 4, t["downbeatPhase"], LAYOUT_PORTABLE)
    assert st["subdivision"] == 3
