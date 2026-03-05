from __future__ import annotations

from pathlib import Path
from typing import Optional

from mutagen import File
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPOS, TRCK, TPE1, TPE2, TCMP
from mutagen.mp3 import MP3

from core.yandex_client import TrackMetadata

# Navidrome preferred separator for multiple artists when using single-valued tag
_ARTIST_SEP = " / "


def _is_compilation(track: TrackMetadata) -> bool:
    """True if track is part of a compilation (Various Artists) per Navidrome."""
    if not track.album_artists:
        return False
    if len(track.album_artists) > 1:
        return True
    return track.album_artists[0].strip().lower() == "various artists"


def _album_artist_display(track: TrackMetadata) -> str:
    """Album Artist for display; fallback to first track artist if no album artists."""
    if track.album_artists:
        return _ARTIST_SEP.join(track.album_artists)
    if track.artists:
        return _ARTIST_SEP.join(track.artists)
    return ""


def _embed_mp3_tags(path: Path, track: TrackMetadata, cover_bytes: Optional[bytes]) -> None:
    audio = MP3(path, ID3=ID3)
    if audio.tags is None:
        audio.add_tags()

    audio.tags["TIT2"] = TIT2(encoding=3, text=track.title)
    if track.artists:
        # Multi-valued TPE1 (ID3v2.4) preferred by Navidrome; list = separate artists
        audio.tags["TPE1"] = TPE1(encoding=3, text=track.artists)
    if track.album:
        audio.tags["TALB"] = TALB(encoding=3, text=track.album)
    album_artist_list = track.album_artists or track.artists
    if album_artist_list:
        audio.tags["TPE2"] = TPE2(encoding=3, text=album_artist_list)
    if track.track_number is not None:
        audio.tags["TRCK"] = TRCK(encoding=3, text=str(track.track_number))
    if track.disc_number is not None:
        audio.tags["TPOS"] = TPOS(encoding=3, text=str(track.disc_number))
    if track.year is not None:
        audio.tags["TDRC"] = TDRC(encoding=3, text=str(track.year))
    if track.genres:
        audio.tags["TCON"] = TCON(encoding=3, text=track.genres)
    if _is_compilation(track):
        audio.tags["TCMP"] = TCMP(encoding=3, text="1")

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
        # Singular ARTIST = display name; plural ARTISTS = multi-valued (Navidrome preferred)
        audio["artist"] = _ARTIST_SEP.join(track.artists)
        audio["artists"] = track.artists
    if track.album:
        audio["album"] = track.album
    album_artist = _album_artist_display(track)
    if album_artist:
        audio["albumartist"] = album_artist
    if track.album_artists:
        audio["albumartists"] = track.album_artists
    if track.track_number is not None:
        audio["tracknumber"] = str(track.track_number)
    if track.disc_number is not None:
        audio["discnumber"] = str(track.disc_number)
    if track.year is not None:
        audio["date"] = str(track.year)
    if track.genres:
        audio["genre"] = track.genres
    if _is_compilation(track):
        audio["compilation"] = "1"

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
            audio["artist"] = _ARTIST_SEP.join(track.artists)
            audio["artists"] = track.artists
        if track.album:
            audio["album"] = track.album
        album_artist = _album_artist_display(track)
        if album_artist:
            audio["albumartist"] = album_artist
        if track.album_artists:
            audio["albumartists"] = track.album_artists
        if track.track_number is not None:
            audio["tracknumber"] = str(track.track_number)
        if track.disc_number is not None:
            audio["discnumber"] = str(track.disc_number)
        if track.year is not None:
            audio["date"] = str(track.year)
        if track.genres:
            audio["genre"] = track.genres
        if _is_compilation(track):
            audio["compilation"] = "1"
        audio.save()

