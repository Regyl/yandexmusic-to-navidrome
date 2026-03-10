"""Navidrome/Subsonic API client for fetching playlists."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import requests

_logger = logging.getLogger("navidrome_client")

_NAVIDROME_URL = "NAVIDROME_URL"
_NAVIDROME_USER = "NAVIDROME_USER"
_NAVIDROME_PASSWORD = "NAVIDROME_PASSWORD"

@dataclass
class PlaylistTrack:
    """A track entry from a Navidrome playlist (Subsonic getPlaylist)."""

    id: str
    title: str
    artist: str
    album: str
    path: str  # Relative to music folder
    duration: Optional[int] = None


@dataclass
class Playlist:
    """A Navidrome playlist with its tracks."""

    id: str
    name: str
    owner: str
    song_count: int
    duration: int
    entries: List[PlaylistTrack]


def _get_base_url() -> str:
    url = os.getenv(_NAVIDROME_URL)
    if not url:
        raise RuntimeError(
            f"Environment variable {_NAVIDROME_URL!r} is not set. "
            "Set it to your Navidrome server URL (e.g. https://navidrome.example.com)."
        )
    return url.rstrip("/")


def _get_auth_params() -> dict:
    user = os.getenv(_NAVIDROME_USER)
    password = os.getenv(_NAVIDROME_PASSWORD)
    if not user or not password:
        raise RuntimeError(
            f"Environment variables {_NAVIDROME_USER!r} and {_NAVIDROME_PASSWORD!r}"
            "are required for Navidrome API authentication."
        )
    return {"u": user, "p": password}


def _parse_playlist_entry(entry: dict) -> Optional[PlaylistTrack]:
    """Parse a single entry from getPlaylist response."""
    path = entry.get("path")
    if not path:
        return None
    return PlaylistTrack(
        id=str(entry.get("id", "")),
        title=entry.get("title") or "Unknown",
        artist=entry.get("artist") or "Unknown Artist",
        album=entry.get("album") or "",
        path=path,
        duration=entry.get("duration"),
    )


def fetch_playlists() -> List[dict]:
    """Fetch all playlists. Returns raw playlist objects with id, name, etc."""
    base_url = _get_base_url()
    params = {"v": "1.16.1", "c": "navidrome_rw", "f": "json", **_get_auth_params()}
    resp = requests.get(
        f"{base_url}/rest/getPlaylists.view",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    sub = data.get("subsonic-response", {})
    if sub.get("status") != "ok":
        err = sub.get("error", {})
        raise RuntimeError(
            f"Navidrome API error {err.get('code', 'unknown')}: {err.get('message', 'Unknown error')}"
        )
    playlists = sub.get("playlists", {})
    items = playlists.get("playlist")
    if items is None:
        items = playlists if isinstance(playlists, list) else []
    if not isinstance(items, list):
        items = [items] if items else []
    return items


def fetch_playlist(playlist_id: str) -> Playlist:
    """Fetch a single playlist with all track entries."""
    base_url = _get_base_url()
    params = {
        "v": "1.16.1",
        "c": "navidrome_rw",
        "f": "json",
        "id": playlist_id,
        **_get_auth_params(),
    }
    resp = requests.get(
        f"{base_url}/rest/getPlaylist.view",
        params=params,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    sub = data.get("subsonic-response", {})
    if sub.get("status") != "ok":
        err = sub.get("error", {})
        raise RuntimeError(
            f"Navidrome API error {err.get('code', 'unknown')}: {err.get('message', 'Unknown error')}"
        )
    pl = sub.get("playlist", {})
    entries_raw = pl.get("entry")
    if entries_raw is None:
        entries_raw = []
    if not isinstance(entries_raw, list):
        entries_raw = [entries_raw] if entries_raw else []
    entries: List[PlaylistTrack] = []
    for e in entries_raw:
        if isinstance(e, dict):
            t = _parse_playlist_entry(e)
            if t:
                entries.append(t)
    return Playlist(
        id=str(pl.get("id", "")),
        name=pl.get("name", ""),
        owner=pl.get("owner", ""),
        song_count=int(pl.get("songCount", 0)),
        duration=int(pl.get("duration", 0)),
        entries=entries,
    )


def get_playlist_by_name(name: str) -> Optional[Playlist]:
    """Find a playlist by exact name and return it with tracks. Returns None if not found."""
    playlists = fetch_playlists()
    for pl in playlists:
        if isinstance(pl, dict) and pl.get("name") == name:
            return fetch_playlist(str(pl.get("id", "")))
    return None
