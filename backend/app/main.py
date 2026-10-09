"""HTTP API + static frontend.

Run:  backend\\.venv\\Scripts\\python -m uvicorn app.main:app --app-dir backend --port 8765
"""
from __future__ import annotations

import logging
import re

from fastapi import Body, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, jobs, projects, styles
from .keyboards import profiles as kb_profiles
from .audio import loader, pipeline
from .errors import AppError, FileTooLargeError, InvalidCorrectionError
from .projects import SUPPORTED_TIME_SIGNATURES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="LAK Style Generator", version="0.3.0")
config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
projects.cleanup_expired()

_CLIENT_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _client(x_client_id: str | None) -> str | None:
    """Anonymous per-browser id (random, stored in the browser) used to keep visitors' songs apart."""
    return x_client_id if x_client_id and _CLIENT_RE.match(x_client_id) else None

_MEDIA_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "ogg": "audio/ogg"}


@app.exception_handler(AppError)
async def _app_error(_: Request, e: AppError):
    return JSONResponse(status_code=e.status_code, content={"error": {"code": e.code, "message": e.message}})


@app.exception_handler(Exception)
async def _unhandled(_: Request, e: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal",
                        "message": "Something went wrong on the server. Please try again."}})


def _project_payload(pid: str) -> dict:
    p = projects.load(pid)
    a = projects.load_analysis(pid)
    return {"project": p, "analysis": a, "effective": projects.effective(p, a) if a else None,
            "job": jobs.active_for(pid)}


# --- API -------------------------------------------------------------------------------------------

@app.get("/api/health")
def health():
    import librosa, soundfile

    return {"ok": True, "librosa": librosa.__version__, "libsndfile": soundfile.__libsndfile_version__,
            "timeSignatures": SUPPORTED_TIME_SIGNATURES}


@app.get("/api/projects")
def list_projects(x_client_id: str | None = Header(default=None)):
    return {"projects": projects.list_all(_client(x_client_id))}


@app.post("/api/projects", status_code=201)
async def upload(file: UploadFile = File(...), x_client_id: str | None = Header(default=None)):
    projects.cleanup_expired()
    head = await file.read(16)
    fmt = loader.validate_upload(file.filename or "", head)

    pid, d = projects.new_project_dir()
    stored = f"audio.{fmt}"
    dest = d / stored
    size = len(head)
    try:
        with dest.open("wb") as out:
            out.write(head)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise FileTooLargeError(f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                out.write(chunk)
        meta = loader.read_metadata(dest, fmt)
    except Exception:
        import shutil

        shutil.rmtree(d, ignore_errors=True)
        raise

    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", file.filename or stored)
    projects.create(pid, safe_name, stored, meta.to_dict(), owner=_client(x_client_id))
    return _project_payload(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _project_payload(pid)


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str):
    projects.delete(pid)
    return Response(status_code=204)


@app.get("/api/projects/{pid}/audio")
def get_audio(pid: str):
    path = projects.audio_path(pid)
    fmt = path.suffix.lstrip(".")
    return FileResponse(path, media_type=_MEDIA_TYPES.get(fmt, "application/octet-stream"))


@app.post("/api/projects/{pid}/analyze", status_code=202)
def analyze(pid: str):
    running = jobs.active_for(pid)
    if running:
        return running
    path = projects.audio_path(pid)
    projects.update(pid, analysisStatus="running", analysisError=None)

    def done(result):
        projects.save_analysis(pid, result)
        # A fresh analysis invalidates beat-grid corrections that were derived from the old beats.
        p = projects.load(pid)
        p["corrections"].pop("_beats", None)
        p["analysisStatus"] = "done"
        projects.save(p)

    return jobs.submit(pid, "analysis", lambda progress: pipeline.run_analysis(path, progress), done,
                       lambda msg: projects.update(pid, analysisStatus="failed", analysisError=msg))


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = jobs.get(job_id)
    if not j:
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Job not found."}})
    return j


@app.patch("/api/projects/{pid}/corrections")
def correct(pid: str, patch: dict = Body(...)):
    fields = projects.validate_corrections(patch)
    p = projects.load(pid)
    a = projects.load_analysis(pid)
    if a is None and any(k != "projectName" for k in fields):
        raise InvalidCorrectionError("Analyze the song before correcting its values.")

    if "projectName" in fields:
        p["projectName"] = fields.pop("projectName")
    c = p.setdefault("corrections", {})
    for k, v in fields.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v

    if "bpm" in fields:
        detected = (a or {}).get("tempo", {}).get("bpm")
        if fields["bpm"] is None or (detected and abs(fields["bpm"] - detected) < 0.05):
            c.pop("bpm", None)
            c.pop("_beats", None)
        else:
            # Re-track the beat grid locked to the corrected tempo so beats stay on real onsets.
            c["_beats"] = pipeline.retrack_beats(projects.audio_path(pid), fields["bpm"])
    projects.save(p)
    return _project_payload(pid)


@app.get("/api/keyboards")
def keyboards():
    return {"keyboards": [p.public() for p in kb_profiles.PROFILES]}


def _style_inputs(pid: str):
    p = projects.load(pid)
    a = projects.load_analysis(pid)
    return p, a, projects.effective(p, a) if a else None


@app.get("/api/projects/{pid}/style")
def style_report(pid: str, keyboard: str, quantize: float = 0.0, part: int = 1):
    p, a, eff = _style_inputs(pid)
    _, report = styles.build(p, a, eff, keyboard, quantize, part)
    return report


@app.get("/api/projects/{pid}/style.sty")
def download_style(pid: str, keyboard: str, quantize: float = 0.0, part: int = 1):
    p, a, eff = _style_inputs(pid)
    data, report = styles.build(p, a, eff, keyboard, quantize, part)
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{report["fileName"]}"'})


@app.put("/api/projects/{pid}/variations")
def save_variations(pid: str, body: dict = Body(...)):
    """Which song groove plays on which Main button. {"layout": "two"|"four", "styles": [[...]] | null}"""
    kind = body.get("layout")
    if kind not in styles.BUTTONS:
        raise InvalidCorrectionError("Unknown variation layout.")
    p = projects.load(pid)
    vm = dict(p.get("variationMap") or {})
    if body.get("styles") is None:
        vm.pop(kind, None)
    else:
        vm[kind] = styles.validate_mapping(kind, body["styles"])
    p["variationMap"] = vm
    projects.save(p)
    return {"variationMap": vm}


@app.get("/api/projects/{pid}/styles.zip")
def download_styles_zip(pid: str, keyboard: str, quantize: float = 0.0):
    p, a, eff = _style_inputs(pid)
    data, _ = styles.build_all(p, a, eff, keyboard, quantize)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{_fname(p)} styles.zip"'})


@app.get("/api/projects/{pid}/drums.mid")
def download_midi(pid: str):
    p, a, eff = _style_inputs(pid)
    data, fname = styles.song_midi(p, a, eff)
    return Response(data, media_type="audio/midi", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/projects/{pid}/analysis.json")
def download_analysis(pid: str):
    a = projects.load_analysis(pid)
    if a is None:
        raise InvalidCorrectionError("This project hasn't been analyzed yet.")
    p = projects.load(pid)
    return JSONResponse(a, headers={"Content-Disposition": f'attachment; filename="{_fname(p)}.analysis.json"'})


@app.get("/api/projects/{pid}/export.lakstyle")
def download_project(pid: str):
    p = projects.load(pid)
    doc = projects.export_document(p, projects.load_analysis(pid))
    return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{_fname(p)}.lakstyle"'})


def _fname(p: dict) -> str:
    return re.sub(r"[^\w\- ]+", "_", p["projectName"]).strip() or "project"


# --- frontend --------------------------------------------------------------------------------------
@app.middleware("http")
async def _revalidate_frontend(request: Request, call_next):
    """ES modules are cached aggressively; after an update a stale api.js breaks imports.
    `no-cache` makes the browser revalidate (cheap 304 via ETag) on every load."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response



app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
