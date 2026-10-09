"""SFF1 style writer and full-song MIDI writer.

Style layout (Wierzba/Bedesem, "Style Files – Introduction and Details" v2.1):
  * SMF type 0, one MTrk.
  * Measure 1, tick 0, in this order: time signature, tempo, copyright, marker "SFF1", track name
    (style name), marker "SInt", GM-On sysex, then per-channel setup (bank, program, volume,
    pan, reverb, chorus, drum-part sysex).
  * From measure 2: one marker per section ("Main A", "Fill In AA", …) followed by its notes.
    Fill ins are exactly one measure.
  * Channels 9–16 carry the accompaniment parts; with that assignment and CMaj7-based
    harmony no CASM block is needed ("simple" style). Drums: channel 10 (Rhythm).
"""
from __future__ import annotations

import re
import struct

DRUM_CH = 9          # MIDI channel 10 (zero-based 9) = Rhythm part
SUB_RHYTHM_CH = 8    # channel 9 = Sub rhythm
SECTION_ORDER = ["Intro A", "Intro B", "Intro C", "Main A", "Main B", "Main C", "Main D",
                 "Fill In AA", "Fill In BB", "Fill In CC", "Fill In DD", "Fill In AB", "Fill In BA",
                 "Ending A", "Ending B", "Ending C"]
GM_ON = bytes.fromhex("F07E7F0901F7")


def _vlq(n: int) -> bytes:
    n = max(0, int(n))
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _sysex(msg: bytes) -> bytes:
    assert msg[0] == 0xF0
    return b"\xF0" + _vlq(len(msg) - 1) + msg[1:]


def _ascii(s: str, limit: int) -> bytes:
    s = re.sub(r"[^\x20-\x7E]", "", s).strip()[:limit]
    return s.encode("ascii")


def safe_filename(name: str, limit: int) -> str:
    """Keyboard-safe stem: ASCII letters, digits, space, - and _ only, and no periods
    (PSR-E models treat everything after the first period as the extension)."""
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip()
    stem = re.sub(r"\s+", " ", stem)[:limit].strip()
    return stem or "Style"


def meter_info(time_signature: str, beats_per_bar: int) -> dict:
    num, den = (int(x) for x in time_signature.split("/"))
    quarters_per_bar = num * 4 / den
    return {"num": num, "den": den, "quarters_per_bar": quarters_per_bar,
            "quarters_per_beat": quarters_per_bar / beats_per_bar}


class _Track:
    """Collects (tick, priority, bytes, status) and serialises with running status."""

    def __init__(self):
        self.items = []

    def add(self, tick: int, data: bytes, prio: int = 5):
        self.items.append((int(tick), prio, len(self.items), data))

    def encode(self) -> bytes:
        out = bytearray()
        last_tick, running = 0, None
        for tick, _, _, data in sorted(self.items):
            out += _vlq(tick - last_tick)
            last_tick = tick
            status = data[0]
            if 0x80 <= status < 0xF0:
                if status == running:
                    out += data[1:]
                else:
                    out += data
                    running = status
            else:
                out += data
                running = None  # meta / sysex cancel running status
        out += _vlq(0) + _meta(0x2F, b"")
        return bytes(out)


def _header(fmt: int, ntracks: int, ppq: int) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, fmt, ntracks, ppq)


def _chunk(tid: bytes, data: bytes) -> bytes:
    return tid + struct.pack(">I", len(data)) + data


def _setup_drum_channel(tr: _Track, ch: int, volume: int):
    for cc, val in ((0, 127), (32, 0)):       # XG drum bank: MSB 127 / LSB 0
        tr.add(0, bytes([0xB0 | ch, cc, val]), 3)
    tr.add(0, bytes([0xC0 | ch, 0]), 3)       # Standard Kit
    for cc, val in ((7, volume), (10, 64), (91, 40), (93, 0)):
        tr.add(0, bytes([0xB0 | ch, cc, val]), 3)


def write_style(style: dict, *, name: str, bpm: float, time_signature: str, beats_per_bar: int,
                ppq: int, quantize: float = 0.0, copyright_text: str = "") -> bytes:
    m = meter_info(time_signature, beats_per_bar)
    ticks_per_beat = ppq * m["quarters_per_beat"]
    bar_ticks = int(round(ppq * m["quarters_per_bar"]))
    dur = max(1, int(ppq / 8))
    quarter_bpm = bpm * m["quarters_per_beat"]
    slots_per_beat = style.get("subdivision", 4)

    tr = _Track()
    dd = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[m["den"]]
    tr.add(0, _meta(0x58, bytes([m["num"], dd, 24, 8])), 0)
    tr.add(0, _meta(0x51, int(round(60_000_000 / quarter_bpm)).to_bytes(3, "big")), 0)
    if copyright_text:
        tr.add(0, _meta(0x02, _ascii(copyright_text, 120)), 0)
    tr.add(0, _meta(0x06, b"SFF1"), 1)
    tr.add(0, _meta(0x03, _ascii(name, 20)), 1)
    tr.add(0, _meta(0x06, b"SInt"), 2)
    tr.add(0, _sysex(GM_ON), 2)
    _setup_drum_channel(tr, DRUM_CH, 100)
    _setup_drum_channel(tr, SUB_RHYTHM_CH, 90)
    tr.add(0, _sysex(bytes.fromhex("F043104C08080703F7")), 4)   # part 9 → drums 1
    tr.add(0, _sysex(bytes.fromhex("F043104C08090702F7")), 4)   # part 10 → drums 2

    tick = bar_ticks  # sections start at measure 2
    layout = []
    for sec_name in SECTION_ORDER:
        sec = style["sections"].get(sec_name)
        if not sec or not sec["events"]:
            continue
        length = sec["bars"] * bar_ticks
        tr.add(tick, _meta(0x06, sec_name.encode("ascii")), 0)
        for e in sec["events"]:
            grid = e["bar"] * beats_per_bar + e["slot"] / slots_per_beat
            pos = e["pos"] - quantize * (e["pos"] - grid)
            on = int(round(pos * ticks_per_beat))
            on = min(max(on, 0), length - dur - 1)
            vel = int(min(127, max(1, e["velocity"])))
            tr.add(tick + on, bytes([0x90 | DRUM_CH, e["note"], vel]), 6)
            tr.add(tick + on + dur, bytes([0x90 | DRUM_CH, e["note"], 0]), 5)  # note off (vel 0)
        layout.append({"name": sec_name, "startTick": tick, "bars": sec["bars"]})
        tick += length

    data = _header(0, 1, ppq) + _chunk(b"MTrk", tr.encode())
    return data


def write_song_midi(events: list[dict], *, beat_positions: list[float], bpm: float, time_signature: str,
                    beats_per_bar: int, key_sig: tuple[int, int] | None, ppq: int, title: str) -> bytes:
    """Full-song MIDI (type 1): conductor track + drum track on channel 10 (spec §18)."""
    m = meter_info(time_signature, beats_per_bar)
    ticks_per_beat = ppq * m["quarters_per_beat"]
    cond = _Track()
    dd = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[m["den"]]
    cond.add(0, _meta(0x03, _ascii(title, 60)), 0)
    cond.add(0, _meta(0x58, bytes([m["num"], dd, 24, 8])), 0)
    cond.add(0, _meta(0x51, int(round(60_000_000 / (bpm * m["quarters_per_beat"]))).to_bytes(3, "big")), 0)
    if key_sig is not None:
        sf, mi = key_sig
        cond.add(0, _meta(0x59, struct.pack("bB", sf, mi)), 0)

    drums = _Track()
    drums.add(0, _meta(0x03, b"Drums"), 0)
    for cc, val in ((0, 127), (32, 0)):
        drums.add(0, bytes([0xB0 | DRUM_CH, cc, val]), 1)
    drums.add(0, bytes([0xC0 | DRUM_CH, 0]), 1)
    dur = max(1, int(ppq / 8))
    for e, pos in zip(events, beat_positions):
        on = int(round(pos * ticks_per_beat))
        if on < 0:
            continue
        drums.add(on, bytes([0x90 | DRUM_CH, e["note"], int(min(127, max(1, e["velocity"])))]), 6)
        drums.add(on + dur, bytes([0x90 | DRUM_CH, e["note"], 0]), 5)

    return (_header(1, 2, ppq) + _chunk(b"MTrk", cond.encode()) + _chunk(b"MTrk", drums.encode()))
