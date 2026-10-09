"""Keyboard profiles (spec §26: no universal .STY claim; one profile per model family).

Every profile lists what is known about the model and how sure we are (`evidence`). The style
writer adapts sections, timing resolution and file size to the profile. All Yamaha profiles
export SFF1: it is the only format the E- and i-series load, and SFF2 instruments (PSR-SX,
Genos, PSR-S) load SFF1 and convert it themselves, so one well-defined format serves all.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Section layouts. Fill "context" = the Main the fill is extracted from / leads out of.
LAYOUT_ARRANGER = {   # PSR-SX, Genos, PSR-S: 4 variations, break, 2 intros/endings used
    "mains": ["Main A", "Main B", "Main C", "Main D"],
    "fills": {"Fill In AA": "Main A", "Fill In BB": "Main B", "Fill In CC": "Main C", "Fill In DD": "Main D"},
    "break": "Fill In BA",
    "intros": {"Intro A": 2, "Intro B": 4},
    "endings": {"Ending A": 2, "Ending B": 4},
    "maxMainBars": 4,
}
LAYOUT_PORTABLE = {   # PSR-E / EW / i: Main A/B, fills between them, one intro and ending
    "mains": ["Main A", "Main B"],
    "fills": {"Fill In AA": "Main A", "Fill In BB": "Main B", "Fill In AB": "Main A", "Fill In BA": "Main B"},
    "break": None,
    "intros": {"Intro A": 2},
    "endings": {"Ending A": 2},
    "maxMainBars": 4,
}


@dataclass
class KeyboardProfile:
    id: str
    name: str
    series: str
    can_load_styles: bool
    format: str | None = "SFF1"
    ppq: int = 1920
    max_bytes: int | None = None
    extension: str = ".sty"
    name_max: int = 20
    load_method: str = ""
    user_slots: str = ""
    layout: dict = field(default_factory=dict)
    evidence: str = ""
    verified: bool = False

    def public(self) -> dict:
        d = asdict(self)
        d["sections"] = (self.layout.get("mains", []) + list(self.layout.get("fills", {}))
                         + ([self.layout["break"]] if self.layout.get("break") else [])
                         + list(self.layout.get("intros", {})) + list(self.layout.get("endings", {})))
        d.pop("layout")
        return d


def _arranger(id, name, series, evidence, verified=True):
    return KeyboardProfile(id, name, series, True, "SFF1", 1920, None, ".sty", 20,
                           "USB flash drive → Style → User / USB tab", "User / USB memory", LAYOUT_ARRANGER,
                           evidence, verified)


def _portable(id, name, series, *, usb: bool, max_kb: int, slots: str, evidence: str, verified=False):
    method = ("USB flash drive (formatted on the keyboard) → Style → Load" if usb else
              "Computer: Yamaha Musicsoft Downloader / storage mode, then register the style")
    return KeyboardProfile(id, name, series, True, "SFF1", 480, max_kb * 1024, ".STY", 12, method, slots,
                           LAYOUT_PORTABLE, evidence, verified)


SFF2_NOTE = "SFF2 instrument; loads SFF1 styles and converts them (Wierzba/Bedesem style-file reference)."
E_NOTE = "SFF1 only, .STY extension, one period in the file name; ~50 KB limit."

PROFILES: list[KeyboardProfile] = [
    # --- Arranger workstations -------------------------------------------------------------
    _arranger("genos2", "Genos2", "Genos", SFF2_NOTE),
    _arranger("genos", "Genos", "Genos", SFF2_NOTE),
    _arranger("psr-sx920", "PSR-SX920", "PSR-SX", SFF2_NOTE),
    _arranger("psr-sx720", "PSR-SX720", "PSR-SX", SFF2_NOTE),
    _arranger("psr-sx900", "PSR-SX900", "PSR-SX", SFF2_NOTE),
    _arranger("psr-sx700", "PSR-SX700", "PSR-SX", SFF2_NOTE),
    _arranger("psr-sx600", "PSR-SX600", "PSR-SX", SFF2_NOTE),
    _arranger("psr-s975", "PSR-S975", "PSR-S", SFF2_NOTE),
    _arranger("psr-s970", "PSR-S970", "PSR-S", SFF2_NOTE),
    _arranger("psr-s770", "PSR-S770", "PSR-S", SFF2_NOTE),
    _arranger("psr-s670", "PSR-S670", "PSR-S", SFF2_NOTE),
    # --- Portable E / EW (USB flash) ------------------------------------------------------------
    _portable("psr-e473", "PSR-E473", "PSR-E", usb=True, max_kb=50, slots="10 (Styles 291–300)",
              evidence="Owner's manual: .sty import into Styles 291–300, max 50 KB, SFF1.", verified=True),
    _portable("psr-ew425", "PSR-EW425", "PSR-E", usb=True, max_kb=50, slots="10",
              evidence="76-key sibling of PSR-E473; same limits assumed — check the owner's manual."),
    _portable("psr-e463", "PSR-E463", "PSR-E", usb=True, max_kb=50, slots="10",
              evidence=E_NOTE + " (community reports; manual states no size)."),
    _portable("psr-ew410", "PSR-EW410", "PSR-E", usb=True, max_kb=50, slots="10",
              evidence="76-key sibling of PSR-E463; " + E_NOTE),
    # --- Portable E (computer transfer) -----------------------------------------------------
    _portable("psr-e383", "PSR-E383", "PSR-E", usb=False, max_kb=50, slots="10 user styles",
              evidence="USB-to-host only; 10 user styles (SFF). Size limit from community reports."),
    _portable("psr-e373", "PSR-E373", "PSR-E", usb=False, max_kb=50, slots="Styles 206–215",
              evidence="Community reports: SFF1 .STY ≤ 50 KB via storage mode / Musicsoft Downloader."),
    # --- India (i) series -----------------------------------------------------------------------
    _portable("psr-i500", "PSR-I500", "PSR-I", usb=True, max_kb=50, slots="user styles",
              evidence="E463-family; community reports SFF1 .STY. Size limit not in manual — 50 KB budget used."),
    _portable("psr-i400", "PSR-I400", "PSR-I", usb=False, max_kb=50, slots="user styles",
              evidence="E373-family; transfer from a computer. Not confirmed — check the owner's manual."),
    _portable("psr-i425", "PSR-I425", "PSR-I", usb=False, max_kb=30, slots="1 (Style 163)",
              evidence="Community reports: single user style, ~30 KB limit, Musicsoft Downloader; "
                       "sections Intro/Main A–B/Fill AB, BA/Ending A."),
    # --- No user-style loading -------------------------------------------------------------------
    KeyboardProfile("psr-e283", "PSR-E283 / E273 / E263", "PSR-E", False, None,
                    evidence="No style-file loading on these models — MIDI file download only (.mid)."),
]

BY_ID = {p.id: p for p in PROFILES}


def get(profile_id: str) -> KeyboardProfile | None:
    return BY_ID.get(profile_id)
