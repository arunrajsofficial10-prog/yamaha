"""In-process background jobs with progress reporting.

Analysis is CPU-bound, so jobs run one at a time on a single worker thread. Later phases that
need GPU / long-running engines (e.g. stem separation) can swap this for a real queue without
changing the API contract: POST starts a job, GET /api/jobs/{id} polls it.
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .errors import AppError

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def active_for(project_id: str) -> dict | None:
    with _lock:
        for j in _jobs.values():
            if j["projectId"] == project_id and j["status"] in ("queued", "running"):
                return dict(j)
    return None


def _set(job_id: str, **kw) -> None:
    with _lock:
        _jobs[job_id].update(kw)


def submit(project_id: str, kind: str, fn: Callable[[Callable], dict], on_done: Callable[[dict], None],
           on_error: Callable[[str], None]) -> dict:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"id": job_id, "projectId": project_id, "kind": kind, "status": "queued",
                         "stage": None, "progress": 0.0, "message": "Waiting to start…", "error": None}

    def progress(stage, frac, message):
        _set(job_id, status="running", stage=stage, progress=round(frac, 3), message=message)

    def run():
        try:
            progress("start", 0.0, "Starting…")
            result = fn(progress)
            on_done(result)
            _set(job_id, status="done", progress=1.0, message="Done")
        except AppError as e:
            log.warning("job %s failed: %s (%s)", job_id, e.message, e.detail)
            on_error(e.message)
            _set(job_id, status="failed", error={"code": e.code, "message": e.message})
        except Exception as e:  # never leak a traceback to the UI
            log.exception("job %s crashed", job_id)
            msg = "Analysis failed unexpectedly. Try another file, or check the server log for details."
            on_error(msg)
            _set(job_id, status="failed", error={"code": "internal", "message": msg, "detail": type(e).__name__})

    _executor.submit(run)
    return get(job_id)
