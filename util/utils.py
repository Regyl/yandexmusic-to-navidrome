from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import requests

from core.models.trackmetdata import TrackMetadata


class DownloadError(Exception):
    """Raised when a Soulseek download fails."""


_ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
# Windows disallows names ending with space or dot; strip any run of these from both ends
_LEADING_TRAILING_WHITESPACE_OR_DOT = re.compile(r"^[\s.]+|[\s.]+$")


def _sanitize_component(value: str) -> str:
    """Sanitize a path component for use in file paths. Removes Windows-invalid
    characters and leading/trailing dots and whitespace (Windows does not allow
    names ending with . or space).
    """
    cleaned = _ILLEGAL_FS_CHARS.sub("", value)
    cleaned = _LEADING_TRAILING_WHITESPACE_OR_DOT.sub("", cleaned)
    return cleaned or "Unknown"


def build_album_directory(music_root: Path, track: TrackMetadata) -> Path:
    primary_artist = track.album_artists[0] if track.album_artists else (
        track.artists[0] if track.artists else "Unknown Artist"
    )
    album_name = track.album or "Unknown Album"

    artist_dir = _sanitize_component(primary_artist)
    album_dir = _sanitize_component(album_name)

    return music_root / artist_dir / album_dir


def build_track_filename(track: TrackMetadata, extension: str) -> str:
    ext = extension.lower().lstrip(".") or "mp3"
    track_no = track.track_number or 0
    track_prefix = f"{track_no:02d} - " if track_no > 0 else ""
    title = _sanitize_component(track.title or "Unknown Title")
    return f"{track_prefix}{title}.{ext}"


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def configure_logging(log_path: Path) -> None:
    ensure_directory(log_path.parent)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def download_cover_image(track: TrackMetadata) -> Optional[bytes]:
    if not track.cover_uri:
        return None

    cover_url = track.cover_uri.replace("%%", "600x600")
    if not cover_url.startswith("http"):
        cover_url = f"https://{cover_url}"

    try:
        resp = requests.get(cover_url, timeout=15)
        if resp.status_code != 200:
            return None
        return resp.content
    except Exception:
        return None
