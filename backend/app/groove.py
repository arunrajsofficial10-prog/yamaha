"""Groove extraction: transcribed drum hits → the song's own rhythm patterns per style section.

"Originality" means the style plays the song's real hits:
  * every hit is positioned on the (user-corrected) beat grid, keeping its micro-timing offset
    from the nearest grid slot (swing, push, lay-back) and its velocity;
  * repeated bars are grouped; a pattern is the *consensus* of all its repetitions (a hit is kept
    when it occurs in at least half of them, with median velocity and median timing offset),
    which removes transcription noise without flattening the feel;
  * fills / intro / ending / break are taken from the song's actual bars where they exist.
Anything not found in the song is marked `kind: "fallback"` or `"generated"` so the UI can say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio.drums import GM, INSTRUMENTS

CLUSTER_DIST = 0.30         # cosine distance under which two bars count as the same pattern
CONSENSUS = 0.5             # fraction of repetitions a hit must appear in
DRUM_BAR_MIN_HITS = 2
DRUM_DURATION_BEATS = 0.125


@dataclass
class Bar:
    index: int                                    # 1-based bar number on the extended grid
    start: float = 0.0                            # song time of the bar's downbeat (s)
    hits: dict = field(default_factory=dict)      # (inst, slot) -> (velocity, offset_beats)

    def vector(self, slots: int) -> np.ndarray:
        v = np.zeros(len(INSTRUMENTS) * slots)
        for (inst, slot), (vel, _) in self.hits.items():
            v[INSTRUMENTS.index(inst) * slots + slot] = vel / 127.0
        return v

    @property
    def density(self) -> float:
        return sum(v for v, _ in self.hits.values()) / 127.0


def _smooth_beats(times: np.ndarray, radius: int = 4) -> np.ndarray:
    """Remove beat-tracker jitter with a local linear fit, keeping genuine tempo drift."""
    n = len(times)
    out = np.empty(n)
    idx = np.arange(n)
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        a, b = np.polyfit(idx[lo:hi], times[lo:hi], 1)
        out[i] = a * i + b
    return out


def _beat_position(t: float, beats: np.ndarray) -> float:
    """Continuous beat index of time t (fractional part = position inside the beat)."""
    if t <= beats[0]:
        return (t - beats[0]) / (beats[1] - beats[0])
    if t >= beats[-1]:
        return len(beats) - 1 + (t - beats[-1]) / (beats[-1] - beats[-2])
    i = int(np.searchsorted(beats, t, side="right") - 1)
    return i + (t - beats[i]) / (beats[i + 1] - beats[i])


def grid_alignment(positions: np.ndarray, n: int, tol: float = 0.15) -> float:
    """Share of hits within ±tol (in grid steps) of an n-per-beat grid, after removing the
    constant latency between tracked beats and hits. Random placement scores ≈ 2·tol = 0.3."""
    if positions.size == 0:
        return 0.0
    signed = ((positions * n) % 1 + 0.5) % 1 - 0.5
    signed = signed - np.median(signed)
    dev = np.abs((signed + 0.5) % 1 - 0.5)
    return float(np.mean(dev < tol))


def _choose_subdivision(positions: np.ndarray) -> int:
    """Straight 16ths (4 per beat) or shuffle / triplet feel (3 per beat)."""
    if positions.size < 8:
        return 4
    return 3 if grid_alignment(positions, 3) > grid_alignment(positions, 4) + 0.1 else 4


def _extend_grid(beats: np.ndarray, t_min: float, t_max: float, phase: int, bpb: int) -> tuple[np.ndarray, int]:
    """Extrapolate the beat grid (at the edge tempo) to cover all events. The tracker drops weak
    beats at the start and end, which would otherwise lose the intro and ending hits."""
    head = beats[1] - beats[0]
    pre = []
    t = beats[0] - head
    while t > t_min - head:
        pre.append(t)
        t -= head
    tail = beats[-1] - beats[-2]
    post = []
    t = beats[-1] + tail
    while t < t_max + tail:
        post.append(t)
        t += tail
    k = len(pre)
    return np.concatenate([pre[::-1], beats, post]), (phase + k) % bpb


def fmt_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def build_bars(events: list[dict], beat_times: list[float], beats_per_bar: int, phase: int) -> tuple[list[Bar], int, dict]:
    beats = _smooth_beats(np.asarray(beat_times, dtype=float))
    if events:
        beats, phase = _extend_grid(beats, min(e["time"] for e in events), max(e["time"] for e in events),
                                    phase, beats_per_bar)
    positions = [(_beat_position(e["time"], beats), e) for e in events]
    n = _choose_subdivision(np.array([p for p, e in positions if e["inst"] in ("kick", "snare", "closed_hat")]))
    slots = beats_per_bar * n

    bars: dict[int, Bar] = {}
    offsets = {"offbeat": [], "onbeat": [], "snare": [], "all": []}
    for p, e in positions:
        rel = p - phase
        slot_global = int(round(rel * n))
        offset = rel - slot_global / n                       # beats; + = late (laid back)
        bar_idx = slot_global // slots + 1
        slot = slot_global % slots
        if bar_idx < 1:
            continue
        if bar_idx not in bars:
            beat_i = phase + (bar_idx - 1) * beats_per_bar
            bars[bar_idx] = Bar(bar_idx, float(beats[min(max(beat_i, 0), len(beats) - 1)]))
        bar = bars[bar_idx]
        key = (e["inst"], slot)
        if key not in bar.hits or e["velocity"] > bar.hits[key][0]:
            bar.hits[key] = (e["velocity"], offset)
        offsets["all"].append(offset)
        if n == 4 and slot % 4 == 2:
            offsets["offbeat"].append(offset)
        if slot % n == 0:
            offsets["onbeat"].append(offset)
        if e["inst"] == "snare":
            offsets["snare"].append(offset)

    # The tracker's beats lag the drum attacks by a roughly constant latency; that is grid
    # alignment, not feel. Re-centre all offsets on the median on-beat offset.
    latency = float(np.median(offsets["onbeat"])) if offsets["onbeat"] else 0.0
    for bar in bars.values():
        bar.hits = {k: (v, o - latency) for k, (v, o) in bar.hits.items()}
    for k in ("offbeat", "snare", "all"):
        offsets[k] = [o - latency for o in offsets[k]]

    ordered = []
    for i in range(1, max(bars, default=0) + 1):
        if i not in bars:
            beat_i = phase + (i - 1) * beats_per_bar
            bars[i] = Bar(i, float(beats[min(max(beat_i, 0), len(beats) - 1)]))
        ordered.append(bars[i])
    feel = {
        "subdivision": "triplet" if n == 3 else "straight",
        # swing as % of a 16th note that the off-beat 8ths are delayed (0 = straight)
        "swingPercent": round(float(np.median(offsets["offbeat"]) * 4 * 100), 1) if len(offsets["offbeat"]) > 4 else 0.0,
        "snareOffsetBeats": round(float(np.median(offsets["snare"])), 4) if offsets["snare"] else 0.0,
        "timingSpreadBeats": round(float(np.std(offsets["all"])), 4) if offsets["all"] else 0.0,
        "gridLatencyBeats": round(latency, 4),
    }
    return ordered, n, feel


def _cosine_dist(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 1.0
    return 1.0 - float(a @ b / (na * nb))


def _cluster(vectors: list[np.ndarray]) -> list[int]:
    """Greedy leader clustering; returns cluster id per item (ids ordered by first appearance)."""
    centroids, members, labels = [], [], []
    for v in vectors:
        dists = [_cosine_dist(v, c) for c in centroids]
        if dists and min(dists) < CLUSTER_DIST:
            k = int(np.argmin(dists))
            members[k].append(v)
            centroids[k] = np.mean(members[k], axis=0)
        else:
            k = len(centroids)
            centroids.append(v.copy())
            members.append([v])
        labels.append(k)
    return labels


def _consensus(blocks: list[list[Bar]], slots: int, n: int) -> tuple[list[dict], dict]:
    """Merge repetitions of a pattern into one; keep hits present in ≥ CONSENSUS of them."""
    L = len(blocks[0])
    tally: dict = {}
    for block in blocks:
        for j, bar in enumerate(block):
            for (inst, slot), (vel, off) in bar.hits.items():
                tally.setdefault((j, inst, slot), []).append((vel, off))
    events, inferred = [], 0
    need = max(1, int(np.ceil(CONSENSUS * len(blocks))))
    present = {}
    for (j, inst, slot), hits in tally.items():
        if len(hits) >= need:
            vel = int(np.median([h[0] for h in hits]))
            off = float(np.median([h[1] for h in hits]))
            present[(j, inst, slot)] = (vel, off)

    # Hi-hats played together with a snare are masked in a mix. When a bar's closed-hat pattern is
    # otherwise regular on the 8th grid and the only gaps are snare positions, restore them.
    if n == 4:
        for j in range(L):
            eighths = [s for s in range(0, slots, 2)]
            hats = [s for s in eighths if (j, "closed_hat", s) in present]
            gaps = [s for s in eighths if s not in hats]
            if hats and len(hats) >= 0.6 * len(eighths) and gaps and all((j, "snare", s) in present for s in gaps):
                hat_vel = int(np.median([present[(j, "closed_hat", s)][0] for s in hats]))
                for s in gaps:
                    present[(j, "closed_hat", s)] = (hat_vel, present[(j, "snare", s)][1])
                    inferred += 1

    for (j, inst, slot), (vel, off) in present.items():
        events.append({"inst": inst, "note": GM[inst], "velocity": vel,
                       "pos": round(j * slots / n + slot / n + off, 5), "slot": slot, "bar": j})
    events.sort(key=lambda e: (e["pos"], e["note"]))
    return events, {"repetitions": len(blocks), "inferredMaskedHats": inferred}


def _bar_events(bars: list[Bar], n: int, slots: int) -> list[dict]:
    """Events of literal song bars (no consensus) — used for fills, intro, ending."""
    out = []
    for j, bar in enumerate(bars):
        for (inst, slot), (vel, off) in bar.hits.items():
            out.append({"inst": inst, "note": GM[inst], "velocity": vel,
                        "pos": round(j * slots / n + slot / n + off, 5), "slot": slot, "bar": j})
    out.sort(key=lambda e: (e["pos"], e["note"]))
    return out


def _intensity(events: list[dict], bars: int) -> float:
    return sum(e["velocity"] for e in events) / max(bars, 1)


def _choose_block_length(labels: list[int], valid: list[bool], max_len: int) -> int:
    """Smallest L in (1, 2, 4) whose bar-pattern sequence repeats as well as the best one."""
    agree = {}
    for L in (1, 2, 4):
        if L > max_len:
            continue
        pairs = [(labels[i], labels[i + L]) for i in range(len(labels) - L) if valid[i] and valid[i + L]]
        agree[L] = np.mean([a == b for a, b in pairs]) if pairs else 0.0
    best = max(agree.values()) if agree else 0.0
    for L in sorted(agree):
        if agree[L] >= best - 0.05:
            return L
    return 1


def extract_style(events: list[dict], beat_times: list[float], beats_per_bar: int, phase: int,
                  layout: dict) -> dict:
    """Build drum sections for a keyboard layout.

    layout = {"mains": ["Main A", "Main B", ...], "fills": {"Fill In AA": "Main A", ...},
              "intros": {"Intro A": 2, ...}, "endings": {"Ending A": 2, ...},
              "break": "Fill In BA" | None, "maxMainBars": 4}
    """
    bars, n, feel = build_bars(events, beat_times, beats_per_bar, phase)
    slots = beats_per_bar * n
    drum_bars = [b for b in bars if len(b.hits) >= DRUM_BAR_MIN_HITS]
    if len(drum_bars) < 4:
        return {"status": "no_drums", "sections": {}, "feel": feel, "subdivision": n}

    labels = _cluster([b.vector(slots) for b in bars])
    valid = [len(b.hits) >= DRUM_BAR_MIN_HITS for b in bars]
    counts = {k: sum(1 for l, v in zip(labels, valid) if l == k and v) for k in set(labels)}
    common = {k for k, c in counts.items() if c >= max(2, 0.08 * len(drum_bars))}

    L = _choose_block_length([l if l in common else -1 for l in labels],
                             [v and l in common for l, v in zip(labels, valid)], layout.get("maxMainBars", 4))

    # Main patterns: aligned L-bar blocks made only of common bar patterns, grouped by signature.
    # A groove plays for several blocks in a row; fills recur but never back-to-back, so a
    # signature only qualifies as a Main if it has at least one run of two consecutive blocks.
    groups: dict[tuple, list[list[Bar]]] = {}
    longest_run: dict[tuple, int] = {}
    prev_sig, run = None, 0
    for start in range(0, len(bars) - L + 1, L):
        sig = tuple(labels[start: start + L])
        if all(valid[start + j] and labels[start + j] in common for j in range(L)):
            groups.setdefault(sig, []).append(bars[start: start + L])
            run = run + 1 if sig == prev_sig else 1
            longest_run[sig] = max(longest_run.get(sig, 0), run)
            prev_sig = sig
        else:
            prev_sig, run = None, 0
    ranked = sorted(((s, b) for s, b in groups.items() if longest_run.get(s, 0) >= 2),
                    key=lambda kv: len(kv[1]), reverse=True)
    if not ranked:  # nothing repeats back-to-back: take the most frequent block anyway
        ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    if not ranked:  # irregular playing: fall back to the most common single bar pattern
        L = 1
        top = max(counts, key=lambda k: counts[k])
        ranked = [((top,), [[b] for b, l, v in zip(bars, labels, valid) if l == top and v])]

    mains_wanted = layout["mains"]
    patterns = []
    for sig, blocks in ranked[: len(mains_wanted)]:
        ev, info = _consensus(blocks, slots, n)
        patterns.append({"sig": sig, "events": ev, "info": info, "blocks": blocks,
                         "intensity": _intensity(ev, L)})
    patterns.sort(key=lambda p: p["intensity"])  # Main A = lightest … Main D = busiest

    sections = {}
    for i, name in enumerate(mains_wanted):
        if i < len(patterns):
            p = patterns[i]
            first = p["blocks"][0][0].start
            sections[name] = {"bars": L, "events": p["events"], "kind": "extracted",
                              "source": f"consensus of {p['info']['repetitions']} repeats of a {L}-bar groove (first at {fmt_time(first)})",
                              **p["info"]}
        else:  # fewer distinct grooves in the song than the keyboard has variations
            src = sections[mains_wanted[i - 1]]
            sections[name] = {**src, "kind": "fallback",
                              "source": f"song has only {len(patterns)} distinct groove(s) — copy of {mains_wanted[i - 1]}"}

    # Fills: song bars that break their surrounding pattern, preferring phrase ends.
    sig_of_main = {name: patterns[i]["sig"] for i, name in enumerate(mains_wanted) if i < len(patterns)}
    main_labels = {l for p in patterns for l in p["sig"]}
    fill_candidates = []
    for i, b in enumerate(bars):
        if not valid[i]:
            continue
        rare = labels[i] not in main_labels
        phrase_end = (i + 1) % 4 == 0
        nxt_change = i + 1 < len(bars) and labels[i + 1] != labels[i]
        if rare and (phrase_end or nxt_change):
            # which main groove was playing in the bar before this fill?
            context = next((m for m, sig in sig_of_main.items() if i > 0 and labels[i - 1] in sig), None)
            fill_candidates.append((b, context, b.density))

    for fill_name, main_name in layout["fills"].items():
        cands = [c for c in fill_candidates if c[1] == main_name] or [c for c in fill_candidates if c[1] is None] or fill_candidates
        if cands:
            b = max(cands, key=lambda c: c[2])[0]
            sections[fill_name] = {"bars": 1, "events": _bar_events([b], n, slots), "kind": "extracted",
                                   "source": f"song fill at {fmt_time(b.start)}"}
        else:
            base = sections[main_name]
            last_bar = [dict(e, pos=round(e["pos"] - (base["bars"] - 1) * slots / n, 5), bar=0)
                        for e in base["events"] if e["bar"] == base["bars"] - 1]
            sections[fill_name] = {"bars": 1, "events": last_bar, "kind": "fallback",
                                   "source": f"no distinct fill found — last bar of {main_name}"}

    # Break: sparsest real bar that still starts with a hit; otherwise a generated stop-hit.
    if layout.get("break"):
        main_density = np.median([b.density for b in drum_bars])
        sparse = [b for b in bars[1:-1] if 0 < len(b.hits) and b.density < 0.35 * main_density
                  and any(slot < n for (_, slot) in b.hits)]
        if sparse:
            b = sparse[0]
            sections[layout["break"]] = {"bars": 1, "events": _bar_events([b], n, slots), "kind": "extracted",
                                         "source": f"song break at {fmt_time(b.start)}"}
        else:
            ev = [{"inst": "kick", "note": GM["kick"], "velocity": 110, "pos": 0.0, "slot": 0, "bar": 0},
                  {"inst": "crash", "note": GM["crash"], "velocity": 110, "pos": 0.0, "slot": 0, "bar": 0}]
            sections[layout["break"]] = {"bars": 1, "events": ev, "kind": "generated",
                                         "source": "no break in the song — single stop hit"}

    first_i = next(i for i, v in enumerate(valid) if v)
    last_i = max(i for i, v in enumerate(valid) if v)
    for name, nb in layout.get("intros", {}).items():
        chunk = bars[first_i: first_i + nb]
        sections[name] = {"bars": len(chunk), "events": _bar_events(chunk, n, slots), "kind": "extracted",
                          "source": f"song bars from {fmt_time(chunk[0].start)} (where the drums enter)"}
    for name, nb in layout.get("endings", {}).items():
        chunk = bars[max(first_i, last_i - nb + 1): last_i + 1]
        sections[name] = {"bars": len(chunk), "events": _bar_events(chunk, n, slots), "kind": "extracted",
                          "source": f"last drum bars, from {fmt_time(chunk[0].start)}"}

    return {"status": "ok", "sections": sections, "feel": feel, "subdivision": n, "patternBars": L,
            "distinctGrooves": len(patterns), "beatsPerBar": beats_per_bar}


def rhythm_alignment(events: list[dict], beat_times: list[float]) -> dict:
    """How well kick/snare hits sit on a straight or triplet grid (drum-kit plausibility check)."""
    if len(beat_times) < 4:
        return {"straight": 0.0, "triplet": 0.0, "best": 0.0}
    beats = _smooth_beats(np.asarray(beat_times, dtype=float))
    pos = np.array([_beat_position(e["time"], beats) for e in events if e["inst"] in ("kick", "snare")])
    a4, a3 = grid_alignment(pos, 4), grid_alignment(pos, 3)
    return {"straight": round(a4, 3), "triplet": round(a3, 3), "best": round(max(a3, a4), 3)}


def song_beat_positions(events: list[dict], beat_times: list[float], beats_per_bar: int, phase: int) -> list[float]:
    """Beat position of every event, counted from the first bar line (≥ 0), on the same
    smoothed/extended grid as the style patterns. Used for the full-song MIDI file."""
    if not events:
        return []
    beats = _smooth_beats(np.asarray(beat_times, dtype=float))
    beats, phase = _extend_grid(beats, min(e["time"] for e in events), max(e["time"] for e in events),
                                phase, beats_per_bar)
    pos = [_beat_position(e["time"], beats) - phase for e in events]
    onbeat = [p - round(p) for p in pos if abs(p - round(p)) < 0.2]
    latency = float(np.median(onbeat)) if onbeat else 0.0
    pos = [p - latency for p in pos]
    shift = -min(0.0, np.floor(min(pos) / beats_per_bar) * beats_per_bar)
    return [round(p + shift, 5) for p in pos]
