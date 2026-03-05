"""Web server for migration status UI and API."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.database import MigrationDB

load_dotenv()

app = FastAPI(title="YM-Navidrome Migration Status")
_STATIC_DIR = Path(__file__).parent / "web" / "static"

_job_lock = threading.Lock()
_current_job: dict | None = None


def _get_data_dir() -> Path:
    env_dir = os.getenv("YM_NAVIDROME_DATA")
    if not env_dir:
        raise RuntimeError("YM_NAVIDROME_DATA environment variable not set")
    return Path(env_dir)


@app.get("/api/status")
def get_status() -> dict:
    """Return migration stats and failed tracks list."""
    try:
        data_dir = _get_data_dir()
    except RuntimeError:
        with _job_lock:
            job = _current_job
        return {
            "success_count": 0,
            "failed_count": 0,
            "total_count": 0,
            "failed_tracks": [],
            "job": _job_to_response(job),
            "error": "YM_NAVIDROME_DATA not set",
        }
    db_path = data_dir / "migration.db"
    if not db_path.exists():
        with _job_lock:
            job = _current_job
        return {
            "success_count": 0,
            "failed_count": 0,
            "total_count": 0,
            "failed_tracks": [],
            "job": _job_to_response(job),
            "error": "no db",
        }
    with MigrationDB(db_path) as db:
        success_count = db.get_successful_count()
        failed_count = db.get_failed_count()
        total_count = db.get_total_count()
        failed_tracks = [
            {"track_id": tid, "error": err}
            for tid, err in db.get_failed_tracks()
        ]
    with _job_lock:
        job = _current_job

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "total_count": total_count,
        "failed_tracks": failed_tracks,
        "job": _job_to_response(job),
    }


def _job_to_response(job: dict | None) -> dict | None:
    if job is None:
        return None
    return {
        "command": job.get("command", ""),
        "status": job.get("status", ""),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "playlist_url": job.get("playlist_url"),
    }


def _run_job(command: str, timeout_minutes: int, playlist_url: str | None = None) -> None:
    from util.utils import configure_logging
    from main import _build_config, _get_data_dir as main_get_data_dir, run_import_soundcloud_playlist, run_retry_failed, run_sync_like_tracks

    global _current_job
    data_dir = main_get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config(timeout_minutes)
    try:
        if command == "ym-import":
            run_sync_like_tracks(cfg)
        elif command == "retry-failed":
            run_retry_failed(cfg)
        elif command == "soundcloud-import" and playlist_url:
            run_import_soundcloud_playlist(playlist_url, cfg)
    except Exception as e:
        with _job_lock:
            if _current_job and _current_job.get("command") == command:
                _current_job["status"] = "failed"
                _current_job["finished_at"] = datetime.now(timezone.utc).isoformat()
                _current_job["error"] = str(e)
        raise
    with _job_lock:
        if _current_job and _current_job.get("command") == command:
            _current_job["status"] = "finished"
            _current_job["finished_at"] = datetime.now(timezone.utc).isoformat()
            _current_job["error"] = None


class RunYmImportBody(BaseModel):
    timeout_minutes: int = 10


class RunRetryFailedBody(BaseModel):
    timeout_minutes: int = 10


class RunSoundcloudImportBody(BaseModel):
    playlist_url: str
    timeout_minutes: int = 10


@app.post("/api/run/ym-import")
def run_ym_import(body: RunYmImportBody) -> dict:
    """Start Yandex Music liked tracks sync. One job at a time."""
    if body.timeout_minutes < 1:
        raise HTTPException(422, "timeout_minutes must be >= 1")
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "ym-import",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "ym-import", "timeout_minutes": body.timeout_minutes},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/retry-failed")
def run_retry_failed_api(body: RunRetryFailedBody) -> dict:
    """Start retry of failed tracks. One job at a time."""
    if body.timeout_minutes < 1:
        raise HTTPException(422, "timeout_minutes must be >= 1")
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "retry-failed",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "retry-failed", "timeout_minutes": body.timeout_minutes},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/soundcloud-import")
def run_soundcloud_import_api(body: RunSoundcloudImportBody) -> dict:
    """Start SoundCloud playlist import. One job at a time."""
    if body.timeout_minutes < 1:
        raise HTTPException(422, "timeout_minutes must be >= 1")
    url = (body.playlist_url or "").strip()
    if not url:
        raise HTTPException(422, "playlist_url is required")
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "soundcloud-import",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": url,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "soundcloud-import", "timeout_minutes": body.timeout_minutes, "playlist_url": url},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
