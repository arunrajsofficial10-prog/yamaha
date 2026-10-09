"""Style generation service: effective analysis + keyboard profile → style file(s) / MIDI."""
from __future__ import annotations

import copy
import io
import zipfile

from . import groove
from .errors import AppError
from .keyboards import profiles, sff


class StyleError(AppError):
    code = "style_error"


# Keyboards with only Main A/B (PSR-E, EW, I) get the song's grooves split over two styles:
# style 1 = grooves A + B, style 2 = grooves C + D, each played from the keyboard's own A/B buttons.
FOUR_GROOVES = {
    "mains": ["Main A", "Main B", "Main C", "Main D"],
    "fills": {"Fill In AA": "Main A", "Fill In BB": "Main B", "Fill In CC": "Main C", "Fill In DD": "Main D"},
}


def _require(analysis: dict | None, effective: dict | None) -> dict:
    if not analysis or not effective:
        raise StyleError("Analyze the song first.")
    drums = analysis.get("drums")
    if drums is None:
        raise StyleError("This project was analyzed before drum extraction existed. Click Re-analyze.")
    if drums.get("status") != "ok":
        raise StyleError("No drum groove was found in this song, so a rhythm style can't be built from it.")
    if not effective.get("bpm") or len(effective.get("beats", [])) < 8:
        raise StyleError("No usable beat grid. Set the BPM manually first.")
    return drums


def _extract(drums, eff, layout):
    return groove.extract_style(drums["events"], [b["time"] for b in eff["beats"]],
                                eff["beatsPerBar"], eff["downbeatPhase"], layout)


BUTTONS = {"two": ["A", "B"], "four": ["A", "B", "C", "D"]}
MAX_STYLES = {"two": 2, "four": 1}


def layout_kind(prof) -> str:
    return "two" if len(prof.layout["mains"]) == 2 else "four"


def available_grooves(style: dict) -> list[str]:
    """Song grooves actually found (not fallback copies), lightest first."""
    return [g for g in "ABCD" if (s := style["sections"].get(f"Main {g}")) and s["kind"] == "extracted"]


def default_mapping(kind: str, available: list[str]) -> list[list[str]]:
    if kind == "four":
        return [["A", "B", "C", "D"]]
    # 2-variation keyboards: grooves A+B in style 1, C+D in style 2 when the song has 3+ grooves.
    return [["A", "B"], ["C", "D"]] if len(available) >= 3 else [["A", "B"]]


def validate_mapping(kind: str, styles_: list) -> list[list[str]]:
    """Structural check of a user's variation choice (which song groove plays on which button)."""
    if not isinstance(styles_, list) or not 1 <= len(styles_) <= MAX_STYLES[kind]:
        raise StyleError(f"Choose between 1 and {MAX_STYLES[kind]} style(s).")
    out = []
    for row in styles_:
        if not isinstance(row, list) or len(row) != len(BUTTONS[kind]) or any(g not in "ABCD" or not g for g in row):
            raise StyleError(f"Each style needs a groove (A–D) for buttons {', '.join(BUTTONS[kind])}.")
        out.append([str(g) for g in row])
    return out


def _apply_mapping(sections: dict, kind: str, chosen: list[str]) -> dict:
    """Put the chosen song grooves on the keyboard's Main buttons; each brings its own fill."""
    out = {}
    for btn, g in zip(BUTTONS[kind], chosen):
        out[f"Main {btn}"] = {**sections[f"Main {g}"], "origin": g}
        out[f"Fill In {btn}{btn}"] = {**sections[f"Fill In {g}{g}"], "origin": g}
    if kind == "two":
        out["Fill In AB"] = {**out["Fill In AA"]}   # leaving A towards B
        out["Fill In BA"] = {**out["Fill In BB"]}   # leaving B towards A
    elif "Fill In BA" in sections:                  # 4-variation keyboards: the Break
        out["Fill In BA"] = sections["Fill In BA"]
    for name, sec in sections.items():
        if name.startswith(("Intro", "Ending")):
            out[name] = sec
    return out


def _groove_summaries(style: dict) -> list[dict]:
    out = []
    for g in available_grooves(style):
        s = style["sections"][f"Main {g}"]
        out.append({"groove": g, "bars": s["bars"], "repetitions": s.get("repetitions"), "source": s["source"],
                    "hits": len(s["events"]), "events": s["events"]})
    return out


def _profile(profile_id: str, quantize: float):
    prof = profiles.get(profile_id)
    if prof is None:
        raise StyleError("Unknown keyboard model.")
    if not prof.can_load_styles:
        raise StyleError(f"The {prof.name} can't load style files. Download the MIDI file instead.")
    if not 0.0 <= quantize <= 1.0:
        raise StyleError("Quantize must be between 0 and 100%.")
    return prof


def _stem(project: dict, prof, part: int, n_parts: int) -> str:
    if n_parts == 1:
        return sff.safe_filename(project["projectName"], prof.name_max)
    return f"{sff.safe_filename(project['projectName'], prof.name_max - 2)} {part}"


def build(project: dict, analysis: dict, effective: dict, profile_id: str, quantize: float = 0.0,
          part: int = 1) -> tuple[bytes, dict]:
    prof = _profile(profile_id, quantize)
    drums = _require(analysis, effective)
    kind = layout_kind(prof)
    two_variations = kind == "two"
    saved = (project.get("variationMap") or {}).get(kind)

    # Size ladder for models with a file-size limit: shorten patterns before giving up.
    full = prof.layout["maxMainBars"]
    ladder = [(full, None, None)]
    if full > 2:
        ladder.append((2, None, "Main patterns shortened to 2 bars"))
    ladder.append((1, 1, "Main patterns, intro and ending shortened to 1 bar"))
    adjustments = []
    data = style = sections = None
    n_parts = 1
    for max_bars, edge_bars, note in ladder:
        layout = copy.deepcopy(prof.layout)
        if two_variations:
            layout.update(copy.deepcopy(FOUR_GROOVES))
        layout["maxMainBars"] = max_bars
        if edge_bars:
            layout["intros"] = {k: edge_bars for k in layout["intros"]}
            layout["endings"] = {k: edge_bars for k in layout["endings"]}
        if note:
            adjustments.append(f"{note} to fit the {prof.max_bytes // 1024} KB limit")
        style = _extract(drums, effective, layout)
        if style["status"] != "ok":
            raise StyleError("Too few drum bars were found to build a style.")
        available = available_grooves(style)
        mapping, custom = default_mapping(kind, available), False
        if saved:
            if all(g in available for row in saved for g in row):
                mapping, custom = saved, True
            elif note is None:
                adjustments.append("Your saved variation choice uses grooves this analysis no longer finds; "
                                   "using the automatic choice")
        n_parts = len(mapping)
        if not 1 <= part <= n_parts:
            raise StyleError(f"There {'is' if n_parts == 1 else 'are'} only {n_parts} style(s) for this keyboard.")
        sections = _apply_mapping(style["sections"], kind, mapping[part - 1])
        data = sff.write_style({**style, "sections": sections}, name=_stem(project, prof, part, n_parts),
                               bpm=effective["bpm"], time_signature=effective["timeSignature"],
                               beats_per_bar=effective["beatsPerBar"], ppq=prof.ppq, quantize=quantize,
                               copyright_text=f"LAK Style Generator - from {project['audioFile']}")
        if prof.max_bytes is None or len(data) <= prof.max_bytes:
            break
    if prof.max_bytes is not None and len(data) > prof.max_bytes:
        raise StyleError(f"The style is {len(data) // 1024} KB, above the {prof.name}'s "
                         f"{prof.max_bytes // 1024} KB limit, even with 1-bar patterns.")

    report = {
        "keyboard": prof.public(),
        "fileName": _stem(project, prof, part, n_parts) + prof.extension,
        "part": part,
        "styleFiles": [{"part": k, "fileName": _stem(project, prof, k, n_parts) + prof.extension,
                        "grooves": " + ".join(mapping[k - 1])}
                       for k in range(1, n_parts + 1)],
        "layoutKind": kind,
        "buttons": BUTTONS[kind],
        "maxStyles": MAX_STYLES[kind],
        "mapping": mapping,
        "mappingCustom": custom,
        "grooves": _groove_summaries(style),
        "bytes": len(data),
        "budget": prof.max_bytes,
        "ppq": prof.ppq,
        "quantize": quantize,
        "adjustments": adjustments,
        "feel": style["feel"],
        "patternBars": style["patternBars"],
        "distinctGrooves": style["distinctGrooves"],
        "subdivision": style["subdivision"],
        "beatsPerBar": style["beatsPerBar"],
        "bpm": effective["bpm"],
        "timeSignature": effective["timeSignature"],
        "instrumentParts": ["Rhythm (drums)"],
        "sections": [
            {"name": n, "bars": s["bars"], "kind": s["kind"], "source": s["source"], "origin": s.get("origin"),
             "hits": len(s["events"]), "events": s["events"]}
            for n in sff.SECTION_ORDER if (s := sections.get(n))
        ],
    }
    return data, report


def build_all(project: dict, analysis: dict, effective: dict, profile_id: str,
              quantize: float = 0.0) -> tuple[bytes, list[str]]:
    """Every style file for the model, zipped (one for 4-variation models, up to two otherwise)."""
    first, report = build(project, analysis, effective, profile_id, quantize, 1)
    files = [(report["fileName"], first)]
    for extra in report["styleFiles"][1:]:
        data, r = build(project, analysis, effective, profile_id, quantize, extra["part"])
        files.append((r["fileName"], data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue(), [n for n, _ in files]


def song_midi(project: dict, analysis: dict, effective: dict, ppq: int = 480) -> tuple[bytes, str]:
    drums = _require(analysis, effective)
    events = drums["events"]
    pos = groove.song_beat_positions(events, [b["time"] for b in effective["beats"]],
                                     effective["beatsPerBar"], effective["downbeatPhase"])
    key = None
    if effective.get("keySignature") is not None:
        key = (effective["keySignature"], 0 if effective["mode"] == "major" else 1)
    data = sff.write_song_midi(events, beat_positions=pos, bpm=effective["bpm"],
                               time_signature=effective["timeSignature"], beats_per_bar=effective["beatsPerBar"],
                               key_sig=key, ppq=ppq, title=project["projectName"])
    return data, sff.safe_filename(project["projectName"], 40) + ".mid"
