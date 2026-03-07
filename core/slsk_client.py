from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional, Tuple

from aioslsk.client import SoulSeekClient
from aioslsk.search.model import SearchRequest, SearchResult
from aioslsk.settings import CredentialsSettings, Settings, SharesSettings

from core.models.trackmetdata import TrackMetadata
from util.utils import DownloadError

_SLSK_USER_ENV = "SLSK_USERNAME"
_SLSK_PASS_ENV = "SLSK_PASSWORD"
_SLSK_DOWNLOAD_ENV = "SLSK_DOWNLOAD_DIR"

_SINGLETON: Optional[SoulSeekClient] = None


def _get_settings() -> Settings:
    username = os.getenv(_SLSK_USER_ENV)
    password = os.getenv(_SLSK_PASS_ENV)
    if not username or not password:
        raise RuntimeError(
            f"Soulseek credentials are not configured. "
            f"Set {_SLSK_USER_ENV} and {_SLSK_PASS_ENV} environment variables."
        )

    download_dir_env = os.getenv(_SLSK_DOWNLOAD_ENV)
    download_dir = Path(download_dir_env) if download_dir_env else Path.cwd() / "slsk_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        credentials=CredentialsSettings(username=username, password=password),
        shares=SharesSettings(download=str(download_dir), directories=[]),
    )


async def _search_best_result(client: SoulSeekClient, track: TrackMetadata) -> SearchResult:
    query = f"{track.title} - {', '.join(track.artists) or 'Unknown'}"
    request: SearchRequest = await client.searches.search(query)

    await asyncio.sleep(5)
    if not request.results:
        raise DownloadError(f"No Soulseek search results for query {query!r}")

    def _score(res: SearchResult) -> tuple[int, int]:
        free = 1 if res.has_free_slots else 0
        return (free, res.avg_speed or 0)

    return max(request.results, key=_score)


async def _download_once(client: SoulSeekClient, track: TrackMetadata) -> Tuple[Path, str]:
    from aioslsk.transfer.model import Transfer

    result = await _search_best_result(client, track)
    if not result.shared_items:
        raise DownloadError("Best Soulseek result has no shared items.")

    file = result.shared_items[0]
    transfer: Transfer = await client.transfers.download(result.username, file.filename)

    while not transfer.is_finalized():
        await asyncio.sleep(0.5)

    snapshot = transfer.progress_snapshot
    if snapshot.fail_reason:
        raise DownloadError(f"Soulseek transfer failed: {snapshot.fail_reason}")
    if not transfer.local_path:
        raise DownloadError("Soulseek transfer finished but local_path is missing.")

    path = Path(transfer.local_path)
    if not path.exists():
        raise DownloadError(f"Soulseek reported completed download but file is missing: {path}")

    ext = path.suffix.lstrip(".").lower() or "mp3"
    return path, ext


async def get_soulseek_client() -> SoulSeekClient:
    """Return the singleton Soulseek client, initializing it if needed."""
    global _SINGLETON
    if _SINGLETON is None:
        settings = _get_settings()
        _SINGLETON = SoulSeekClient(settings)
        await _SINGLETON.start()
        await _SINGLETON.login()
    return _SINGLETON


async def shutdown_soulseek_client() -> None:
    """Stop and clear the singleton Soulseek client."""
    global _SINGLETON
    if _SINGLETON is not None:
        await _SINGLETON.stop()
        _SINGLETON = None


async def download_track(
    client: SoulSeekClient,
    track: TrackMetadata,
    timeout_seconds: int,
    max_retries: int,
) -> Tuple[Path, str]:
    """Download a single track using the given Soulseek client."""
    last_error: Exception | None = None
    for _ in range(max_retries):
        try:
            return await asyncio.wait_for(
                _download_once(client, track),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
    raise DownloadError(str(last_error) if last_error else "Unknown download error")


def download_track_with_retries(
    track: TrackMetadata,
    timeout_seconds: int,
    max_retries: int,
) -> Tuple[Path, str]:
    """Синхронная загрузка одного трека. Каждый вызов использует asyncio.run(); клиент явно останавливается до закрытия цикла, чтобы избежать RuntimeError('Event loop is closed')."""
    async def _run_standalone() -> Tuple[Path, str]:
        client = await get_soulseek_client()
        try:
            return await download_track(client, track, timeout_seconds, max_retries)
        finally:
            await shutdown_soulseek_client()
    return asyncio.run(_run_standalone())
