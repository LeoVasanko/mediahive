"""Data structures for mediahive and hivescan.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec

from .tmdb import Info, Person

# ---------------------------------------------------------------------------
# Index item types (the state stored in IndexStore, sent over WS/API)
# ---------------------------------------------------------------------------


class Torrent(msgspec.Struct, omit_defaults=True):
    """A torrent file, either for a movie or an episode."""

    title: str | None = None
    playable_file: str | None = None
    resolution: str | None = None
    quality: str | None = None
    network: str | None = None
    codec: str | None = None
    audio: str | None = None
    audio_languages: list[str] | None = None
    subtitle_languages: list[str] | None = None
    external_subtitle_languages: list[str] | None = None
    hdr: bool = False
    dovi: bool = False
    atmos: bool = False
    hdr10plus: bool = False
    encoder: str | None = None
    size: int | None = None
    added_at: int | None = None


class Episode(msgspec.Struct):
    """Episode within a season."""

    episode_number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    runtime: int | None = None
    still_path: str | None = None
    rating: float | None = None
    director: str | None = None
    reel_image: str | None = None
    reel_sources: list[str] | None = None
    files: dict[str, Torrent] = {}


class Season(msgspec.Struct):
    """Season within a series."""

    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    poster_path: str | None = None
    episode_count: int | None = None
    episodes: list[Episode] = []


class Movie(msgspec.Struct):
    """A movie in the index (one or more versions/releases)."""

    title: str | None = None
    info: Info | None = None
    year: int | None = None
    newest: int | None = None
    cover_path: str | None = None
    backdrop_path: str | None = None
    showreel_images: list[str] | None = None
    showreel_source_sets: list[list[str]] | None = None
    files: dict[str, Torrent] = {}


class Series(msgspec.Struct):
    """A TV series in the index."""

    title: str | None = None
    info: Info | None = None
    alternative_titles: list[str] | None = None
    newest: int | None = None
    cover_path: str | None = None
    backdrop_path: str | None = None
    seasons: list[Season] = []


# ---------------------------------------------------------------------------
# Snapshot (disk format for index.json)
# ---------------------------------------------------------------------------

INDEX_SNAPSHOT_VERSION = 1


class IndexSnapshot(msgspec.Struct):
    """On-disk recovery snapshot of the full index."""

    v: int = INDEX_SNAPSHOT_VERSION
    generated_at: str = ""
    movies: dict[str, Movie] = {}
    series: dict[str, Series] = {}
    people: dict[int, Person] = {}


class TaskInfo(msgspec.Struct):
    """Progress info for a background task (scan, showreel, etc.)."""

    id: str
    status: str
    progress: float = 0.0
    detail: str = ""
