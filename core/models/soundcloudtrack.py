from __future__ import annotations

from dataclasses import dataclass

from core.models.trackmetdata import TrackMetadata


@dataclass
class SoundCloudTrack:
    metadata: TrackMetadata
    url: str