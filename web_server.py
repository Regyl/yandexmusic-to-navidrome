"""Web server for migration status UI and API."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
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


_LOG_TAIL_BYTES = 200_000
_LOG_TAIL_LINES = 1000


@app.get("/api/logs")
def get_logs() -> dict:
    """Return tail of migration.log from YM_NAVIDROME_DATA directory."""
    try:
        data_dir = _get_data_dir()
    except RuntimeError:
        return {"content": None, "error": "YM_NAVIDROME_DATA not set"}
    log_path = data_dir / "migration.log"
    if not log_path.exists():
        return {"content": "", "error": None}
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            if size <= _LOG_TAIL_BYTES:
                raw = f.read()
            else:
                f.seek(size - _LOG_TAIL_BYTES)
                raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        tail = lines[-_LOG_TAIL_LINES:] if len(lines) > _LOG_TAIL_LINES else lines
        content = "\n".join(tail)
        return {"content": content, "error": None}
    except OSError as e:
        return {"content": None, "error": str(e)}


def _run_job(
    command: str,
    soundcloud_username: str | None = None,
    redownload_playlist_name: str | None = None,
) -> None:
    from util.utils import configure_logging
    from cli_core import (
        _build_config,
        _get_data_dir as main_get_data_dir,
        run_import_soundcloud_likes,
        run_redownload_playlist,
        run_retry_failed,
        run_sync_like_tracks,
    )

    global _current_job
    data_dir = main_get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    try:
        if command == "ym-import":
            run_sync_like_tracks(cfg)
        elif command == "ym-import-test":
            run_sync_like_tracks(cfg, limit=1)
        elif command == "retry-failed":
            run_retry_failed(cfg)
        elif command == "soundcloud-import":
            run_import_soundcloud_likes(soundcloud_username, cfg)
        elif command == "soundcloud-import-test":
            run_import_soundcloud_likes(soundcloud_username, cfg, limit=1)
        elif command == "redownload-playlist":
            run_redownload_playlist(redownload_playlist_name or "_REDOWNLOAD", cfg)
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


class RunSoundcloudImportLikesBody(BaseModel):
    username: str


class RunRedownloadPlaylistBody(BaseModel):
    playlist_name: str = "_REDOWNLOAD"


@app.post("/api/run/ym-import")
def run_ym_import() -> dict:
    """Start Yandex Music liked tracks sync. One job at a time."""
    global _current_job
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
        kwargs={"command": "ym-import"},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/ym-import-test")
def run_ym_import_test_api() -> dict:
    """Test Yandex Music import: process only one track and upload into Navidrome target folder."""
    global _current_job
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "ym-import-test",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "ym-import-test"},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/retry-failed")
def run_retry_failed_api() -> dict:
    """Start retry of failed tracks. One job at a time."""
    global _current_job
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
        kwargs={"command": "retry-failed"},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/soundcloud-import")
def run_soundcloud_import_likes_api(body: RunSoundcloudImportLikesBody) -> dict:
    """Start SoundCloud import: liked tracks and all user playlists for the given username. One job at a time."""
    global _current_job
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(422, "username is required")
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "soundcloud-import",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "soundcloud-import", "soundcloud_username": username},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/soundcloud-import-test")
def run_soundcloud_import_test_api(body: RunSoundcloudImportLikesBody) -> dict:
    """Test SoundCloud import: process only one track and upload into Navidrome target folder."""
    global _current_job
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(422, "username is required")
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "soundcloud-import-test",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "soundcloud-import-test", "soundcloud_username": username},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


@app.post("/api/run/redownload-playlist")
def run_redownload_playlist_api(
    body: RunRedownloadPlaylistBody | None = Body(default=None),
) -> dict:
    """Fetch tracks from Navidrome playlist (default: _REDOWNLOAD), redownload from Yandex Music, replace files."""
    global _current_job
    playlist_name = (body.playlist_name if body else "_REDOWNLOAD") or "_REDOWNLOAD"
    with _job_lock:
        if _current_job and _current_job.get("status") == "running":
            raise HTTPException(409, "A job is already running")
        _current_job = {
            "command": "redownload-playlist",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "playlist_url": None,
        }
    thread = threading.Thread(
        target=_run_job,
        kwargs={"command": "redownload-playlist", "redownload_playlist_name": playlist_name},
        daemon=True,
    )
    thread.start()
    with _job_lock:
        job = _current_job
    return {"ok": True, "job": _job_to_response(job)}


if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
