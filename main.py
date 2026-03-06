from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import typer
import uvicorn
from dotenv import load_dotenv

from core import soundcloud_client
from core.database import MigrationDB
from core.lyrics import generate_lrc_for_track
from core.slsk_client import download_track_with_retries as download_track_soulseek
from core.tagging import embed_tags
from core.yandex_client import TrackMetadata, fetch_failed_track_metadata, fetch_liked_tracks
from core.ytdlp_client import download_track as download_track_ytdlp, \
    download_track_from_url as download_track_ytdlp_url
from util.utils import (
    DownloadError,
    build_album_directory,
    build_track_filename,
    configure_logging,
    download_cover_image,
    ensure_directory,
)

app = typer.Typer(help="Migrate Yandex Music liked tracks into a Navidrome library.")
_logger = logging.getLogger("navidrome_rw")

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
                    raise Exception("The current session has been rate-limited by YouTube. Retry after an hour")
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
    folder = os.getenv("NAVIDROME_FOLDER")
    if not folder:
        raise RuntimeError("NAVIDROME_FOLDER environment variable not set")
    return AppConfig(music_root=Path(folder))


@app.command("ym-import")
def sync_command() -> None:
    """Synchronize all liked tracks from Yandex Music into Navidrome."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    run_sync_like_tracks(cfg)
    _logger.info("finished ym-import")


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


@app.command("soundcloud-import")
def import_soundcloud_likes_command(
    username: str = typer.Argument(
        ...,
        help="SoundCloud username: imports liked tracks and all tracks from your playlists.",
    ),
) -> None:
    """Import SoundCloud liked tracks and all user playlists into NAVIDROME_FOLDER."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    run_import_soundcloud_likes(username, cfg)
    _logger.info("finished soundcloud-import")

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
def retry_failed_command() -> None:
    """Retry previously failed downloads recorded in migration.db."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    run_retry_failed(cfg)
    _logger.info("finished retry-failed")


@app.command("list-failed")
def list_failed_command(
) -> None:
    """List all failed-to-download tracks and their quantity."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    run_list_failed(data_dir)


def run_count_successful(data_dir: Path) -> None:
    with MigrationDB(data_dir / "migration.db") as db:
        count = db.get_successful_count()
        _logger.info("successful_downloads_count")
        typer.echo(f"Successfully downloaded tracks: {count}")


@app.command("count-successful")
def count_successful_command() -> None:
    """Print the quantity of successfully downloaded tracks."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    run_count_successful(data_dir)


@app.command("web")
def web_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
) -> None:
    """Start the web UI server to view migration status."""
    uvicorn.run(
        "web_server:app",
        host=host,
        port=port,
        reload=False,
        log_level=logging.WARNING
    )


def main() -> None:
    load_dotenv()
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
    app()


if __name__ == "__main__":
    main()
