"""Protocol structures for API and WebSocket communication.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec
from fastapi.responses import Response

from .data import Movie, Series
from .events import Remove, ScanEvent, Task, Upsert
from .tmdb import Person

# ---------------------------------------------------------------------------
# WebSocket message types
# ---------------------------------------------------------------------------


class WsInitData(msgspec.Struct):
    """Payload of the init message."""

    movies: dict[str, Movie]
    series: dict[str, Series]
    people: dict[int, Person]


class WsInit(msgspec.Struct, tag="init"):
    """Full index sent on WS connect."""

    data: WsInitData


# Union of all outbound WS messages (for documentation / future decoding)
WsMessage = WsInit | Upsert | Remove | Task


# Re-export unified types for backward compatibility
__all__ = [
    "Remove",
    "ScanEvent",
    "Task",
    "Upsert",
    "WsInit",
    "WsInitData",
    "WsMessage",
]


# ---------------------------------------------------------------------------
# API request / response types
# ---------------------------------------------------------------------------


class PlayMediaRequest(msgspec.Struct):
    """POST /api/roots/{root_id}/play body."""

    file_path: str = ""
    player_id: str | None = None
    player_custom_cmd: str | None = None


class OpenFolderRequest(msgspec.Struct):
    """POST /api/roots/{root_id}/open-folder body."""

    folder_path: str = ""


class RootsRequest(msgspec.Struct):
    """PUT /api/roots body."""

    roots: dict[str, str]


class RootEntryResponse(msgspec.Struct):
    """Single root entry in responses."""

    name: str
    path: str
    root_id: str


class RootStatusResponse(msgspec.Struct):
    """Per-root status in GET /api/roots."""

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
