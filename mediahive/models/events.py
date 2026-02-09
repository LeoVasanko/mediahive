"""
Event types shared between scanner and WebSocket.

These types are used as:
- Internal scan events (scanner → server queue)
- WebSocket messages (server → clients)
"""

from __future__ import annotations

import msgspec

from .data import Movie, Series, TaskInfo


class Upsert(msgspec.Struct, tag="upsert"):
    """Single item inserted or updated."""

    kind: str  # "movie" or "series"
    item: Movie | Series


class Remove(msgspec.Struct, tag="remove"):
    """Single item removed."""

    kind: str
    id: str


class Task(msgspec.Struct, tag="task"):
    """Task progress broadcast."""

    data: TaskInfo


# Union of scan events (scanner → server) and WS broadcast messages
ScanEvent = Upsert | Task
