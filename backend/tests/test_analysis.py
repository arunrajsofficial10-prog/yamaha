"""Detector accuracy on synthesised songs with known tempo / meter / key."""
import numpy as np
import pytest

from app.audio.key import analyze_key
from app.audio.tempo import analyze_tempo
from app.projects import beats_per_bar, label_beats

from .synth import MINOR_PROG, POP_MAJOR, SR, song


@pytest.mark.parametrize("bpm", [84.0, 96.0, 120.0, 140.0])
def test_bpm_4_4(bpm):
    y, truth = song(bpm=bpm, bars=20)
    r = analyze_tempo(y, SR)
    assert r["status"] == "ok"
    assert r["bpm"] == pytest.approx(bpm, rel=0.02)
    assert r["timeSignature"] == "4/4"
    assert r["bpmConfidence"] > 0.6


def test_downbeats_4_4_with_pickup():
    y, truth = song(bpm=100.0, bars=20, lead_in=60.0 / 100.0)  # one pickup beat
    r = analyze_tempo(y, SR)
    _, downbeats = label_beats(r["beats"], 4, r["downbeatPhase"])
    # every detected downbeat should sit within 70 ms of a true downbeat
    err = [min(abs(d - t) for t in truth["downbeats"]) for d in downbeats]
    assert np.median(err) < 0.07


def test_waltz_3_4():
    y, truth = song(bpm=150.0, beats_per_bar=3, bars=32, progression=POP_MAJOR)
    r = analyze_tempo(y, SR)
    assert r["bpm"] == pytest.approx(150.0, rel=0.02)
    assert r["timeSignature"] == "3/4"


def test_silence_has_no_beat():
    r = analyze_tempo(np.zeros(SR * 10, dtype=np.float32), SR)
    assert r["status"] == "no_beat"
    assert r["bpm"] is None


@pytest.mark.parametrize("offset,expected", [(0, (0, "major")), (3, (3, "major")), (7, (7, "major"))])
def test_major_keys(offset, expected):
    y, _ = song(bpm=110.0, bars=16, progression=POP_MAJOR, key_offset=offset)
    r = analyze_key(y, SR)
    assert (r["tonic"], r["mode"]) == expected


@pytest.mark.parametrize("offset", [0, 7])  # A minor, E minor
def test_minor_key(offset):
    y, _ = song(bpm=110.0, bars=16, progression=MINOR_PROG, key_offset=offset)
    r = analyze_key(y, SR)
    assert (r["tonic"], r["mode"]) == ((9 + offset) % 12, "minor")
    assert len(r["pitchClassProfile"]) == 12


def test_compound_meter_pulse():
    assert beats_per_bar("6/8") == 2
    assert beats_per_bar("12/8") == 4
    assert beats_per_bar("7/8") == 7
