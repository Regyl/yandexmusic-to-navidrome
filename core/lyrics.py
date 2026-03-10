from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from core.models.trackmetdata import TrackMetadata

_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_NETEASE_SEARCH_URL = "https://music.163.com/api/search/get"
_NETEASE_LYRIC_URL = "https://music.163.com/api/song/lyric"
_logger = logging.getLogger("navidrome_rw.lyrics")

_NETEASE_SESSION: Optional[requests.Session] = None


def _get_netease_session() -> requests.Session:
    global _NETEASE_SESSION
    if _NETEASE_SESSION is None:
        _NETEASE_SESSION = requests.Session()
        _NETEASE_SESSION.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com/",
        })
    return _NETEASE_SESSION


def _fetch_best_lrclib_entry(track: TrackMetadata) -> Optional[dict]:
    params = {
        "track_name": track.title,
        "artist_name": ", ".join(track.artists) if track.artists else "",
    }
    if track.album:
        params["album_name"] = track.album
    if track.duration_ms:
        params["duration"] = track.duration_ms / 1000.0

    try:
        resp = requests.get(_LRCLIB_SEARCH_URL, params=params, timeout=15)
        if resp.status_code != 200:
            _logger.debug("LRCLIB request failed", extra={"status": resp.status_code})
            return None
        data = resp.json()
    except Exception:
        return None

    if not isinstance(data, list) or not data:
        return None

    def _score(item: dict) -> tuple[int, float]:
        has_synced = 1 if item.get("syncedLyrics") else 0
        duration = float(item.get("duration") or 0.0)
        target = float(track.duration_ms or 0) / 1000.0
        return has_synced, -abs(duration - target)

    best = max(data, key=_score)
    if not best.get("syncedLyrics"):
        return None
    return best


def _fetch_best_netease_lrc(track: TrackMetadata) -> Optional[str]:
    """Fetch synced lyrics from NetEase Cloud Music as fallback. Returns LRC string or None."""
    query = f"{track.title} {' '.join(track.artists) if track.artists else ''}".strip()
    if not query:
        return None

    session = _get_netease_session()
    try:
        resp = session.post(
            _NETEASE_SEARCH_URL,
            data={"s": query, "type": 1, "offset": 0, "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            _logger.debug("NetEase search failed", extra={"status": resp.status_code})
            return None
        data = resp.json()
    except Exception:
        return None

    result = data.get("result") if isinstance(data, dict) else None
    songs = result.get("songs") if isinstance(result, dict) else []
    if not isinstance(songs, list) or not songs:
        return None

    target_duration_ms = float(track.duration_ms or 0)
    best = min(
        songs,
        key=lambda s: abs(float(s.get("duration", 0) or 0) - target_duration_ms)
        if target_duration_ms else 0,
    )
    song_id = best.get("id")
    if song_id is None:
        return None

    try:
        lyric_resp = session.get(
            _NETEASE_LYRIC_URL,
            params={"id": song_id, "lv": 1, "kv": 1, "tv": -1},
            timeout=15,
        )
        if lyric_resp.status_code != 200:
            _logger.debug("NetEase lyric fetch failed", extra={"status": lyric_resp.status_code})
            return None
        lyric_data = lyric_resp.json()
    except Exception:
        return None

    lrc = lyric_data.get("lrc") if isinstance(lyric_data, dict) else None
    if not lrc or not isinstance(lrc, dict):
        return None
    synced = lrc.get("lyric")
    if not isinstance(synced, str) or not synced.strip():
        return None
    return synced.strip()


def generate_lrc_for_track(audio_path: Path, track: TrackMetadata) -> None:
    lrc_path = audio_path.with_suffix(".lrc")
    if lrc_path.exists():
        return

    synced: Optional[str] = None
    source: str = ""
    entry = _fetch_best_lrclib_entry(track)
    if entry:
        s = entry.get("syncedLyrics")
        if isinstance(s, str) and s.strip():
            synced = s
            source = "lrclib"
    if not synced:
        synced = _fetch_best_netease_lrc(track)
        if synced:
            source = "netease"
    if not synced or not isinstance(synced, str) or not synced.strip():
        _logger.info(
            "no_lyrics_found",
            extra={"title": track.title, "artists": ", ".join(track.artists)},
        )
        return

    content = synced.strip()
    if source:
        content = f"[by:{source}]\n{content}"
    lrc_path.write_text(content + "\n", encoding="utf-8")
