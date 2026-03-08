from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from random import randint
from typing import List, Optional, Tuple

from yandex_music import Client, Track
from yandex_music.exceptions import NetworkError

from core.models.trackmetdata import TrackMetadata
from util.utils import DownloadError

_RETRY_DELAY_SECONDS = 60  # 1 minute
_YM_TOKEN = "YANDEX_MUSIC_TOKEN"
_YM_DOWNLOAD_ENV = "YM_DOWNLOAD_DIR"
_YM_PERIOD_BETWEEN_REQUESTS = "YANDEX_MUSIC_PERIOD_BETWEEN_REQUESTS"
_logger = logging.getLogger("yandex_client")

_SINGLETON: Optional[Client] = None

def _album_genres_to_list(album) -> List[str]:
    """Extract genre names from album.genre into a list of strings."""
    if album is None:
        return []
    raw = getattr(album, "genre", None)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [
            getattr(g, "name", g) if not isinstance(g, str) else g
            for g in raw
        ]
    return [str(raw)] if raw else []


def _get_client() -> Client:
    global _SINGLETON
    if _SINGLETON is None:
        token = os.getenv(_YM_TOKEN)
        if not token:
            raise RuntimeError(
                f"Environment variable '{_YM_TOKEN}' is not set. "
                "Set it to a valid Yandex Music access token. "
                "See https://yandex-music.readthedocs.io/en/main/token.html for details."
            )
        _SINGLETON = Client(token).init()
    return _SINGLETON


def _build_metadata(track: Track) -> TrackMetadata:
    album = track.albums[0] if track.albums else None
    track_position = getattr(track, "track_position", None)

    return TrackMetadata(
        track_id=str(getattr(track, "id", "")),
        title=track.title,
        artists=[a.name for a in track.artists] if track.artists else [],
        album=album.title if album else None,
        album_artists=[a.name for a in album.artists] if album and album.artists else [],
        year=getattr(album, "year", None) if album else None,
        track_number=getattr(track_position, "index", None) if track_position else None,
        disc_number=getattr(track_position, "volume", None) if track_position else None,
        duration_ms=getattr(track, "duration_ms", None),
        cover_uri=getattr(track, "cover_uri", None) or (
            getattr(album, "cover_uri", None) if album else None
        ),
        genres=_album_genres_to_list(album),
    )


def fetch_liked_tracks(
    cache_path: Optional[Path] = None, limit: Optional[int] = None
) -> list[TrackMetadata]:
    if cache_path is not None and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        result = []
        for item in data:
            # Backward compat: old cache had "genre" (str), now we use "genres" (list)
            if "genres" not in item and "genre" in item:
                item = dict(item)
                item["genres"] = [item["genre"]] if item.get("genre") else []
                del item["genre"]
            result.append(TrackMetadata(**item))
        return result[:limit] if limit is not None else result

    client = _get_client()
    likes = client.users_likes_tracks()
    result: list[TrackMetadata] = []
    _logger.info(f"Found {len(likes)} liked tracks.")

    for liked in likes:
        if limit is not None and len(result) >= limit:
            break
        full_track = _fetch_track_with_retry(liked)
        result.append(_build_metadata(full_track))
        _logger.info(f"Built metadata for {full_track.title}")
        max_rnd = int(os.getenv(_YM_PERIOD_BETWEEN_REQUESTS))
        time.sleep(randint(1, max_rnd))  # To prevent rate-limiters

    if cache_path is not None and limit is None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps([asdict(t) for t in result], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return result


def _fetch_track_with_retry(liked) -> Track:
    """Fetch full track with infinite retry on NetworkError (5 min delay)."""
    while True:
        try:
            return liked.fetch_track()
        except NetworkError as e:
            _logger.warning(
                "NetworkError fetching track, retrying in %s minutes: %s",
                _RETRY_DELAY_SECONDS // 60,
                e,
            )
            time.sleep(_RETRY_DELAY_SECONDS)


def fetch_failed_track_metadata(track_id: str) -> TrackMetadata:
    client = _get_client()
    while True:
        try:
            tr = client.tracks([track_id])[0]
            return _build_metadata(tr)
        except NetworkError as e:
            _logger.warning(
                "NetworkError fetching track %s, retrying in %s minutes: %s",
                track_id,
                _RETRY_DELAY_SECONDS // 60,
                e,
            )
            time.sleep(_RETRY_DELAY_SECONDS)
        except Exception:
            break

    likes = client.users_likes_tracks()
    for liked in likes:
        full_track = _fetch_track_with_retry(liked)
        real_id = getattr(full_track, "real_id", getattr(full_track, "id", None))
        if str(real_id) == str(track_id):
            return _build_metadata(full_track)
    raise RuntimeError(f"Could not resolve failed track metadata for id={track_id!r}")


def _get_download_dir() -> Path:
    dir_env = os.getenv(_YM_DOWNLOAD_ENV)
    download_dir = Path(dir_env)
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def _best_download_info(track: Track):
    """Choose best DownloadInfo: prefer flac, then highest bitrate mp3."""
    infos = track.get_download_info()
    if not infos:
        raise DownloadError("No download info for track")
    # Prefer flac, then mp3 by bitrate descending
    def key(d):
        return (0 if (getattr(d, "codec", "") or "").lower() == "flac" else 1, -(getattr(d, "bitrate_in_kbps", 0) or 0))
    return max(infos, key=key)


def download_track(
    track: TrackMetadata,
    max_retries: int,
) -> Tuple[Path, str]:
    """Download a single track from Yandex Music. Returns (path, extension)."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            client = _get_client()
            tracks = client.tracks([track.track_id])
            if not tracks:
                raise DownloadError(f"Track not found: {track.track_id}")
            ym_track = tracks[0]
            info = _best_download_info(ym_track)
            codec = (getattr(info, "codec", None) or "mp3").lower()
            download_dir = _get_download_dir()
            path = download_dir / f"{track.track_id}.{codec}"
            info.download(str(path))
            if not path.exists():
                raise DownloadError(f"Download finished but file missing: {path}")
            return path, codec
        except NetworkError as e:
            last_error = e
            _logger.warning("NetworkError downloading track, retry %s/%s: %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(_RETRY_DELAY_SECONDS)
        except Exception as e:
            last_error = e
            _logger.warning("Error downloading track, retry %s/%s: %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(_RETRY_DELAY_SECONDS)
    raise DownloadError(str(last_error) if last_error else "Yandex Music download failed")