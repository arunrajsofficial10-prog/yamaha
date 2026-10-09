"""File-based project store.

data/projects/<id>/
    project.json    project record + user corrections (spec §28)
    audio.<ext>     original upload, untouched
    analysis.json   raw detector output (never modified by corrections)

Detected values and user corrections are stored separately; `effective()` merges them. Every
later phase (chords, MIDI, style) must read from `effective()` so corrections carry through (spec §25).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .audio.tempo import label_beats
from .errors import InvalidCorrectionError, NotFoundError
from .theory import PITCH_CLASSES, key_name, key_signature_accidentals

_lock = threading.RLock()
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

SUPPORTED_TIME_SIGNATURES = ["2/4", "3/4", "4/4", "5/4", "6/8", "7/8", "9/8", "12/8"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir(pid: str) -> Path:
    if not _ID_RE.match(pid):
        raise NotFoundError("Project not found.")
    d = config.PROJECTS_DIR / pid
    if not (d / "project.json").exists():
        raise NotFoundError("Project not found.")
    return d


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def new_project_dir() -> tuple[str, Path]:
    pid = uuid.uuid4().hex
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True, exist_ok=False)
    return pid, d


def create(pid: str, original_name: str, stored_name: str, metadata: dict, owner: str | None = None) -> dict:
    stem = Path(original_name).stem
    project = {
        "id": pid,
        "owner": owner,
        "projectName": metadata.get("title") or stem,
        "audioFile": original_name,
        "audioStored": stored_name,
        "createdAt": _now(),
        "updatedAt": _now(),
        "metadata": metadata,
        "analysisStatus": "none",
        "analysisError": None,
        "corrections": {},
        "keyboardProfile": None,  # chosen at export time (Phase 11)
    }
    _write_json(config.PROJECTS_DIR / pid / "project.json", project)
    return project


def load(pid: str) -> dict:
    return json.loads((_dir(pid) / "project.json").read_text(encoding="utf-8"))


def save(project: dict) -> dict:
    with _lock:
        project["updatedAt"] = _now()
        _write_json(_dir(project["id"]) / "project.json", project)
    return project


def update(pid: str, **fields) -> dict:
    with _lock:
        p = load(pid)
        p.update(fields)
        return save(p)


def audio_path(pid: str) -> Path:
    p = load(pid)
    return _dir(pid) / p["audioStored"]


def load_analysis(pid: str) -> dict | None:
    f = _dir(pid) / "analysis.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def save_analysis(pid: str, analysis: dict) -> None:
    _write_json(_dir(pid) / "analysis.json", analysis)


def visible_to(project: dict, owner: str | None) -> bool:
    """In public mode a browser sees only songs it uploaded; locally everything is shown."""
    if not config.PUBLIC:
        return True
    return bool(owner) and project.get("owner") == owner


def cleanup_expired() -> int:
    """Delete projects not touched for RETENTION_DAYS (public hosting keeps the disk small)."""
    if config.RETENTION_DAYS <= 0 or not config.PROJECTS_DIR.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - config.RETENTION_DAYS * 86400
    removed = 0
    for d in config.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        try:
            stamp = f.stat().st_mtime if f.exists() else d.stat().st_mtime
            if stamp < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def list_all(owner: str | None = None) -> list[dict]:
    out = []
    if not config.PROJECTS_DIR.exists():
        return out
    for d in config.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        if f.exists():
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                if not visible_to(p, owner):
                    continue
                out.append({k: p.get(k) for k in ("id", "projectName", "audioFile", "createdAt", "updatedAt", "analysisStatus")}
                           | {"duration": p.get("metadata", {}).get("duration")})
            except (json.JSONDecodeError, OSError):
                continue
    out.sort(key=lambda p: p["updatedAt"] or "", reverse=True)
    return out


def delete(pid: str) -> None:
    shutil.rmtree(_dir(pid))


# --- corrections ---------------------------------------------------------------------------------

def validate_corrections(c: dict) -> dict:
    """Normalise a corrections patch. `None` values remove a correction."""
    out = {}
    for k, v in c.items():
        if v is None:
            out[k] = None
            continue
        if k == "bpm":
            v = float(v)
            if not 30 <= v <= 300:
                raise InvalidCorrectionError("BPM must be between 30 and 300.")
            out[k] = round(v, 2)
        elif k == "tonic":
            v = int(v)
            if not 0 <= v <= 11:
                raise InvalidCorrectionError("Key tonic must be a pitch class 0–11.")
            out[k] = v
        elif k == "mode":
            if v not in ("major", "minor"):
                raise InvalidCorrectionError("Mode must be major or minor.")
            out[k] = v
        elif k == "timeSignature":
            if v not in SUPPORTED_TIME_SIGNATURES:
                raise InvalidCorrectionError(f"Supported time signatures: {', '.join(SUPPORTED_TIME_SIGNATURES)}.")
            out[k] = v
        elif k == "downbeatPhase":
            v = int(v)
            if not 0 <= v <= 11:
                raise InvalidCorrectionError("Downbeat offset is out of range.")
            out[k] = v
        elif k == "beatOffsetMs":
            v = float(v)
            if not -500 <= v <= 500:
                raise InvalidCorrectionError("Beat offset must be within ±500 ms.")
            out[k] = round(v, 1)
        elif k == "projectName":
            v = str(v).strip()[:120]
            if not v:
                raise InvalidCorrectionError("Project name can't be empty.")
            out[k] = v
        else:
            raise InvalidCorrectionError(f"“{k}” can't be corrected.")
    return out


def beats_per_bar(time_signature: str) -> int:
    num, den = (int(x) for x in time_signature.split("/"))
    # Compound meters (6/8, 9/8, 12/8) are felt — and tracked — in dotted-quarter pulses.
    if den == 8 and num % 3 == 0 and num > 3:
        return num // 3
    return num


def effective(project: dict, analysis: dict | None) -> dict:
    """Detected values with user corrections applied. Downstream phases consume this."""
    c = project.get("corrections", {})
    t = (analysis or {}).get("tempo", {})
    k = (analysis or {}).get("key", {})

    bpm = c.get("bpm", t.get("bpm"))
    beats = c.get("_beats") if c.get("bpm") is not None and c.get("_beats") else t.get("beats", [])
    offset = c.get("beatOffsetMs", 0.0) / 1000.0
    beats = [round(b + offset, 4) for b in beats if b + offset >= 0]
    ts = c.get("timeSignature", t.get("timeSignature") or "4/4")
    bpb = beats_per_bar(ts)
    phase = c.get("downbeatPhase", t.get("downbeatPhase", 0)) % bpb
    labelled, downbeats = label_beats(beats, bpb, phase)

    tonic = c.get("tonic", k.get("tonic"))
    mode = c.get("mode", k.get("mode"))

    return {
        "bpm": bpm,
        "timeSignature": ts,
        "beatsPerBar": bpb,
        "downbeatPhase": phase,
        "beats": labelled,
        "downbeats": downbeats,
        "bars": (labelled[-1]["bar"] if labelled else 0),
        "tonic": tonic,
        "mode": mode,
        "key": key_name(tonic, mode) if tonic is not None and mode else None,
        "keySignature": key_signature_accidentals(tonic, mode) if tonic is not None and mode else None,
        "corrected": sorted(x for x in c if not x.startswith("_")),
    }


def export_document(project: dict, analysis: dict | None) -> dict:
    """Project file (spec §28). `.lakstyle` is this JSON document."""
    eff = effective(project, analysis)
    return {
        "format": "lakstyle",
        "formatVersion": 1,
        "projectName": project["projectName"],
        "audioFile": project["audioFile"],
        "bpm": eff["bpm"],
        "key": PITCH_CLASSES[eff["tonic"]] if eff["tonic"] is not None else None,
        "mode": eff["mode"],
        "timeSignature": eff["timeSignature"],
        "beats": eff["beats"],
        "sections": [],
        "chords": [],
        "tracks": [],
        "style": {},
        "corrections": {k: v for k, v in project.get("corrections", {}).items() if not k.startswith("_")},
        "metadata": project.get("metadata"),
        "analysis": analysis,
    }
