import numpy as np
import pytest

from app import grid, theory


def test_midi_formula():
    assert theory.midi_note(0, 4) == 60
    assert theory.midi_note(4, 4) == 64
    assert theory.midi_note(7, 4) == 67
    assert theory.note_name(60) == "C4"


def test_chord_relative_roundtrip():
    assert theory.to_chord_relative([60, 64, 67], 0) == [0, 4, 7]
    g = theory.from_chord_relative([0, 4, 7], theory.pitch_class("G"), 4)
    assert [theory.note_name(n) for n in g] == ["G4", "B4", "D5"]
    d = theory.from_chord_relative([0, 4, 7], theory.pitch_class("D"), 4)
    assert [n % 12 for n in d] == [2, 6, 9]  # D F# A


def test_clamp_to_range():
    lo, hi = theory.TRACK_RANGES["bass"]
    assert theory.clamp_to_range(70, lo, hi) == 46
    assert theory.clamp_to_range(20, lo, hi) == 32


def test_key_signature():
    assert theory.key_signature_accidentals(7, "major") == 1     # G major: 1 sharp
    assert theory.key_signature_accidentals(2, "minor") == -1    # D minor: 1 flat
    assert theory.key_name(3, "major") == "Eb Major"


def test_bpm_formula():
    beats = np.arange(0, 30, 0.625)  # 96 BPM
    assert grid.bpm_from_beats(beats) == pytest.approx(96.0)
    glitched = np.sort(np.r_[beats, 10.3])  # one spurious extra beat
    assert grid.bpm_from_beats(glitched) == pytest.approx(96.0, abs=0.5)


def test_ticks():
    assert grid.ticks_for_bars(1, 480) == 1920
    assert grid.ticks_for_bars(8, 480) == 15360
    assert grid.seconds_per_beat(90) == pytest.approx(0.6667, abs=1e-4)


@pytest.mark.parametrize("strength,expected", [(1.0, 0.5), (0.5, 0.52), (0.25, 0.53), (0.75, 0.51)])
def test_quantize_uniform(strength, expected):
    assert grid.quantize_uniform(0.54, 0.25, strength) == pytest.approx(expected)


def test_subdivide_and_quantize_to_tracked_grid():
    beats = np.array([0.0, 0.5, 1.0, 1.52])  # slight drift
    g16 = grid.subdivide(beats, 4)
    assert len(g16) == 4 * 4 + 1
    assert g16[1] == pytest.approx(0.125)
    q = grid.quantize_to_grid([0.13, 1.06], g16, 1.0)
    assert q[0] == pytest.approx(0.125)
    assert q[1] == pytest.approx(1.0)  # 1.06 is 0.06 from 1.0 and 0.07 from 1.13
