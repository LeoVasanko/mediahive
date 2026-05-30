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
    """Single item inserted or updated."""

    kind: str  # "movie" or "series"
    id: str
    item: Movie | Series
    people: dict[int, Person] | None = None


class Remove(msgspec.Struct, tag="remove"):
    """Single item removed."""

    kind: str
    id: str


class Task(msgspec.Struct, tag="task"):
    """Task progress broadcast."""

    data: TaskInfo


# Union of scan events (scanner → server) and WS broadcast messages
ScanEvent = Upsert | Task
