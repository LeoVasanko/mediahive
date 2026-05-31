"""TMDb data structures.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec

# ---------------------------------------------------------------------------
# Sub-types (shared by TMDb results and index items)
# ---------------------------------------------------------------------------


class CastCredit(msgspec.Struct, array_like=True):
    """Cast reference embedded in media info (character + person id)."""

    character: str | None = None
    id: int | None = None


class Person(msgspec.Struct, array_like=True):
    """Deduplicated person payload stored in top-level people map."""

    name: str
    profile_path: str | None = None
    gender: str | None = None


# ---------------------------------------------------------------------------
# TMDb result types
# ---------------------------------------------------------------------------


class EpisodeInfo(msgspec.Struct):
    """Episode metadata from TMDb."""

    episode_number: int
    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    runtime: int | None = None
    still_path: str | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    director: str | None = None


class SeasonInfo(msgspec.Struct):
    """Season metadata from TMDb."""

    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    poster_path: str | None = None
    episode_count: int | None = None
    episodes: list[EpisodeInfo] | None = None


class Info(msgspec.Struct):
    """Full metadata result from TMDb (movies or series)."""

    tmdb_id: int
    title: str | None = None
    original_title: str | None = None
    original_language: str | None = None
    alternative_titles: list[str] | None = None
    rating: float | None = None
    vote_count: int | None = None
    overview: str | None = None
    genres: list[str] | None = None
    release_date: str | None = None
    runtime: int | None = None
    collection: str | None = None
    status: str | None = None
    tagline: str | None = None
    keywords: list[str] | None = None
    cast: list[CastCredit] | None = None
    director: str | None = None
    creators: list[str] | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    networks: list[str] | None = None
