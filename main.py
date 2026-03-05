from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import typer
from dotenv import load_dotenv

from core.database import MigrationDB
from core.lyrics import generate_lrc_for_track
from core.slsk_client import download_track_with_retries as download_track_soulseek
from core.tagging import embed_tags
from core.yandex_client import TrackMetadata, fetch_failed_track_metadata, fetch_liked_tracks
from core.ytdlp_client import download_track as download_track_ytdlp, download_track_from_url as download_track_ytdlp_url
from core.soundcloud_client import fetch_playlist_tracks
from util.utils import (
    DownloadError,
    build_album_directory,
    build_track_filename,
    configure_logging,
    download_cover_image,
    ensure_directory,
)

app = typer.Typer(help="Migrate Yandex Music liked tracks into a Navidrome library.")
_logger = logging.getLogger("yandexmusic_to_navidrome")

@dataclass
class AppConfig:
    music_root: Path
    download_timeout_seconds: int = 600
    max_download_retries: int = 3


def _get_data_dir() -> Path:
    """Return directory for migration state (db/log/cache), creating it if needed.

    If YM_NAVIDROME_DATA is set, that directory is used. Otherwise the
    Navidrome music root is used as before.
    """
    env_dir = os.getenv("YM_NAVIDROME_DATA")
    if not env_dir:
        raise RuntimeError(f"YM_NAVIDROME_DATA environment variable not set")
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
        _logger.info(
            "track_already_migrated",
            extra={"track_id": track.track_id, "title": track.title},
        )
        return

    album_dir = build_album_directory(cfg.music_root, track)
    ensure_directory(album_dir)

    audio_dest = album_dir / build_track_filename(track, extension="mp3")

    if audio_dest.exists():
        _logger.info(
            "destination_exists_skip",
            extra={"track_id": track.track_id, "path": str(audio_dest)},
        )
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
                _logger.warning("download_error from yt-dlp: " + str(e), extra={"track_id": track.track_id})
                if "The current session has been rate-limited by YouTube" in str(e):
                    exit(1)
                download_path, actual_extension = download_track_soulseek(
                    track=track,
                    timeout_seconds=cfg.download_timeout_seconds,
                    max_retries=cfg.max_download_retries,
                )
    except (DownloadError, RuntimeError) as exc:
        _logger.error(
            "download_failed: " + str(exc),
            extra={"track_id": track.track_id, "error": str(exc)},
        )
        db.mark_failed(track.track_id, str(exc))
        return
    _logger.info("download_successful", extra={"title": track.title})

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
    except Exception as exc:
        _logger.error(
            "tagging_failed",
            extra={
                "track_id": track.track_id,
                "path": str(final_audio_dest),
                "error": str(exc),
            },
        )

    try:
        generate_lrc_for_track(final_audio_dest, track)
    except Exception as exc:
        _logger.warning(
            "lyrics_failed",
            extra={"track_id": track.track_id, "error": str(exc)},
        )

    db.mark_success(track.track_id, str(final_audio_dest))


def run_sync_like_tracks(cfg: AppConfig) -> None:
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        liked_tracks = fetch_liked_tracks(
            cache_path=data_dir / "migration_liked_tracks.json"
        )
        _logger.info(
            "fetched_yandex_liked_tracks",
            extra={"count": len(liked_tracks)},
        )

        for track in liked_tracks:
            process_single_track(track, cfg, db)


def run_retry_failed(cfg: AppConfig) -> None:
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        failed_ids = db.get_failed_track_ids()
        if not failed_ids:
            _logger.info("no_failed_tracks_to_retry")
            return

        _logger.info("retrying_failed_tracks", extra={"count": len(failed_ids)})

        for track_id in failed_ids:
            track = fetch_failed_track_metadata(track_id)
            process_single_track(track, cfg, db)


def _build_config(timeout_minutes: int) -> AppConfig:
    folder = os.getenv("NAVIDROME_FOLDER")
    if not folder:
        raise RuntimeError("NAVIDROME_FOLDER environment variable not set")
    return AppConfig(
        music_root=Path(folder),
        download_timeout_seconds=timeout_minutes * 60,
    )


@app.command("ym-import")
def sync_command(
        timeout_minutes: int = typer.Option(
        10,
        "--timeout-minutes",
        min=1,
        help="Per-track download timeout in minutes.",
    ),
) -> None:
    """Synchronize all liked tracks from Yandex Music into Navidrome."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config(timeout_minutes)
    run_sync_like_tracks(cfg)


def run_import_soundcloud_playlist(playlist_url: str, cfg: AppConfig) -> None:
    """Fetch SoundCloud playlist and download each track into NAVIDROME_FOLDER."""
    data_dir = _get_data_dir()
    with MigrationDB(data_dir / "migration.db") as db:
        tracks = fetch_playlist_tracks(playlist_url)
        _logger.info(
            "fetched_soundcloud_playlist",
            extra={"url": playlist_url, "count": len(tracks)},
        )
        for sc_track in tracks:
            process_single_track(
                sc_track.metadata,
                cfg,
                db,
                source_url=sc_track.url,
            )


@app.command("soundcloud-import")
def import_soundcloud_command(
    playlist_url: str = typer.Argument(
        ...,
        help="SoundCloud playlist/set URL, e.g. https://soundcloud.com/user/sets/playlist-name",
    ),
    timeout_minutes: int = typer.Option(
        10,
        "--timeout-minutes",
        min=1,
        help="Per-track download timeout in minutes.",
    ),
) -> None:
    """Import a SoundCloud playlist: download all tracks into NAVIDROME_FOLDER."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config(timeout_minutes)
    run_import_soundcloud_playlist(playlist_url, cfg)

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
        # Also print to stdout for easy inspection
        typer.echo(f"Failed downloads: {count}")
        for track_id, error in failed:
            typer.echo(f"  {track_id}: {error}")


@app.command("retry-failed")
def retry_failed_command(
        timeout_minutes: int = typer.Option(
        10,
        "--timeout-minutes",
        min=1,
        help="Per-track download timeout in minutes.",
    ),
) -> None:
    """Retry previously failed downloads recorded in migration.db."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config(timeout_minutes)
    run_retry_failed(cfg)


@app.command("list-failed")
def list_failed_command(
) -> None:
    """List all failed-to-download tracks and their quantity."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    run_list_failed(data_dir)


def run_count_successful(cache_dir: Path) -> None:
    with MigrationDB(cache_dir / "migration.db") as db:
        count = db.get_successful_count()
        _logger.info("successful_downloads_count", extra={"count": count})
        typer.echo(f"Successfully downloaded tracks: {count}")


@app.command("count-successful")
def count_successful_command(
) -> None:
    """Print the quantity of successfully downloaded tracks."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    run_count_successful(data_dir)


def main() -> None:
    load_dotenv()
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
    app()


if __name__ == "__main__":
    main()
