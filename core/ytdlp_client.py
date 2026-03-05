"""Download audio using yt-dlp (YouTube and other supported sites)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import yt_dlp

from core.yandex_client import TrackMetadata
from util.utils import DownloadError

_YTDLP_DOWNLOAD_ENV = "YTDLP_DOWNLOAD_DIR"

_ydl_singleton: Optional[yt_dlp.YoutubeDL] = None


def _get_download_dir() -> Path:
    dir_env = os.getenv(_YTDLP_DOWNLOAD_ENV)
    if dir_env:
        download_dir = Path(dir_env)
    else:
        download_dir = Path.cwd() / "ytdlp_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def _get_ydl(timeout_seconds: int) -> yt_dlp.YoutubeDL:
    global _ydl_singleton
    if _ydl_singleton is None:
        download_dir = _get_download_dir()
        outtmpl = str(download_dir / "%(id)s.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": timeout_seconds,
            "retries": 3,
            "fragment_retries": 3,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        _ydl_singleton = yt_dlp.YoutubeDL(opts)
    return _ydl_singleton


def download_track(track: TrackMetadata, timeout_seconds: int) -> Tuple[Path, str]:
    """Download a single track via yt-dlp (YouTube search). Returns (path, extension)."""
    query = f"{track.title} - {', '.join(track.artists) or ''}"
    url = f"ytsearch1:{query}"

    try:
        ydl = _get_ydl(timeout_seconds)
        info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(f"yt-dlp: {exc}") from exc
    except Exception as exc:
        raise DownloadError(f"yt-dlp: {exc}") from exc

    if not info:
        raise DownloadError(f"No yt-dlp results for query {query!r}")

    # For ytsearch1: (and other playlists), download info is on the first entry, not the root
    requested = info.get("requested_downloads") or []
    if not requested and info.get("entries"):
        first_entry = info["entries"][0]
        if isinstance(first_entry, dict) and first_entry:
            requested = first_entry.get("requested_downloads") or []
    if not requested:
        raise DownloadError(f"No yt-dlp download for query {query!r}")

    filepath = requested[0].get("filepath")
    if not filepath:
        raise DownloadError(f"No yt-dlp filepath for query {query!r}")

    path = Path(filepath)
    if not path.exists():
        # FFmpegExtractAudio may replace original with .mp3
        mp3_path = path.with_suffix(".mp3")
        if mp3_path.exists():
            path = mp3_path
        else:
            raise DownloadError(f"yt-dlp reported download but file missing: {path}")

    ext = path.suffix.lstrip(".").lower() or "mp3"
    return path, ext


def download_track_from_url(url: str, timeout_seconds: int) -> Tuple[Path, str]:
    """Download a single track from a direct URL (e.g. SoundCloud track) via yt-dlp. Returns (path, extension)."""
    try:
        ydl = _get_ydl(timeout_seconds)
        info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(f"yt-dlp: {exc}") from exc
    except Exception as exc:
        raise DownloadError(f"yt-dlp: {exc}") from exc

    if not info:
        raise DownloadError(f"No yt-dlp results for url {url!r}")

    requested = info.get("requested_downloads") or []
    if not requested and info.get("entries"):
        first_entry = info["entries"][0]
        if isinstance(first_entry, dict) and first_entry:
            requested = first_entry.get("requested_downloads") or []
    if not requested:
        raise DownloadError(f"No yt-dlp download for url {url!r}")

    filepath = requested[0].get("filepath")
    if not filepath:
        raise DownloadError(f"No yt-dlp filepath for url {url!r}")

    path = Path(filepath)
    if not path.exists():
        mp3_path = path.with_suffix(".mp3")
        if mp3_path.exists():
            path = mp3_path
        else:
            raise DownloadError(f"yt-dlp reported download but file missing: {path}")

    ext = path.suffix.lstrip(".").lower() or "mp3"
    return path, ext
