"""End-to-end API flow: upload → analyze → correct → export, plus error handling."""
import io
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import config
from app.main import app

from .synth import SR, song


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    return TestClient(app)


def _encode(y, fmt):
    buf = io.BytesIO()
    sf.write(buf, y, SR, format=fmt)
    return buf.getvalue()


def _wait(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise TimeoutError


@pytest.mark.parametrize("fmt,ext", [("WAV", "wav"), ("MP3", "mp3")])
def test_full_flow(client, fmt, ext):
    y, _ = song(bpm=96.0, bars=16)
    r = client.post("/api/projects", files={"file": (f"groove.{ext}", _encode(y, fmt), "audio/mpeg")})
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["project"]["id"]
    assert body["project"]["metadata"]["format"] == fmt
    assert body["project"]["metadata"]["duration"] == pytest.approx(len(y) / SR, abs=0.1)

    job = client.post(f"/api/projects/{pid}/analyze").json()
    assert _wait(client, job["id"])["status"] == "done"

    body = client.get(f"/api/projects/{pid}").json()
    assert body["analysis"]["tempo"]["bpm"] == pytest.approx(96.0, rel=0.02)
    assert body["effective"]["key"] == "C Major"
    assert body["effective"]["beats"][0].keys() >= {"time", "bar", "beat"}

    # corrections flow into the effective values and the exported project
    r = client.patch(f"/api/projects/{pid}/corrections", json={"tonic": 9, "mode": "minor", "timeSignature": "3/4"})
    eff = r.json()["effective"]
    assert eff["key"] == "A Minor" and eff["beatsPerBar"] == 3 and set(eff["corrected"]) == {"tonic", "mode", "timeSignature"}

    r = client.patch(f"/api/projects/{pid}/corrections", json={"bpm": 192})
    eff = r.json()["effective"]
    assert eff["bpm"] == 192
    assert len(eff["beats"]) > 1.7 * len(body["effective"]["beats"])  # re-tracked at double time

    doc = client.get(f"/api/projects/{pid}/export.lakstyle").json()
    assert doc["format"] == "lakstyle" and doc["key"] == "A" and doc["bpm"] == 192

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_rejects_wrong_extension(client):
    r = client.post("/api/projects", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_audio"


def test_rejects_renamed_non_audio(client):
    r = client.post("/api/projects", files={"file": ("fake.mp3", b"%PDF-1.7 not audio at all", "audio/mpeg")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_audio"


def test_rejects_corrupt_mp3(client):
    junk = b"ID3" + bytes(np.random.default_rng(1).integers(0, 256, 50_000, dtype=np.uint8))
    r = client.post("/api/projects", files={"file": ("broken.mp3", junk, "audio/mpeg")})
    assert r.status_code == 400
    assert r.json()["error"]["code"] in ("corrupt_audio", "audio_too_short")


def test_rejects_short_audio(client):
    y = np.sin(np.arange(SR * 3) * 0.05).astype(np.float32) * 0.5
    r = client.post("/api/projects", files={"file": ("short.wav", _encode(y, "WAV"), "audio/wav")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "audio_too_short"


def test_silent_audio_fails_analysis_gracefully(client):
    y = np.zeros(SR * 12, dtype=np.float32)
    pid = client.post("/api/projects", files={"file": ("silence.wav", _encode(y, "WAV"), "audio/wav")}).json()["project"]["id"]
    job = client.post(f"/api/projects/{pid}/analyze").json()
    j = _wait(client, job["id"])
    assert j["status"] == "failed" and j["error"]["code"] == "silent_audio"
    assert client.get(f"/api/projects/{pid}").json()["project"]["analysisStatus"] == "failed"


def test_invalid_correction(client):
    y, _ = song(bpm=120.0, bars=6)
    pid = client.post("/api/projects", files={"file": ("a.wav", _encode(y, "WAV"), "audio/wav")}).json()["project"]["id"]
    job = client.post(f"/api/projects/{pid}/analyze").json()
    _wait(client, job["id"])
    r = client.patch(f"/api/projects/{pid}/corrections", json={"bpm": 5000})
    assert r.status_code == 422
    r = client.patch(f"/api/projects/{pid}/corrections", json={"_beats": [1, 2]})
    assert r.status_code == 422


def test_save_and_reset_variations(client):
    y, _ = song(bpm=120.0, bars=6)
    pid = client.post("/api/projects", files={"file": ("v.wav", _encode(y, "WAV"), "audio/wav")}).json()["project"]["id"]
    r = client.put(f"/api/projects/{pid}/variations", json={"layout": "two", "styles": [["B", "A"]]})
    assert r.status_code == 200 and r.json()["variationMap"] == {"two": [["B", "A"]]}
    assert client.get(f"/api/projects/{pid}").json()["project"]["variationMap"] == {"two": [["B", "A"]]}
    assert client.put(f"/api/projects/{pid}/variations", json={"layout": "two", "styles": [["A"]]}).status_code == 400
    assert client.put(f"/api/projects/{pid}/variations", json={"layout": "nine", "styles": None}).status_code == 422
    r = client.put(f"/api/projects/{pid}/variations", json={"layout": "two", "styles": None})
    assert r.json()["variationMap"] == {}


def test_public_mode_keeps_visitors_apart(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC", True)
    y, _ = song(bpm=120.0, bars=6)
    up = lambda cid: client.post("/api/projects", headers={"X-Client-Id": cid},
                                 files={"file": ("s.wav", _encode(y, "WAV"), "audio/wav")}).json()["project"]["id"]
    a, b = up("browser-aaaaaaaa"), up("browser-bbbbbbbb")
    ids = lambda cid: {p["id"] for p in client.get("/api/projects", headers={"X-Client-Id": cid}).json()["projects"]}
    assert ids("browser-aaaaaaaa") == {a}
    assert ids("browser-bbbbbbbb") == {b}
    assert ids("someone-elsezzzz") == set()
    assert client.get("/api/projects").json()["projects"] == []          # no id → nothing listed
    monkeypatch.setattr(config, "PUBLIC", False)
    assert ids("someone-elsezzzz") == {a, b}                               # local use: everything


def test_retention_cleanup(client, monkeypatch):
    import os
    import time

    from app import projects

    y, _ = song(bpm=120.0, bars=6)
    pid = client.post("/api/projects", files={"file": ("s.wav", _encode(y, "WAV"), "audio/wav")}).json()["project"]["id"]
    monkeypatch.setattr(config, "RETENTION_DAYS", 1.0)
    assert projects.cleanup_expired() == 0
    f = config.PROJECTS_DIR / pid / "project.json"
    old = time.time() - 2 * 86400
    os.utime(f, (old, old))
    assert projects.cleanup_expired() == 1
    assert client.get(f"/api/projects/{pid}").status_code == 404
