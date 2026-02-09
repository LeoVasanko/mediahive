"""
Protocol structures for API and WebSocket communication.

All types are msgspec.Structs for fast serialization.
"""

from __future__ import annotations

import msgspec
from fastapi.responses import Response

from .data import Movie, Series, TaskInfo


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
        return msgspec.json.encode(content)</content>
<parameter name="filePath">c:\mediahive\mediahive\models\protocol.py
