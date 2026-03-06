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