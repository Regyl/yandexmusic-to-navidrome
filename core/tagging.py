from __future__ import annotations

from pathlib import Path
from typing import Optional

from mutagen import File
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, TPE1, TALB, TRCK, TDRC, TCON
from mutagen.mp3 import MP3

from core.yandex_client import TrackMetadata


def _embed_mp3_tags(path: Path, track: TrackMetadata, cover_bytes: Optional[bytes]) -> None:
    audio = MP3(path, ID3=ID3)
    if audio.tags is None:
        audio.add_tags()

    audio.tags["TIT2"] = TIT2(encoding=3, text=track.title)
    if track.artists:
        audio.tags["TPE1"] = TPE1(encoding=3, text=", ".join(track.artists))
    if track.album:
        audio.tags["TALB"] = TALB(encoding=3, text=track.album)
    if track.track_number:
        audio.tags["TRCK"] = TRCK(encoding=3, text=str(track.track_number))
    if track.year:
        audio.tags["TDRC"] = TDRC(encoding=3, text=str(track.year))
    if track.genres:
        audio.tags["TCON"] = TCON(encoding=3, text=track.genres)

    if cover_bytes:
        audio.tags["APIC"] = APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=cover_bytes,
        )

    audio.save()


def _embed_flac_tags(path: Path, track: TrackMetadata, cover_bytes: Optional[bytes]) -> None:
    audio = FLAC(path)
    audio["title"] = track.title
    if track.artists:
        audio["artist"] = ", ".join(track.artists)
    if track.album:
        audio["album"] = track.album
    if track.track_number:
        audio["tracknumber"] = str(track.track_number)
    if track.year:
        audio["date"] = str(track.year)
    if track.genres:
        audio["genre"] = track.genres

    if cover_bytes:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover_bytes
        audio.clear_pictures()
        audio.add_picture(pic)

    audio.save()


def embed_tags(path: Path, track: TrackMetadata, cover_bytes: Optional[bytes]) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _embed_mp3_tags(path, track, cover_bytes)
    elif suffix == ".flac":
        _embed_flac_tags(path, track, cover_bytes)
    else:
        # For other formats, fall back to mutagen's generic tagging if possible.
        audio = File(path)
        if audio is None:
            return
        audio["title"] = track.title
        if track.artists:
            audio["artist"] = ", ".join(track.artists)
        if track.album:
            audio["album"] = track.album
        if track.track_number:
            audio["tracknumber"] = str(track.track_number)
        if track.year:
            audio["date"] = str(track.year)
        if track.genres:
            audio["genre"] = track.genres
        audio.save()

