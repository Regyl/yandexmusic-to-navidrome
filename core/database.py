from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import List, Tuple

FailedTrack = Tuple[str, str]  # (track_id, error)


class MigrationDB:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "MigrationDB":
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migrations (
                track_id   TEXT PRIMARY KEY,
                status     TEXT NOT NULL,
                dest_path  TEXT,
                error      TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("MigrationDB is not initialized, use it as a context manager.")
        return self._conn

    def is_successful(self, track_id: str) -> bool:
        cursor = self._connection.execute(
            "SELECT status FROM migrations WHERE track_id = ?", (track_id,)
        )
        row = cursor.fetchone()
        return bool(row and row[0] == "success")

    def mark_success(self, track_id: str, dest_path: str) -> None:
        now = _dt.datetime.utcnow().isoformat()
        self._connection.execute(
            """
            INSERT INTO migrations (track_id, status, dest_path, error, updated_at)
            VALUES (?, 'success', ?, NULL, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                status = 'success',
                dest_path = excluded.dest_path,
                error = NULL,
                updated_at = excluded.updated_at
            """,
            (track_id, dest_path, now),
        )
        self._connection.commit()

    def mark_failed(self, track_id: str, error: str) -> None:
        now = _dt.datetime.utcnow().isoformat()
        self._connection.execute(
            """
            INSERT INTO migrations (track_id, status, dest_path, error, updated_at)
            VALUES (?, 'failed', NULL, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                status = 'failed',
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (track_id, error, now),
        )
        self._connection.commit()

    def get_failed_track_ids(self) -> List[str]:
        cursor = self._connection.execute(
            "SELECT track_id FROM migrations WHERE status = 'failed'"
        )
        rows = cursor.fetchall()
        return [row[0] for row in rows]

    def get_failed_tracks(self) -> List[FailedTrack]:
        """Return all failed tracks as (track_id, error) pairs."""
        cursor = self._connection.execute(
            "SELECT track_id, error FROM migrations WHERE status = 'failed' ORDER BY track_id"
        )
        rows = cursor.fetchall()
        return [(row[0], row[1] or "") for row in rows]

    def get_successful_count(self) -> int:
        """Return the number of successfully downloaded tracks."""
        cursor = self._connection.execute(
            "SELECT COUNT(*) FROM migrations WHERE status = 'success'"
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_failed_count(self) -> int:
        """Return the number of failed tracks."""
        cursor = self._connection.execute(
            "SELECT COUNT(*) FROM migrations WHERE status = 'failed'"
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_total_count(self) -> int:
        """Return the total number of tracks in migrations (success + failed)."""
        cursor = self._connection.execute("SELECT COUNT(*) FROM migrations")
        row = cursor.fetchone()
        return row[0] if row else 0
