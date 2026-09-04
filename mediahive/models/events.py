"""Event types shared between scanner and WebSocket.

These types are used as:
- Internal scan events (scanner → server queue)
- WebSocket messages (server → clients)
"""

from __future__ import annotations

import msgspec

from .data import Movie, Series, TaskInfo
from .tmdb import Person


class Upsert(msgspec.Struct, tag="upsert"):
    """Single item inserted or updated.

    ``scanned`` lists the media-root-relative torrent paths whose content was
    (re)scanned to build this item.  When present, the store merges the item
    into the existing entry instead of replacing it wholesale: only data
    belonging to the scanned torrents is replaced.  ``None`` means full
    replacement (legacy behaviour).
    """

    kind: str  # "movie" or "series"
    id: str
    item: Movie | Series
    people: dict[int, Person] | None = None
    scanned: list[str] | None = None


class Remove(msgspec.Struct, tag="remove"):
    """Single item removed."""

    kind: str
    id: str


class Sync(msgspec.Struct, tag="sync"):
    """Full set of media-root-relative torrent paths currently on disk.

    Sent by the scanner after a successfully completed discovery pass so the
    store can drop entries whose files no longer exist.  Internal only —
    never forwarded to WebSocket clients.
    """

    paths: list[str]


class MovieShowreel(msgspec.Struct, tag="movie-showreel"):
    """Reel worker result for a movie (internal, scanner → store)."""

    id: str
    showreel_images: list[str] | None = None
    showreel_source_sets: list[list[str]] | None = None


class EpisodeReel(msgspec.Struct, tag="episode-reel"):
    """Reel worker result for one episode (internal, scanner → store)."""

    id: str
    season: int
    episode: int
    reel_image: str | None = None
    reel_sources: list[str] | None = None


class Task(msgspec.Struct, tag="task"):
    """Task progress broadcast."""

    data: TaskInfo


# Union of scan events (scanner → server) and WS broadcast messages
ScanEvent = Upsert | Sync | MovieShowreel | EpisodeReel | Task
