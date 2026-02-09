"""
Typed structures for the hivescan/mediahive API, index state, and WebSocket protocol.

All API and state types are msgspec.Structs for fast serialization.
Internal scanning types (ContentHash, ParsedContent) remain in models.py.
"""

from __future__ import annotations

import msgspec
from fastapi.responses import Response


# ---------------------------------------------------------------------------
# Sub-types (shared by TMDb results and index items)
# ---------------------------------------------------------------------------


class CastMember(msgspec.Struct):
    """Actor/crew member."""

    name: str
    character: str | None = None
    profile_path: str | None = None


class SimilarMedia(msgspec.Struct):
    """Pointer to a similar movie/series on TMDb."""

    id: int
    title: str
    poster_path: str | None = None


# ---------------------------------------------------------------------------
# TMDb result types (returned by tmdb_client, consumed by indexer)
# ---------------------------------------------------------------------------


class TMDbEpisodeInfo(msgspec.Struct):
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


class TMDbSeasonInfo(msgspec.Struct):
    """Season metadata from TMDb."""

    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    poster_path: str | None = None
    episode_count: int | None = None
    episodes: list[TMDbEpisodeInfo] | None = None


class TMDbInfo(msgspec.Struct):
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


# ---------------------------------------------------------------------------
# Index item types (the state stored in IndexStore, sent over WS/API)
# ---------------------------------------------------------------------------


class MovieVersion(msgspec.Struct):
    """One release/torrent of a movie, keyed by relative torrent path."""

    torrent_title: str | None = None
    playable_file: str | None = None
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    audio: str | None = None
    encoder: str | None = None
    size: int | None = None
    newest: int | None = None


class EpisodeRelease(msgspec.Struct):
    """One release/torrent file of an episode, keyed by relative torrent path."""

    torrent_title: str | None = None
    playable_file: str | None = None
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    audio: str | None = None
    encoder: str | None = None
    size: int | None = None


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
    releases: dict[str, EpisodeRelease] = {}


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

    id: str
    title: str
    original_title: str | None = None
    alternative_titles: list[str] | None = None
    year: int | None = None
    newest: int | None = None
    cover_path: str | None = None
    backdrop_path: str | None = None
    showreel_images: list[str] | None = None
    versions: dict[str, MovieVersion] = {}
    tmdb_id: int | None = None
    tmdb_title: str | None = None
    rating: float | None = None
    vote_count: int | None = None
    overview: str | None = None
    genres: list[str] | None = None
    release_date: str | None = None
    runtime: int | None = None
    status: str | None = None
    tagline: str | None = None
    poster_path: str | None = None
    similar: list[SimilarMedia] | None = None
    keywords: list[str] | None = None
    cast: list[CastMember] | None = None
    director: str | None = None


class Series(msgspec.Struct):
    """A TV series in the index."""

    id: str
    title: str
    original_title: str | None = None
    alternative_titles: list[str] | None = None
    newest: int | None = None
    cover_path: str | None = None
    backdrop_path: str | None = None
    seasons: list[Season] = []
    tmdb_id: int | None = None
    tmdb_title: str | None = None
    rating: float | None = None
    vote_count: int | None = None
    overview: str | None = None
    genres: list[str] | None = None
    release_date: str | None = None
    status: str | None = None
    tagline: str | None = None
    poster_path: str | None = None
    similar: list[SimilarMedia] | None = None
    keywords: list[str] | None = None
    cast: list[CastMember] | None = None
    creators: list[str] | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    networks: list[str] | None = None


# ---------------------------------------------------------------------------
# Snapshot (disk format for index.json)
# ---------------------------------------------------------------------------


class MediaStats(msgspec.Struct):
    """Aggregate counts for the index snapshot."""

    total_movies: int = 0
    total_movie_versions: int = 0
    total_series: int = 0
    total_series_episodes: int = 0


class IndexSnapshot(msgspec.Struct):
    """On-disk recovery snapshot of the full index."""

    version: int = 6
    generated_at: str = ""
    media_root: str | None = None
    stats: MediaStats = msgspec.UNSET  # type: ignore[assignment]
    movies: list[Movie] = []
    series: list[Series] = []

    def __post_init__(self):
        if self.stats is msgspec.UNSET:
            self.stats = MediaStats()


# ---------------------------------------------------------------------------
# WebSocket message types
# ---------------------------------------------------------------------------


class WsInitData(msgspec.Struct):
    """Payload of the init message."""

    movies: list[Movie]
    series: list[Series]


class WsInit(msgspec.Struct, tag="init"):
    """Full index sent on WS connect."""

    data: WsInitData


class WsUpsert(msgspec.Struct, tag="upsert"):
    """Single item inserted or updated."""

    kind: str
    item: Movie | Series


class WsRemove(msgspec.Struct, tag="remove"):
    """Single item removed."""

    kind: str
    id: str


class TaskInfo(msgspec.Struct):
    """Progress info for a background task (scan, showreel, etc.)."""

    id: str
    status: str
    progress: float = 0.0
    detail: str = ""


class WsTask(msgspec.Struct, tag="task"):
    """Task progress broadcast."""

    data: TaskInfo


# Union of all outbound WS messages (for documentation / future decoding)
WsMessage = WsInit | WsUpsert | WsRemove | WsTask


# ---------------------------------------------------------------------------
# API request / response types
# ---------------------------------------------------------------------------


class ScanRequest(msgspec.Struct):
    """POST /api/scan body."""

    paths: list[str] | None = None


class StatusResponse(msgspec.Struct):
    """GET /api/status response."""

    scanning: bool = False
    movies: int = 0
    series: int = 0
    showreel_queue: int = 0


class PlayMediaRequest(msgspec.Struct):
    """POST /api/play body (mediahive server)."""

    file_path: str = ""


class OpenFolderRequest(msgspec.Struct):
    """POST /api/open-folder body (mediahive server)."""

    folder_path: str = ""


# ---------------------------------------------------------------------------
# FastAPI response helper
# ---------------------------------------------------------------------------


class MsgspecResponse(Response):
    """FastAPI response that serializes content with msgspec.json."""

    media_type = "application/json; charset=utf-8"

    def render(self, content: object) -> bytes:
        return msgspec.json.encode(content)
