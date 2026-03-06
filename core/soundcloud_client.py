"""Fetch SoundCloud playlist track list and build TrackMetadata using yt-dlp."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse

import yt_dlp

from core.yandex_client import TrackMetadata

_logger = logging.getLogger("soundcloud_client")

# Prefix to avoid collision with Yandex track_id in migration DB
_TRACK_ID_PREFIX = "sc_"

def _canonical_username(username: str) -> str:
    """Extract a single SoundCloud username (one path segment). Accepts username or profile URL."""
    s = (username or "").strip()
    if not s:
        raise ValueError("SoundCloud username is required (e.g. your profile name from the URL)")
    if "soundcloud.com" in s:
        parsed = urlparse(s if s.startswith("http") else "https://" + s)
        parts = [p for p in parsed.path.strip("/").split("/") if p and p not in ("likes", "sets")]
        if not parts:
            raise ValueError("Could not parse username from URL; use your SoundCloud username")
        s = parts[0]
    s = s.split("/")[0].split("?")[0]
    if not s:
        raise ValueError("SoundCloud username is required")
    return s


# Base URL pattern for a user's likes (use actual username; "you" does not work with yt-dlp and returns 404)
def _likes_url_for_username(username: str) -> str:
    """Build soundcloud.com/USERNAME/likes URL."""
    uname = _canonical_username(username)
    return f"https://soundcloud.com/{uname}/likes"


def _sets_url_for_username(username: str) -> str:
    """Build soundcloud.com/USERNAME/sets URL (user's playlists page)."""
    uname = _canonical_username(username)
    return f"https://soundcloud.com/{uname}/sets"


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


def _build_ydl_opts() -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    return opts


def fetch_playlist_tracks(playlist_url: str) -> List[SoundCloudTrack]:
    """Extract playlist/set from SoundCloud and return list of (TrackMetadata, track URL)."""
    opts = _build_ydl_opts()
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


def fetch_liked_tracks(username: str) -> List[SoundCloudTrack]:
    """Fetch liked tracks for a SoundCloud user (https://soundcloud.com/USERNAME/likes).
    Use your actual username; 'you' does not work with yt-dlp (404). Set SOUNDCLOUD_COOKIES_FILE for private likes."""
    url = _likes_url_for_username(username)
    return fetch_playlist_tracks(url)


def _fetch_tracks_from_user_playlists(username: str) -> List[SoundCloudTrack]:
    """Fetch all tracks from all playlists of a user (soundcloud.com/USERNAME/sets). Deduplicates by track URL."""
    sets_url = _sets_url_for_username(username)
    opts = _build_ydl_opts()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(sets_url, download=False)
    if not info:
        _logger.warning("yt-dlp returned no info for user sets %r", sets_url)
        return []

    entries = info.get("entries") or []
    playlist_urls: List[str] = []
    for ent in entries:
        if ent is None or not isinstance(ent, dict):
            continue
        pl_url = ent.get("url") or ent.get("webpage_url")
        if pl_url:
            playlist_urls.append(pl_url)

    if not playlist_urls:
        _logger.info("No playlists found for user sets %r", sets_url)
        return []

    seen_urls: set[str] = set()
    result: List[SoundCloudTrack] = []
    for pl_url in playlist_urls:
        try:
            tracks = fetch_playlist_tracks(pl_url)
            for sc_track in tracks:
                if sc_track.url not in seen_urls:
                    seen_urls.add(sc_track.url)
                    result.append(sc_track)
        except Exception as e:
            _logger.warning("Skip playlist %r: %s", pl_url, e)

    _logger.info("Fetched user playlists from %r: %d playlists, %d unique tracks", sets_url, len(playlist_urls), len(result))
    return result


def fetch_all_tracks_for_user(username: str) -> List[SoundCloudTrack]:
    """Fetch liked tracks and all tracks from user's playlists; merge and deduplicate by track URL."""
    uname = _canonical_username(username)
    seen_urls: set[str] = set()
    result: List[SoundCloudTrack] = []

    # Liked tracks
    try:
        for sc_track in fetch_liked_tracks(uname):
            if sc_track.url not in seen_urls:
                seen_urls.add(sc_track.url)
                result.append(sc_track)
    except Exception as e:
        _logger.warning("Could not fetch likes for %r: %s", uname, e)

    # Tracks from user's playlists
    for sc_track in _fetch_tracks_from_user_playlists(uname):
        if sc_track.url not in seen_urls:
            seen_urls.add(sc_track.url)
            result.append(sc_track)

    _logger.info("Total tracks for user %r: %d (likes + playlists, deduplicated)", uname, len(result))
    return result
