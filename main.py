from __future__ import annotations

import logging
import os

import typer
import uvicorn
from dotenv import load_dotenv

from cli_core import (
    _build_config,
    _get_data_dir,
    _logger,
    run_count_successful,
    run_import_soundcloud_likes,
    run_list_failed,
    run_retry_failed,
    run_sync_like_tracks,
)
from util.utils import configure_logging

app = typer.Typer(help="Migrate Yandex Music liked tracks into a Navidrome library.")


@app.command("ym-import")
def sync_command() -> None:
    """Synchronize all liked tracks from Yandex Music into Navidrome."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    run_sync_like_tracks(cfg)
    _logger.info("finished ym-import")


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


@app.command("retry-failed")
def retry_failed_command() -> None:
    """Retry previously failed downloads recorded in migration.db."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    cfg = _build_config()
    run_retry_failed(cfg)
    _logger.info("finished retry-failed")


@app.command("list-failed")
def list_failed_command() -> None:
    """List all failed-to-download tracks and their quantity."""
    data_dir = _get_data_dir()
    configure_logging(data_dir / "migration.log")
    run_list_failed(data_dir)


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
        log_level=logging.WARNING,
    )

def main() -> None:
    load_dotenv()
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
    app()


if __name__ == "__main__":
    main()