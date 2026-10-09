"""Audio validation, decoding and metadata extraction (Phase 1)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import config
from ..errors import (
    AudioTooLongError,
    AudioTooShortError,
    CorruptAudioError,
    SilentAudioError,
    UnsupportedAudioError,
)

_FORMAT_NAMES = {"mp3": "MP3", "wav": "WAV", "flac": "FLAC", "ogg": "OGG"}


def sniff_format(head: bytes) -> str | None:
    """Identify the container from its first bytes, independent of the file extension."""
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"  # bare MPEG audio frame sync
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    return None


def validate_upload(filename: str, head: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in config.ALLOWED_EXTENSIONS))
        raise UnsupportedAudioError(f"“{ext or filename}” files aren't supported. Please upload {allowed}.")
    fmt = sniff_format(head)
    if fmt is None:
        raise UnsupportedAudioError(
            "This file doesn't look like audio. It may be renamed from another format or damaged."
        )
    return fmt


@dataclass
class AudioMetadata:
    format: str
    sample_rate: int
    channels: int
    duration_sec: float
    file_size: int
    bitrate_kbps: int | None
    codec: str | None
    title: str | None = None
    artist: str | None = None
    album: str | None = None

    def to_dict(self) -> dict:
        return {
            "format": _FORMAT_NAMES.get(self.format, self.format.upper()),
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "duration": round(self.duration_sec, 3),
            "fileSize": self.file_size,
            "bitrateKbps": self.bitrate_kbps,
            "codec": self.codec,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
        }


def _read_tags(path: Path) -> dict:
    try:
        import mutagen

        f = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if not f or not f.tags:
        return {}

    def first(key):
        v = f.tags.get(key)
        return str(v[0]).strip() if v else None

    return {"title": first("title"), "artist": first("artist"), "album": first("album")}


def read_metadata(path: Path, fmt: str) -> AudioMetadata:
    try:
        info = sf.info(str(path))
    except Exception as e:
        raise CorruptAudioError(
            "We couldn't read this audio file. It may be corrupted or incompletely downloaded.",
            detail=str(e),
        ) from e

    if info.samplerate <= 0 or info.frames <= 0:
        raise CorruptAudioError("This audio file contains no playable audio.")

    duration = info.frames / info.samplerate
    size = path.stat().st_size
    bitrate = int(round(size * 8 / duration / 1000)) if duration > 0 else None
    meta = AudioMetadata(
        format=fmt,
        sample_rate=info.samplerate,
        channels=info.channels,
        duration_sec=duration,
        file_size=size,
        bitrate_kbps=bitrate,
        codec=info.subtype_info or info.subtype,
        **_read_tags(path),
    )
    check_duration(meta.duration_sec)
    return meta


def check_duration(duration: float) -> None:
    if duration < config.MIN_DURATION_SEC:
        raise AudioTooShortError(
            f"This audio is only {duration:.1f} s long. At least {config.MIN_DURATION_SEC:.0f} s "
            "is needed to detect tempo and key."
        )
    if duration > config.MAX_DURATION_SEC:
        raise AudioTooLongError(
            f"This audio is {duration / 60:.0f} minutes long. The maximum is "
            f"{config.MAX_DURATION_SEC / 60:.0f} minutes."
        )


def load_mono(path: Path, sr: int = config.ANALYSIS_SR) -> np.ndarray:
    """Decode to mono float32 at the analysis sample rate."""
    import librosa

    try:
        data, native_sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as e:
        raise CorruptAudioError(
            "The audio stopped decoding part-way through. The file is probably damaged.",
            detail=str(e),
        ) from e

    y = data.mean(axis=1)
    if not np.all(np.isfinite(y)):
        raise CorruptAudioError("The decoded audio contains invalid samples.")
    if native_sr != sr:
        y = librosa.resample(y, orig_sr=native_sr, target_sr=sr, res_type="soxr_hq")

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak < 1e-3:  # below -60 dBFS
        raise SilentAudioError("This audio is silent or almost silent, so there's nothing to analyze.")
    return y.astype(np.float32)
