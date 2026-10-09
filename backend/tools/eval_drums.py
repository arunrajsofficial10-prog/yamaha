"""Precision / recall of drum transcription against synthetic ground truth."""
import sys
import numpy as np
sys.path.insert(0, ".")
from tests.synth import drum_song, SR
from app.audio.drums import transcribe_drums

TOL = 0.04

def score(truth, est, insts):
    res = {}
    for inst in insts:
        T = sorted(t for t, i, _ in truth if i == inst)
        E = sorted(e["time"] for e in est if e["inst"] == inst)
        used, tp = set(), 0
        for e in E:
            j = min(range(len(T)), key=lambda k: abs(T[k] - e), default=None)
            if j is not None and j not in used and abs(T[j] - e) <= TOL:
                used.add(j); tp += 1
        p = tp / len(E) if E else (1.0 if not T else 0.0)
        r = tp / len(T) if T else 1.0
        res[inst] = (round(p, 2), round(r, 2), len(T), len(E))
    return res

if __name__ == "__main__":
    for kw in ({}, {"swing": 0.33}, {"bpm": 128.0}, {"with_harmony": False}):
        y, truth = drum_song(**kw)
        r = transcribe_drums(y, SR)
        print(kw, score(truth["events"], r["events"], ["kick", "snare", "closed_hat", "open_hat", "crash"]))
