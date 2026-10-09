"""Pitch / MIDI helpers shared by every phase (spec §7, §9, §10, §23)."""

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_ALIASES = {"DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10, "CB": 11, "FB": 4, "E#": 5, "B#": 0}

# Conventional spelling for key names in the UI (Eb major rather than D# major).
KEY_DISPLAY = {
    "major": ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
    "minor": ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"],
}

# MIDI key-signature sharps(+)/flats(-) for each major tonic pitch class.
_MAJOR_ACCIDENTALS = [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5]


def pitch_class(name: str) -> int:
    n = name.strip().upper()
    if n in _FLAT_ALIASES:
        return _FLAT_ALIASES[n]
    if n in PITCH_CLASSES:
        return PITCH_CLASSES.index(n)
    raise ValueError(f"Unknown pitch class: {name!r}")


def midi_note(pc: int, octave: int) -> int:
    """MIDI = 12 × (octave + 1) + pitch_class  (C4 = 60)."""
    return 12 * (octave + 1) + pc


def note_name(midi: int) -> str:
    return f"{PITCH_CLASSES[midi % 12]}{midi // 12 - 1}"


def key_name(tonic_pc: int, mode: str) -> str:
    return f"{KEY_DISPLAY[mode][tonic_pc % 12]} {mode.capitalize()}"


def key_signature_accidentals(tonic_pc: int, mode: str) -> int:
    """Sharps (+) or flats (-) for a MIDI key-signature meta event."""
    major_pc = tonic_pc if mode == "major" else (tonic_pc + 3) % 12
    return _MAJOR_ACCIDENTALS[major_pc]


def to_chord_relative(notes: list[int], chord_root_pc: int) -> list[int]:
    """Absolute MIDI notes → intervals relative to the chord root (spec §9).

    Octave information is kept: C4-E4-G4 over root C gives 0-4-7 when the lowest note is the root.
    """
    if not notes:
        return []
    base = min(notes)
    base -= (base - chord_root_pc) % 12
    return [n - base for n in notes]


def from_chord_relative(intervals: list[int], chord_root_pc: int, octave: int) -> list[int]:
    """absolute_note = chord_root + relative_interval (spec §9, §21)."""
    root = midi_note(chord_root_pc, octave)
    return [root + i for i in intervals]


def clamp_to_range(note: int, lo: int, hi: int) -> int:
    """Octave-fold a note into [lo, hi] (spec §23)."""
    if hi - lo < 11:
        raise ValueError("Range must span at least one octave")
    while note > hi:
        note -= 12
    while note < lo:
        note += 12
    return note


# Recommended accompaniment ranges (spec §23)
TRACK_RANGES = {
    "bass": (28, 55),
    "chord": (48, 84),
    "pad": (55, 96),
    "phrase": (48, 96),
}
