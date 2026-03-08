from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TrackMetadata:
    track_id: str
    title: str
    artists: List[str]
    album: Optional[str]
    album_artists: List[str]
    year: Optional[int]
    track_number: Optional[int]
    disc_number: Optional[int]
    duration_ms: Optional[int]
    cover_uri: Optional[str]
    genres: List[str]
    language: Optional[str] = None  # tlan, language
    mood: Optional[List[str]] = None  # tmoo, mood (split: ; / ,)
    release_country: Optional[str] = None  # releasecountry, musicbrainz/album release country
    releasetype: Optional[str] = None  # releasetype, musicbrainz_albumtype (album, ep, single, compilation)
    style: Optional[str] = None  # grouping or style (album style)
    source: Optional[str] = None  # TXXX:source - where track was sourced from