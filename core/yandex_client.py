from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from random import randint
from typing import List, Optional

from yandex_music import Client, Track

from core.models.trackmetdata import TrackMetadata

_YM_TOKEN = "YANDEX_MUSIC_TOKEN"
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


def fetch_liked_tracks(cache_path: Optional[Path] = None) -> list[TrackMetadata]:
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
        return result

    client = _get_client()
    likes = client.users_likes_tracks()
    result: list[TrackMetadata] = []
    _logger.info(f"Found {len(likes)} liked tracks.")

    for liked in likes:
        full_track = liked.fetch_track()
        result.append(_build_metadata(full_track))
        _logger.info(f"Built metadata for {full_track.title}")
        max_rnd = int(os.getenv(_YM_PERIOD_BETWEEN_REQUESTS))
        time.sleep(randint(1, max_rnd)) # To prevent rate-limiters


    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps([asdict(t) for t in result], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return result


def fetch_failed_track_metadata(track_id: str) -> TrackMetadata:
    client = _get_client()
    try:
        tr = client.tracks([track_id])[0]
        return _build_metadata(tr)
    except Exception:
        likes = client.users_likes_tracks()
        for liked in likes:
            full_track = liked.fetch_track()
            real_id = getattr(full_track, "real_id", getattr(full_track, "id", None))
            if str(real_id) == str(track_id):
                return _build_metadata(full_track)
        raise RuntimeError(f"Could not resolve failed track metadata for id={track_id!r}")
