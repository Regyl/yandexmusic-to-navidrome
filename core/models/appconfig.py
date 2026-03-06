from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    music_root: Path
    download_timeout_seconds: int = 600
    max_download_retries: int = 3

