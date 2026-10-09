"""Groove fidelity and style-file structure."""
import io

import mido
import numpy as np
import pytest

from app import groove, styles
from app.audio.drums import transcribe_drums
from app.audio.tempo import analyze_tempo
from app.keyboards import profiles, sff
from app.keyboards.profiles import LAYOUT_ARRANGER, LAYOUT_PORTABLE

from .synth import SR, drum_song


@pytest.fixture(scope="module", params=[0.0, 0.33], ids=["straight", "swing33"])
def analysed(request):
    y, truth = drum_song(swing=request.param)
    t = analyze_tempo(y, SR)
    d = transcribe_drums(y, SR)
    return {"truth": truth, "tempo": t, "drums": d, "swing": request.param}


def _effective(t, bpb=4):
    from app.audio.tempo import label_beats

    labelled, downbeats = label_beats(t["beats"], bpb, t["downbeatPhase"])
    return {"bpm": t["bpm"], "beats": labelled, "downbeats": downbeats, "beatsPerBar": bpb,
            "downbeatPhase": t["downbeatPhase"], "timeSignature": "4/4", "keySignature": 0, "mode": "major"}


def _slots(section, inst):
    return sorted({e["slot"] for e in section["events"] if e["inst"] == inst and e["bar"] == 0})


def test_main_grooves_match_the_song(analysed):
    st = groove.extract_style(analysed["drums"]["events"], analysed["tempo"]["beats"], 4,
                              analysed["tempo"]["downbeatPhase"], LAYOUT_PORTABLE)
    mains = [st["sections"]["Main A"], st["sections"]["Main B"]]
    grooves = {(tuple(_slots(m, "kick")), tuple(_slots(m, "snare"))) for m in mains}
    assert ((0, 7, 10), (4, 12)) in grooves            # verse
    assert ((0, 3, 8, 10), (4, 12, 15)) in grooves      # chorus
    assert all(m["kind"] == "extracted" for m in mains)


def test_swing_is_preserved(analysed):
    st = groove.extract_style(analysed["drums"]["events"], analysed["tempo"]["beats"], 4,
                              analysed["tempo"]["downbeatPhase"], LAYOUT_PORTABLE)
    assert st["feel"]["swingPercent"] == pytest.approx(analysed["swing"] * 100, abs=6)
    verse = next(s for s in (st["sections"]["Main A"], st["sections"]["Main B"]) if _slots(s, "kick") == [0, 7, 10])
    hats_off = [e for e in verse["events"] if e["inst"] == "closed_hat" and e["slot"] % 4 == 2]
    offsets = [e["pos"] - e["slot"] / 4 for e in hats_off]
    assert np.median(offsets) == pytest.approx(analysed["swing"] / 4, abs=0.02)


def test_fills_are_the_songs_own(analysed):
    st = groove.extract_style(analysed["drums"]["events"], analysed["tempo"]["beats"], 4,
                              analysed["tempo"]["downbeatPhase"], LAYOUT_ARRANGER)
    fill = st["sections"]["Fill In AA"]
    assert fill["kind"] == "extracted" and fill["bars"] == 1
    assert len(_slots(fill, "snare")) >= 10                   # the snare roll


def _parse(data, ppq):
    mid = mido.MidiFile(file=io.BytesIO(data))
    assert mid.type == 0 and len(mid.tracks) == 1 and mid.ticks_per_beat == ppq
    t, markers, channels, notes = 0, [], set(), 0
    for msg in mid.tracks[0]:
        t += msg.time
        if msg.type == "marker":
            markers.append((msg.text, t))
        elif msg.type == "note_on":
            channels.add(msg.channel)
            notes += msg.velocity > 0
    return mid, markers, channels, notes


@pytest.mark.parametrize("pid", [p.id for p in profiles.PROFILES if p.can_load_styles])
def test_style_file_for_every_model(analysed, pid):
    prof = profiles.get(pid)
    project = {"projectName": "My.Song: Test!", "audioFile": "song.mp3"}
    data, report = styles.build(project, {"drums": analysed["drums"]}, _effective(analysed["tempo"]), pid)
    assert report["fileName"].count(".") == 1 and report["fileName"].endswith(prof.extension)
    if prof.max_bytes:
        assert len(data) <= prof.max_bytes

    mid, markers, channels, notes = _parse(data, prof.ppq)
    names = [m for m, _ in markers]
    assert names[:2] == ["SFF1", "SInt"] and markers[0][1] == 0 and markers[1][1] == 0
    bar = prof.ppq * 4
    assert markers[2][1] == bar                                 # sections start at measure 2
    assert set(names[2:]) == {s["name"] for s in report["sections"]}
    for (name, start), (_, nxt) in zip(markers[2:], markers[3:] + [(None, None)]):
        sec = next(s for s in report["sections"] if s["name"] == name)
        if nxt is not None:
            assert nxt - start == sec["bars"] * bar
        if name.startswith("Fill In"):
            assert sec["bars"] == 1
    assert channels == {9} and notes > 50                      # drums on MIDI channel 10 only
    assert "Main A" in names


def test_size_ladder_shrinks_patterns(analysed, monkeypatch):
    prof = profiles.get("psr-i425")
    small = profiles.KeyboardProfile(**{**prof.__dict__, "id": "tiny", "max_bytes": 1000})
    monkeypatch.setitem(profiles.BY_ID, "tiny", small)
    data, report = styles.build({"projectName": "x", "audioFile": "x"}, {"drums": analysed["drums"]},
                                _effective(analysed["tempo"]), "tiny")
    assert len(data) <= 1000 and report["adjustments"]


def test_quantize_moves_hits_onto_grid(analysed):
    eff = _effective(analysed["tempo"])
    proj = {"projectName": "q", "audioFile": "q"}
    raw, _ = styles.build(proj, {"drums": analysed["drums"]}, eff, "psr-sx700", 0.0)
    hard, _ = styles.build(proj, {"drums": analysed["drums"]}, eff, "psr-sx700", 1.0)

    def off_grid(data):
        mid = mido.MidiFile(file=io.BytesIO(data))
        t, offs = 0, []
        for msg in mid.tracks[0]:
            t += msg.time
            if msg.type == "note_on" and msg.velocity:
                q = 1920 / 4
                offs.append(abs(t - round(t / q) * q))
        return np.mean(offs)

    assert off_grid(hard) < 1.0
    if analysed["swing"]:
        assert off_grid(raw) > 20


def test_cannot_load_profile_is_refused(analysed):
    with pytest.raises(styles.StyleError):
        styles.build({"projectName": "x", "audioFile": "x"}, {"drums": analysed["drums"]},
                     _effective(analysed["tempo"]), "psr-e283")


def test_song_midi(analysed):
    data, name = styles.song_midi({"projectName": "Song.v2", "audioFile": "x"}, {"drums": analysed["drums"]},
                                  _effective(analysed["tempo"]))
    mid = mido.MidiFile(file=io.BytesIO(data))
    assert name == "Songv2.mid" and mid.type == 1 and len(mid.tracks) == 2
    hits = [m for m in mid.tracks[1] if m.type == "note_on" and m.velocity]
    assert len(hits) == len(analysed["drums"]["events"])
    assert {m.channel for m in hits} == {9}


def test_safe_filename():
    assert sff.safe_filename("Ãbc.Def/ghi  jk", 12) == "bcDefghi jk"
    assert sff.safe_filename("...", 12) == "Style"


@pytest.fixture(scope="module")
def four_grooves():
    plan = (("verse", 8), ("chorus", 8), ("bridge", 8), ("halftime", 8), ("chorus", 4))
    y, truth = drum_song(bpm=104.0, plan=plan)
    t = analyze_tempo(y, SR)
    return {"tempo": t, "drums": transcribe_drums(y, SR)}


def _kick_snare(sec):
    return (tuple(_slots(sec, "kick")), tuple(_slots(sec, "snare")))


def test_two_variation_keyboard_gets_two_styles(four_grooves):
    eff = _effective(four_grooves["tempo"])
    proj = {"projectName": "Four Grooves Song", "audioFile": "x"}
    a = {"drums": four_grooves["drums"]}
    d1, r1 = styles.build(proj, a, eff, "psr-e473", part=1)
    d2, r2 = styles.build(proj, a, eff, "psr-e473", part=2)
    assert r1["distinctGrooves"] == 4
    assert [f["fileName"] for f in r1["styleFiles"]] == ["Four Groov 1.STY", "Four Groov 2.STY"]
    for r in (r1, r2):
        names = [s["name"] for s in r["sections"]]
        assert "Main C" not in names and "Main D" not in names and {"Main A", "Main B"} <= set(names)
    origins = {s["origin"] for r in (r1, r2) for s in r["sections"] if s["name"].startswith("Main")}
    assert origins == {"A", "B", "C", "D"}
    grooves = {_kick_snare(s) for r in (r1, r2) for s in r["sections"] if s["name"].startswith("Main")}
    assert len(grooves) == 4                                   # all four song grooves, none duplicated
    assert len(d1) <= 50 * 1024 and len(d2) <= 50 * 1024

    zipped, names = styles.build_all(proj, a, eff, "psr-e473")
    import zipfile
    assert sorted(zipfile.ZipFile(io.BytesIO(zipped)).namelist()) == sorted(names) == ["Four Groov 1.STY", "Four Groov 2.STY"]


def test_four_variation_keyboard_gets_one_style(four_grooves):
    eff = _effective(four_grooves["tempo"])
    data, r = styles.build({"projectName": "Four", "audioFile": "x"}, {"drums": four_grooves["drums"]}, eff, "psr-sx700")
    assert len(r["styleFiles"]) == 1
    mains = [s for s in r["sections"] if s["name"].startswith("Main")]
    assert len(mains) == 4 and all(s["kind"] == "extracted" for s in mains)
    with pytest.raises(styles.StyleError):
        styles.build({"projectName": "Four", "audioFile": "x"}, {"drums": four_grooves["drums"]}, eff, "psr-sx700", part=2)


def test_custom_variation_choice(four_grooves):
    eff = _effective(four_grooves["tempo"])
    a = {"drums": four_grooves["drums"]}
    auto = styles.build({"projectName": "S", "audioFile": "x"}, a, eff, "psr-e473")[1]
    groove = {g["groove"]: g for g in auto["grooves"]}
    assert set(groove) == {"A", "B", "C", "D"} and auto["mapping"] == [["A", "B"], ["C", "D"]]

    # One style only, busiest groove on button A, lightest on B
    proj = {"projectName": "S", "audioFile": "x", "variationMap": {"two": [["D", "A"]]}}
    _, r = styles.build(proj, a, eff, "psr-e473")
    assert r["mappingCustom"] and len(r["styleFiles"]) == 1 and r["styleFiles"][0]["fileName"] == "S.STY"
    mains = {s["name"]: s for s in r["sections"] if s["name"].startswith("Main")}
    assert mains["Main A"]["origin"] == "D" and mains["Main B"]["origin"] == "A"
    assert _kick_snare(mains["Main A"]) == tuple(map(tuple, (_slots_of(groove["D"], "kick"), _slots_of(groove["D"], "snare"))))
    fills = {s["name"]: s["origin"] for s in r["sections"] if s["name"].startswith("Fill")}
    assert fills == {"Fill In AA": "D", "Fill In BB": "A", "Fill In AB": "D", "Fill In BA": "A"}

    # SX: reorder the four variations
    proj = {"projectName": "S", "audioFile": "x", "variationMap": {"four": [["B", "B", "D", "C"]]}}
    _, r = styles.build(proj, a, eff, "psr-sx700")
    origins = [s["origin"] for s in r["sections"] if s["name"].startswith("Main")]
    assert origins == ["B", "B", "D", "C"]
    assert any(s["name"] == "Fill In BA" for s in r["sections"])   # Break kept


def _slots_of(groove, inst):
    return sorted({e["slot"] for e in groove["events"] if e["inst"] == inst and e["bar"] == 0})


def test_stale_variation_choice_falls_back(analysed):
    eff = _effective(analysed["tempo"])
    proj = {"projectName": "S", "audioFile": "x", "variationMap": {"two": [["A", "D"]]}}  # song has 2 grooves
    _, r = styles.build(proj, {"drums": analysed["drums"]}, eff, "psr-e473")
    assert not r["mappingCustom"] and r["mapping"] == [["A", "B"]] and r["adjustments"]


@pytest.mark.parametrize("kind,bad", [("two", [["A"]]), ("two", [["A", "B"]] * 3), ("four", [["A", "B", "C", "X"]]),
                                      ("four", [["A", "B", "C", "D"]] * 2), ("two", "AB")])
def test_mapping_validation(kind, bad):
    with pytest.raises(styles.StyleError):
        styles.validate_mapping(kind, bad)
