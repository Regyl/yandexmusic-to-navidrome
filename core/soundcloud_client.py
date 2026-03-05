"""Fetch SoundCloud playlist track list and build TrackMetadata using yt-dlp."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

import yt_dlp

from core.yandex_client import TrackMetadata

_logger = logging.getLogger("soundcloud_client")

# Prefix to avoid collision with Yandex track_id in migration DB
_TRACK_ID_PREFIX = "sc_"


@dataclass
class SoundCloudTrack:
    metadata: TrackMetadata
    url: str


def _normalize_thumbnail(thumb: str | None) -> str | None:
    """Return a usable cover URL; SoundCloud often gives small thumbs."""
    if not thumb or not isinstance(thumb, str):
        return None
    if not thumb.startswith("http"):
        return f"https://{thumb}" if thumb else None
    return thumb


def _entry_to_metadata(entry: dict, index: int) -> TrackMetadata:
    """Build TrackMetadata from a single track entry. Uses track's source album only, not playlist title."""
    eid = entry.get("id") or entry.get("url", "")
    track_id = f"{_TRACK_ID_PREFIX}{eid}"
    title = entry.get("title") or "Unknown Title"
    uploader = entry.get("uploader") or entry.get("creator") or "Unknown Artist"
    artists = [uploader] if isinstance(uploader, str) else list(uploader)[:1] if uploader else ["Unknown Artist"]
    duration = entry.get("duration")
    duration_ms = int(duration * 1000) if duration is not None else None
    thumb = entry.get("thumbnail")
    if not thumb and entry.get("thumbnails"):
        first_thumb = entry["thumbnails"][0]
        thumb = first_thumb.get("url") if isinstance(first_thumb, dict) else None

    # Use track's source album from SoundCloud when present; never use playlist/set name.
    album = entry.get("album") or None

    return TrackMetadata(
        track_id=track_id,
        title=title,
        artists=artists,
        album=album,
        album_artists=artists,
        year=None,
        track_number=index + 1,
        disc_number=None,
        duration_ms=duration_ms,
        cover_uri=_normalize_thumbnail(thumb),
        genres=[],
    )


def fetch_playlist_tracks(playlist_url: str) -> List[SoundCloudTrack]:
    """Extract playlist/set from SoundCloud and return list of (TrackMetadata, track URL)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
    if not info:
        raise RuntimeError(f"yt-dlp returned no info for {playlist_url!r}")

    entries = info.get("entries") or []
    if not entries:
        raise RuntimeError(f"No entries in playlist: {playlist_url!r}")

    playlist_title = info.get("title") or "SoundCloud Playlist"
    result: List[SoundCloudTrack] = []
    for i, ent in enumerate(entries):
        if ent is None:
            continue
        if not isinstance(ent, dict):
            continue
        url = ent.get("url") or ent.get("webpage_url")
        if not url:
            _logger.warning("Skip entry without url: %s", ent.get("title"))
            continue
        metadata = _entry_to_metadata(ent, i)
        result.append(SoundCloudTrack(metadata=metadata, url=url))
    _logger.info("Fetched SoundCloud playlist %r: %d tracks", playlist_title, len(result))
    return result
