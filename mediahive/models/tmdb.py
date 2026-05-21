"""
TMDb data structures.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec


# ---------------------------------------------------------------------------
# Sub-types (shared by TMDb results and index items)
# ---------------------------------------------------------------------------


class CastMember(msgspec.Struct):
    """Actor/crew member."""

    name: str
    character: str | None = None
    profile_path: str | None = None
    gender: str | None = None


class SimilarMedia(msgspec.Struct):
    """Pointer to a similar movie/series on TMDb."""

    id: int
    title: str
    poster_path: str | None = None


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
    alternative_titles: list[str] | None = None
    rating: float | None = None
    vote_count: int | None = None
    overview: str | None = None
    genres: list[str] | None = None
    release_date: str | None = None
    runtime: int | None = None
    status: str | None = None
    tagline: str | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    similar: list[SimilarMedia] | None = None
    keywords: list[str] | None = None
    cast: list[CastMember] | None = None
    director: str | None = None
    creators: list[str] | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    networks: list[str] | None = None
