"""Synthesised test songs with known ground truth (tempo, meter, key, downbeats)."""
from __future__ import annotations

import numpy as np

SR = 22050
MAJOR = [0, 4, 7]
MINOR = [0, 3, 7]


def _env(n, decay):
    return np.exp(-np.arange(n) / (decay * SR))


def _kick(n=int(0.25 * SR)):
    t = np.arange(n) / SR
    freq = 50 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.09)


def _snare(rng, n=int(0.18 * SR)):
    return (rng.standard_normal(n) * 0.6 + np.sin(2 * np.pi * 190 * np.arange(n) / SR) * 0.4) * _env(n, 0.05)


def _hat(rng, n=int(0.05 * SR)):
    x = rng.standard_normal(n)
    return np.diff(x, prepend=0) * _env(n, 0.012) * 0.35


def _tone(midi, n, amp=0.12):
    f = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.arange(n) / SR
    wave = sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3, 4))
    attack = np.minimum(1.0, np.arange(n) / (0.01 * SR))
    return wave * attack * _env(n, 1.2) * amp


def _add(buf, sig, start):
    s = int(start * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[: e - s]


def song(bpm=120.0, beats_per_bar=4, progression=((0, MAJOR), (9, MINOR), (5, MAJOR), (7, MAJOR)),
         bars=24, key_offset=0, lead_in=0.0, seed=0) -> tuple[np.ndarray, dict]:
    """Drums + bass + chords. `progression` = [(root_pc, intervals), ...], one chord per bar."""
    rng = np.random.default_rng(seed)
    spb = 60.0 / bpm
    total = lead_in + bars * beats_per_bar * spb + 1.0
    y = np.zeros(int(total * SR))
    downbeats = []

    for bar in range(bars):
        root, ivs = progression[bar % len(progression)]
        root = (root + key_offset) % 12
        bar_start = lead_in + bar * beats_per_bar * spb
        downbeats.append(bar_start)
        bar_len = int(beats_per_bar * spb * SR)
        for iv in ivs:
            _add(y, _tone(60 + root + iv, bar_len), bar_start)
        _add(y, _tone(36 + root, bar_len, amp=0.25), bar_start)

        for b in range(beats_per_bar):
            t = bar_start + b * spb
            if beats_per_bar == 4:
                if b in (0, 2):
                    _add(y, _kick() * (1.0 if b == 0 else 0.8), t)
                else:
                    _add(y, _snare(rng) * 0.7, t)
            else:  # waltz: strong kick on 1, light snare on 2 & 3
                _add(y, _kick() if b == 0 else _snare(rng) * 0.35, t)
            _add(y, _hat(rng), t)
            _add(y, _hat(rng) * 0.6, t + spb / 2)

    y = y / np.max(np.abs(y)) * 0.8
    truth = {"bpm": bpm, "beatsPerBar": beats_per_bar, "downbeats": downbeats, "tonic": key_offset % 12}
    return y.astype(np.float32), truth


# Diatonic progressions in C; shifted with `key_offset`.
POP_MAJOR = ((0, MAJOR), (9, MINOR), (5, MAJOR), (7, MAJOR))           # I vi IV V
MINOR_PROG = ((9, MINOR), (2, MINOR), (4, MAJOR), (9, MINOR))          # i iv V i in A minor


# --- drum-groove songs with exact ground-truth hits -------------------------------------------

def _open_hat(rng, n=int(0.35 * SR)):
    x = rng.standard_normal(n)
    return np.diff(x, prepend=0) * _env(n, 0.12) * 0.3


def _crash(rng, n=int(1.2 * SR)):
    x = rng.standard_normal(n)
    return (np.diff(x, prepend=0) * 0.7 + x * 0.15) * _env(n, 0.45) * 0.45


# Patterns: instrument -> {16th-slot: velocity 0..1}, one bar of 4/4 (16 slots).
GROOVE_VERSE = {
    "kick": {0: 1.0, 7: 0.7, 10: 0.9},
    "snare": {4: 0.9, 12: 1.0},
    "closed_hat": {s: (0.8 if s % 4 == 0 else 0.5) for s in range(0, 16, 2)},
}
GROOVE_CHORUS = {
    "kick": {0: 1.0, 3: 0.6, 8: 1.0, 10: 0.7},
    "snare": {4: 1.0, 12: 1.0, 15: 0.5},
    "open_hat": {s: 0.7 for s in range(2, 16, 4)},
    "closed_hat": {s: 0.6 for s in range(0, 16, 4)},
}
GROOVE_BRIDGE = {   # four-on-the-floor
    "kick": {0: 1.0, 4: 1.0, 8: 1.0, 12: 1.0},
    "snare": {4: 0.8, 12: 0.8},
    "open_hat": {s: 0.6 for s in (2, 6, 10, 14)},
    "closed_hat": {s: 0.5 for s in (1, 3, 5, 7, 9, 11, 13, 15)},
}
GROOVE_HALFTIME = {  # sparse half-time
    "kick": {0: 1.0, 6: 0.6},
    "snare": {8: 1.0},
    "closed_hat": {s: 0.5 for s in range(0, 16, 4)},
}
GROOVE_FILL = {
    "kick": {0: 1.0},
    "snare": {s: 0.5 + 0.03 * s for s in range(4, 16)},
}


def drum_song(bpm=100.0, plan=(("verse", 8), ("chorus", 8), ("verse", 4)), swing=0.0, fills_every=4,
              crash_on_section=True, seed=1, with_harmony=True):
    """Return (audio, truth) where truth = {"events": [(time, inst, vel)], "bpm", "sectionStarts"}.

    `swing` delays odd 8th-note positions (16th slots 2, 6, 10, 14) by `swing` × a 16th note,
    which is the micro-timing a groove extractor must preserve.
    """
    rng = np.random.default_rng(seed)
    gens = {"kick": lambda: _kick(), "snare": lambda: _snare(rng), "closed_hat": lambda: _hat(rng),
            "open_hat": lambda: _open_hat(rng), "crash": lambda: _crash(rng)}
    patterns = {"verse": GROOVE_VERSE, "chorus": GROOVE_CHORUS, "bridge": GROOVE_BRIDGE, "halftime": GROOVE_HALFTIME}
    spb = 60.0 / bpm
    sixteenth = spb / 4
    total_bars = sum(n for _, n in plan)
    y = np.zeros(int((total_bars * 4 * spb + 2.0) * SR))
    events, section_starts = [], []
    bar = 0
    prog = POP_MAJOR
    for name, n in plan:
        section_starts.append((bar, name))
        for b in range(n):
            t0 = bar * 4 * spb
            is_fill = fills_every and (b % fills_every == fills_every - 1) and b == n - 1
            pat = GROOVE_FILL if is_fill else patterns[name]
            for inst, slots in pat.items():
                for slot, vel in slots.items():
                    t = t0 + slot * sixteenth + (swing * sixteenth if slot % 4 == 2 else 0.0)
                    _add(y, gens[inst]() * vel, t)
                    events.append((t, inst, vel))
            if crash_on_section and b == 0:
                _add(y, gens["crash"](), t0)
                events.append((t0, "crash", 1.0))
            if with_harmony:
                root, ivs = prog[bar % len(prog)]
                bar_len = int(4 * spb * SR)
                for iv in ivs:
                    _add(y, _tone(60 + root + iv, bar_len, amp=0.06), t0)
                _add(y, _tone(36 + root, bar_len, amp=0.12), t0)
            bar += 1
    y = y / np.max(np.abs(y)) * 0.8
    events.sort()
    return y.astype(np.float32), {"events": events, "bpm": bpm, "sectionStarts": section_starts}
