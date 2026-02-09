"""
In-memory index store with disk snapshot and WebSocket broadcast.

The IndexStore is the single source of truth for the media index.
All mutations happen synchronously in the asyncio event loop — no locks needed.
index.json on disk is a recovery snapshot only, written periodically via a
debounced background task.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import msgspec
from aiopathlib import AsyncPath
from fastapi import WebSocket

from hivescan.structs import (
    IndexSnapshot,
    MediaStats,
    Movie,
    Series,
    TaskInfo,
    WsInit,
    WsInitData,
    WsRemove,
    WsTask,
    WsUpsert,
)

logger = logging.getLogger("hivescan.index_store")

# Debounce interval for writing snapshots to disk (seconds)
SNAPSHOT_DEBOUNCE = 5.0


class IndexStore:
    """In-memory media index with WS broadcast and disk snapshots."""

    def __init__(self, snapshot_path: Path, media_root: Optional[str] = None):
        self.snapshot_path = snapshot_path
        self.media_root = media_root

        # The index: keyed by item id
        self.movies: dict[str, Movie] = {}
        self.series: dict[str, Series] = {}

        # Connected WebSocket clients
        self._clients: set[WebSocket] = set()

        # Snapshot debounce state
        self._snapshot_dirty = False
        self._snapshot_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def load_snapshot(self) -> None:
        """Load index from disk snapshot (recovery on startup)."""
        ap = AsyncPath(self.snapshot_path)
        if not await ap.exists():
            logger.info("No snapshot found at %s, starting fresh", self.snapshot_path)
            return
        try:
            data = msgspec.json.decode(
                await ap.read_bytes(), type=IndexSnapshot
            )
            for m in data.movies:
                self.movies[m.id] = m
            for s in data.series:
                self.series[s.id] = s
            logger.info(
                "Loaded snapshot: %d movies, %d series",
                len(self.movies),
                len(self.series),
            )
        except Exception:
            logger.exception("Failed to load snapshot from %s", self.snapshot_path)

    async def _write_snapshot(self) -> None:
        """Write current index to disk (called from debounce task)."""
        movies_list = sorted(
            self.movies.values(), key=lambda x: (x.title.lower(), x.year or 0)
        )
        series_list = sorted(self.series.values(), key=lambda x: x.title.lower())

        total_movie_versions = sum(len(m.versions) for m in movies_list)
        total_series_episodes = sum(
            sum(len(season.episodes) for season in s.seasons) for s in series_list
        )

        snapshot = IndexSnapshot(
            generated_at=datetime.now().isoformat(),
            media_root=self.media_root,
            stats=MediaStats(
                total_movies=len(movies_list),
                total_movie_versions=total_movie_versions,
                total_series=len(series_list),
                total_series_episodes=total_series_episodes,
            ),
            movies=movies_list,
            series=series_list,
        )

        await AsyncPath(self.snapshot_path.parent).mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot_path.with_suffix(".tmp")
        await AsyncPath(tmp).write_bytes(msgspec.json.format(msgspec.json.encode(snapshot), indent=2))
        # os.replace is atomic and overwrites on all platforms (unlike rename on Windows)
        await asyncio.to_thread(os.replace, tmp, self.snapshot_path)
        logger.debug("Snapshot written to %s", self.snapshot_path)

    def _schedule_snapshot(self) -> None:
        """Schedule a debounced snapshot write."""
        self._snapshot_dirty = True
        if self._snapshot_task is None or self._snapshot_task.done():
            self._snapshot_task = asyncio.create_task(self._snapshot_writer())

    async def _snapshot_writer(self) -> None:
        """Flush to disk every SNAPSHOT_DEBOUNCE seconds while dirty."""
        while True:
            await asyncio.sleep(SNAPSHOT_DEBOUNCE)
            if self._snapshot_dirty:
                self._snapshot_dirty = False
                await self._write_snapshot()
            else:
                break  # No pending mutations — stop the loop

    async def flush_snapshot(self) -> None:
        """Force-write a snapshot immediately (e.g. on shutdown)."""
        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass
        await self._write_snapshot()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert_movie(self, item: Movie) -> bool:
        """Insert or update a movie. Returns True if it was a real change."""
        existing = self.movies.get(item.id)
        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                return False
        self.movies[item.id] = item
        self._schedule_snapshot()
        self._broadcast(WsUpsert(kind="movie", item=item))
        return True

    def upsert_series(self, item: Series) -> bool:
        """Insert or update a series. Returns True if it was a real change."""
        existing = self.series.get(item.id)
        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                return False
        self.series[item.id] = item
        self._schedule_snapshot()
        self._broadcast(WsUpsert(kind="series", item=item))
        return True

    def remove_movie(self, item_id: str) -> None:
        """Remove a movie from the index and broadcast."""
        self.movies.pop(item_id, None)
        self._schedule_snapshot()
        self._broadcast(WsRemove(kind="movie", id=item_id))

    def remove_series(self, item_id: str) -> None:
        """Remove a series from the index and broadcast."""
        self.series.pop(item_id, None)
        self._schedule_snapshot()
        self._broadcast(WsRemove(kind="series", id=item_id))

    # ------------------------------------------------------------------
    # WebSocket management
    # ------------------------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        """Accept a WS client and send the full index as init."""
        await ws.accept()
        self._clients.add(ws)
        logger.info("WS client connected (%d total)", len(self._clients))
        # Send full current state
        msg = WsInit(
            data=WsInitData(
                movies=list(self.movies.values()),
                series=list(self.series.values()),
            )
        )
        await ws.send_bytes(msgspec.json.encode(msg))

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a WS client."""
        self._clients.discard(ws)
        logger.info("WS client disconnected (%d remaining)", len(self._clients))

    def _broadcast(self, msg: object) -> None:
        """Broadcast a message to all connected WS clients (non-blocking)."""
        data = msgspec.json.encode(msg)
        dead: list[WebSocket] = []
        for ws in self._clients:
            asyncio.create_task(self._safe_send(ws, data, dead))
        # Clean up dead connections after sends are scheduled
        for ws in dead:
            self._clients.discard(ws)

    @staticmethod
    async def _safe_send(ws: WebSocket, data: bytes, dead: list) -> None:
        """Send data to a WS client; mark as dead on failure."""
        try:
            await ws.send_bytes(data)
        except Exception:
            dead.append(ws)

    def broadcast_task(self, task_info: TaskInfo) -> None:
        """Broadcast a task progress message to all WS clients."""
        self._broadcast(WsTask(data=task_info))

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_full_index(self) -> IndexSnapshot:
        """Return the full index as an IndexSnapshot."""
        movies_list = sorted(
            self.movies.values(), key=lambda x: (x.title.lower(), x.year or 0)
        )
        series_list = sorted(self.series.values(), key=lambda x: x.title.lower())

        total_movie_versions = sum(len(m.versions) for m in movies_list)
        total_series_episodes = sum(
            sum(len(season.episodes) for season in s.seasons) for s in series_list
        )

        return IndexSnapshot(
            generated_at=datetime.now().isoformat(),
            media_root=self.media_root,
            stats=MediaStats(
                total_movies=len(movies_list),
                total_movie_versions=total_movie_versions,
                total_series=len(series_list),
                total_series_episodes=total_series_episodes,
            ),
            movies=movies_list,
            series=series_list,
        )
