"""Protocol structures for API and WebSocket communication.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec
from fastapi.responses import Response

from .data import Movie, Series, TaskInfo
from .events import ScanEvent
from .tmdb import Person

# ---------------------------------------------------------------------------
# WebSocket message types
# ---------------------------------------------------------------------------


class WsRootStatus(msgspec.Struct):
    """Current status for one configured root."""

    root_id: str
    path: str
    status: str
    error: str | None = None
    snapshot_loaded: bool = False
    movies: int = 0
    series: int = 0


class WsRootInitData(msgspec.Struct):
    """Initial full index payload for one root."""

    movies: dict[str, Movie]
    series: dict[str, Series]
    people: dict[int, Person]


class WsRoots(msgspec.Struct, tag="roots"):
    """Root list and status update."""

    roots: list[WsRootStatus]


class WsInit(msgspec.Struct, tag="init"):
    """Full index payload keyed by root_id."""

    roots: dict[str, WsRootInitData]


class WsUpsert(msgspec.Struct, tag="upsert"):
    """Single item inserted or updated for one root."""

    root_id: str
    kind: str  # "movie" or "series"
    id: str
    item: Movie | Series
    people: dict[int, Person] | None = None


class WsRemove(msgspec.Struct, tag="remove"):
    """Single item removed for one root."""

    root_id: str
    kind: str
    id: str


class WsTask(msgspec.Struct, tag="task"):
    """Task progress update for one root."""

    root_id: str
    data: TaskInfo


# Union of all outbound WS messages (for documentation / future decoding)
WsMessage = WsRoots | WsInit | WsUpsert | WsRemove | WsTask


# Re-export unified types for backward compatibility
__all__ = [
    "ScanEvent",
    "WsInit",
    "WsMessage",
    "WsRemove",
    "WsRootInitData",
    "WsRootStatus",
    "WsRoots",
    "WsTask",
    "WsUpsert",
]


# ---------------------------------------------------------------------------
# API request / response types
# ---------------------------------------------------------------------------


class PlayMediaRequest(msgspec.Struct):
    """POST /api/play/{root_id} body."""

    file_path: str = ""
    player_id: str | None = None
    player_custom_cmd: str | None = None


class OpenFolderRequest(msgspec.Struct):
    """POST /api/open-folder/{root_id} body."""

    folder_path: str = ""


class RootsRequest(msgspec.Struct):
    """PUT /api/config/roots body."""

    roots: dict[str, str]


class PlaybackStateUpdateRequest(msgspec.Struct):
    """POST /api/meta/playback-state body."""

    root_id: str
    file_path: str
    pos: int | None = None


class RootEntryResponse(msgspec.Struct):
    """Single root entry in responses."""

    path: str
    root_id: str


class RootStatusResponse(msgspec.Struct):
    """Legacy per-root status shape kept for non-WS callers."""

    root_id: str
    path: str
    status: str
    error: str | None = None
    movies: int = 0
    series: int = 0


# ---------------------------------------------------------------------------
# FastAPI response helper
# ---------------------------------------------------------------------------


class MsgspecResponse(Response):
    """FastAPI response that serializes content with msgspec.json."""

    media_type = "application/json; charset=utf-8"

    def render(self, content: object) -> bytes:
        return msgspec.json.encode(content)
