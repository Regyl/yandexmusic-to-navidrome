from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from core import soundcloud_client
from core.database import MigrationDB
from core.lyrics import generate_lrc_for_track
from core.models.appconfig import AppConfig
from core.models.trackmetdata import TrackMetadata
from core.slsk_client import download_track_with_retries as download_track_soulseek
from core.tagging import embed_tags
from core.yandex_client import fetch_failed_track_metadata, fetch_liked_tracks
from core.ytdlp_client import (
    download_track as download_track_ytdlp,
    download_track_from_url as download_track_ytdlp_url,
)
from util.utils import (
    DownloadError,
    build_album_directory,
    build_track_filename,
    download_cover_image,
    ensure_directory,
)

_logger = logging.getLogger("navidrome_rw")


def _ensure_env_loaded() -> None:
    """Load environment variables from .env once, if present."""
    load_dotenv()
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")


def _get_data_dir() -> Path:
    """Return directory for migration state (db/log/cache), creating it if needed.

    If YM_NAVIDROME_DATA is set, that directory is used. Otherwise the
    Navidrome music root is used as before.
    """
    _ensure_env_loaded()
    env_dir = os.getenv("YM_NAVIDROME_DATA")
    if not env_dir:
        raise RuntimeError("YM_NAVIDROME_DATA environment variable not set")
    cache_dir = Path(env_dir)

    ensure_directory(cache_dir)
    return cache_dir


def process_single_track(
    track: TrackMetadata,
    cfg: AppConfig,
    db: MigrationDB,
    source_url: str | None = None,
) -> None:
    if db.is_successful(track.track_id):
        _logger.info(f"track_already_migrated for {track.title}")
        return

    album_dir = build_album_directory(cfg.music_root, track)
    ensure_directory(album_dir)

    audio_dest = album_dir / build_track_filename(track, extension="mp3")

    if audio_dest.exists():
        _logger.info(f"destination_exists_skip for {track.title}")
        db.mark_success(track.track_id, str(audio_dest))
        return

    try:
        if source_url:
            download_path, actual_extension = download_track_ytdlp_url(
                url=source_url,
                timeout_seconds=cfg.download_timeout_seconds,
            )
        else:
            try:
                download_path, actual_extension = download_track_ytdlp(
                    track=track,
                    timeout_seconds=cfg.download_timeout_seconds,
                )
            except DownloadError as e:
                _logger.warning("download_error from yt-dlp: " + str(e))
                if "The current session has been rate-limited by YouTube" in str(e):
                    raise Exception(
                        "The current session has been rate-limited by YouTube. Retry after an hour"
                    )
                download_path, actual_extension = download_track_soulseek(
                    track=track,
                    timeout_seconds=cfg.download_timeout_seconds,
                    max_retries=cfg.max_download_retries,
                )
    except (DownloadError, RuntimeError) as exc:
        _logger.error(f"download_failed: {str(exc)}")
        db.mark_failed(track.track_id, str(exc))
        return
    _logger.info(f"download_successful for {track.title}")

    final_audio_dest = album_dir / build_track_filename(
        track, extension=actual_extension
    )

    ensure_directory(final_audio_dest.parent)
    download_path.replace(final_audio_dest)

    cover_bytes = download_cover_image(track)

    cover_path = album_dir / "album-cover.jpg"
    if cover_bytes and not cover_path.exists():
        cover_path.write_bytes(cover_bytes)

    try:
        embed_tags(final_audio_dest, track, cover_bytes)
    except Exception:
        _logger.warning(f"tagging_failed for {track.title}")

    try:
        generate_lrc_for_track(final_audio_dest, track)
    except Exception:
        _logger.warning(f"lyrics_failed for {track.title}")

    db.mark_success(track.track_id, str(final_audio_dest))


def run_sync_like_tracks(cfg: AppConfig) -> None:
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        liked_tracks = fetch_liked_tracks(
            cache_path=data_dir / "migration_liked_tracks.json"
        )
        _logger.info(f"fetched_yandex_liked_tracks: {len(liked_tracks)}")

        for track in liked_tracks:
            process_single_track(track, cfg, db)


def run_retry_failed(cfg: AppConfig) -> None:
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        failed_ids = db.get_failed_track_ids()
        if not failed_ids:
            _logger.info("no_failed_tracks_to_retry")
            return

        _logger.info(f"retrying_failed_tracks: {len(failed_ids)}")

        for track_id in failed_ids:
            track = fetch_failed_track_metadata(track_id)
            process_single_track(track, cfg, db)


def _build_config() -> AppConfig:
    _ensure_env_loaded()
    folder = os.getenv("NAVIDROME_FOLDER")
    if not folder:
        raise RuntimeError("NAVIDROME_FOLDER environment variable not set")
    return AppConfig(music_root=Path(folder))


def run_import_soundcloud_likes(username: str, cfg: AppConfig) -> None:
    """Fetch SoundCloud liked tracks and all user playlists for the given username, then download each into NAVIDROME_FOLDER."""
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        tracks = soundcloud_client.fetch_all_tracks_for_user(username)
        _logger.info(f"fetched_soundcloud_likes_and_playlists: {len(tracks)}")
        for sc_track in tracks:
            process_single_track(
                sc_track.metadata,
                cfg,
                db,
                source_url=sc_track.url,
            )


def run_list_failed(cache_dir: Path) -> None:
    with MigrationDB(cache_dir / "migration.db") as db:
        failed = db.get_failed_tracks()
        count = len(failed)
        if count == 0:
            _logger.info("no_failed_tracks")
            return
        _logger.info("failed_tracks_count", extra={"count": count})
        for track_id, error in failed:
            _logger.info(
                "failed_track",
                extra={"track_id": track_id, "error": error},
            )


def run_count_successful(data_dir: Path) -> None:
    with MigrationDB(data_dir / "migration.db") as db:
        count = db.get_successful_count()
        _logger.info("successful_downloads_count")
        # Keep printing to stdout for the CLI user
        from typer import echo

        echo(f"Successfully downloaded tracks: {count}")

