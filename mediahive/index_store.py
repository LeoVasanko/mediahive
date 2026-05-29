"""In-memory index store with disk snapshot and WebSocket broadcast.

The IndexStore is the single source of truth for the media index.
All mutations happen synchronously in the asyncio event loop — no locks needed.
index.json on disk is a recovery snapshot only, written periodically via a
debounced background task.
"""

import asyncio
import contextlib
import logging
from datetime import datetime
from pathlib import Path

import msgspec
from aiopathlib import AsyncPath
from fastapi import WebSocket

from mediahive.models.data import (
    IndexSnapshot,
    MediaStats,
    Movie,
    Series,
    TaskInfo,
)
from mediahive.models.events import Remove, Task, Upsert
from mediahive.models.protocol import (
    WsInit,
    WsInitData,
)

logger = logging.getLogger("mediahive.index_store")

# Debounce interval for writing snapshots to disk (seconds)
SNAPSHOT_DEBOUNCE = 5.0
# Debounce interval for rebuilding the in-memory API snapshot (seconds)
SNAPSHOT_CACHE_DEBOUNCE = 0.25


class IndexStore:
    """In-memory media index with WS broadcast and disk snapshots.

    # TODO: Decouple WS transport from data storage
    # Consider splitting WS broadcasting into a separate Broadcaster class that:
    # - Owns per-connection queues instead of direct WebSocket references
    # - Each WS connection gets its own asyncio.Queue for messages
    # - Scanner pushes events to all queues; each WS reader drains its own queue
    # This would make IndexStore testable without FastAPI's WebSocket.
    """

    def __init__(
        self,
        snapshot_path: Path,
        media_root: str | None = None,
        root_id: str | None = None,
    ) -> None:
        self.snapshot_path = snapshot_path
        self.media_root = media_root
        self.root_id = root_id

        # The index: keyed by item id
        self.movies: dict[str, Movie] = {}
        self.series: dict[str, Series] = {}

        # Connected WebSocket clients
        self._clients: set[WebSocket] = set()

        # Snapshot debounce state
        self._snapshot_dirty = False
        self._snapshot_task: asyncio.Task | None = None

        # In-memory API snapshot cache (served by get_full_index)
        self._snapshot_cache_dirty = True
        self._snapshot_cache_task: asyncio.Task | None = None
        self._cached_snapshot = IndexSnapshot(
            generated_at=datetime.now().isoformat(),
            media_root=self.media_root,
            stats=MediaStats(),
            movies=[],
            series=[],
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _maybe_migrate_id(self, item_id: str) -> str:
        """Strip any legacy root_id prefix, leaving only the content hash."""
        if ":" in item_id:
            return item_id.split(":", 1)[1]
        return item_id

    async def load_snapshot(self) -> None:
        """Load index from disk snapshot (recovery on startup)."""
        ap = AsyncPath(self.snapshot_path)
        if not await ap.exists():
            logger.info("No snapshot found at %s, starting fresh", self.snapshot_path)
            self._schedule_snapshot_cache_refresh()
            return
        try:
            raw = await ap.read_bytes()
            loaded_movies, loaded_series = await asyncio.to_thread(
                self._load_snapshot_sync,
                raw,
            )
            self._merge_loaded_snapshot(
                loaded_movies,
                loaded_series,
            )

            self._schedule_snapshot_cache_refresh()
        except Exception:
            logger.exception("Failed to load snapshot from %s", self.snapshot_path)

    def _load_snapshot_sync(self, raw: bytes) -> tuple[list[Movie], list[Series]]:
        """Parse snapshot bytes in a thread-pool context."""
        data = msgspec.json.decode(raw, type=IndexSnapshot)
        loaded_movies: list[Movie] = []
        loaded_series: list[Series] = []
        for m in data.movies:
            m.id = self._maybe_migrate_id(m.id)
            m.root_id = self.root_id
            loaded_movies.append(m)
        for s in data.series:
            s.id = self._maybe_migrate_id(s.id)
            s.root_id = self.root_id
            loaded_series.append(s)
        logger.info(
            "Loaded snapshot: %d movies, %d series",
            len(loaded_movies),
            len(loaded_series),
        )
        return loaded_movies, loaded_series

    def _merge_loaded_snapshot(
        self,
        movies: list[Movie],
        series: list[Series],
    ) -> None:
        """Merge loaded snapshot items without overriding newer in-memory updates."""
        for movie in movies:
            if movie.id not in self.movies:
                self.movies[movie.id] = movie
        for show in series:
            if show.id not in self.series:
                self.series[show.id] = show

    async def _write_snapshot(self) -> None:
        """Write current index to disk (called from debounce task)."""
        # Copy values on the event loop thread, then do full snapshot build + disk I/O
        # in a worker thread to keep the loop responsive.
        movies = list(self.movies.values())
        series = list(self.series.values())
        await asyncio.to_thread(self._write_snapshot_sync, movies, series)
        logger.debug("Snapshot written to %s", self.snapshot_path)

    def _write_snapshot_sync(self, movies: list[Movie], series: list[Series]) -> None:
        """Build and write snapshot synchronously in a worker thread."""
        snapshot = self._build_snapshot_from_lists(movies, series)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot_path.with_suffix(".tmp")
        tmp.write_bytes(msgspec.json.format(msgspec.json.encode(snapshot), indent=2))
        # Path.replace is atomic and overwrites on all platforms.
        tmp.replace(self.snapshot_path)

    def _schedule_snapshot(self) -> None:
        """Schedule a debounced snapshot write."""
        self._snapshot_dirty = True
        if self._snapshot_task is None or self._snapshot_task.done():
            self._snapshot_task = asyncio.create_task(self._snapshot_writer())
        self._schedule_snapshot_cache_refresh()

    def _schedule_snapshot_cache_refresh(self) -> None:
        """Schedule a debounced rebuild of the in-memory API snapshot cache."""
        self._snapshot_cache_dirty = True
        if self._snapshot_cache_task is None or self._snapshot_cache_task.done():
            self._snapshot_cache_task = asyncio.create_task(
                self._snapshot_cache_writer()
            )

    async def _refresh_snapshot_cache_once(self) -> None:
        """Rebuild cached snapshot once using copied store values."""
        movies = list(self.movies.values())
        series = list(self.series.values())
        self._cached_snapshot = await asyncio.to_thread(
            self._build_snapshot_from_lists,
            movies,
            series,
        )

    async def _snapshot_cache_writer(self) -> None:
        """Refresh the in-memory snapshot cache while mutations are pending."""
        while True:
            await asyncio.sleep(SNAPSHOT_CACHE_DEBOUNCE)
            if self._snapshot_cache_dirty:
                self._snapshot_cache_dirty = False
                await self._refresh_snapshot_cache_once()
            else:
                break

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
        if self._snapshot_cache_task and not self._snapshot_cache_task.done():
            self._snapshot_cache_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._snapshot_cache_task
        await self._refresh_snapshot_cache_once()

        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._snapshot_task
        await self._write_snapshot()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert_movie(self, item: Movie) -> bool:
        """Insert or update a movie. Returns True if it was a real change."""
        item.id = self._maybe_migrate_id(item.id)
        if not item.root_id and self.root_id:
            item.root_id = self.root_id
        existing = self.movies.get(item.id)
        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                return False
        self.movies[item.id] = item
        self._schedule_snapshot()
        self._broadcast(Upsert(kind="movie", item=item))
        return True

    def upsert_series(self, item: Series) -> bool:
        """Insert or update a series. Returns True if it was a real change."""
        item.id = self._maybe_migrate_id(item.id)
        if not item.root_id and self.root_id:
            item.root_id = self.root_id
        existing = self.series.get(item.id)
        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                return False
        self.series[item.id] = item
        self._schedule_snapshot()
        self._broadcast(Upsert(kind="series", item=item))
        return True

    def remove_movie(self, item_id: str) -> None:
        """Remove a movie from the index and broadcast."""
        item_id = self._maybe_migrate_id(item_id)
        self.movies.pop(item_id, None)
        self._schedule_snapshot()
        self._broadcast(Remove(kind="movie", id=item_id))

    def remove_series(self, item_id: str) -> None:
        """Remove a series from the index and broadcast."""
        item_id = self._maybe_migrate_id(item_id)
        self.series.pop(item_id, None)
        self._schedule_snapshot()
        self._broadcast(Remove(kind="series", id=item_id))

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
        except OSError, RuntimeError:
            dead.append(ws)

    def broadcast_task(self, task_info: TaskInfo) -> None:
        """Broadcast a task progress message to all WS clients."""
        self._broadcast(Task(data=task_info))

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _build_snapshot_from_lists(
        self,
        movies: list[Movie],
        series: list[Series],
    ) -> IndexSnapshot:
        """Build a sorted IndexSnapshot with computed stats from list copies."""
        movies_list = sorted(
            movies,
            key=lambda x: ((x.title or "").lower(), x.year or 0),
        )
        series_list = sorted(series, key=lambda x: (x.title or "").lower())

        total_movie_versions = sum(len(m.torrents) for m in movies_list)
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

    def _build_snapshot(self) -> IndexSnapshot:
        """Build a sorted IndexSnapshot with computed stats."""
        return self._build_snapshot_from_lists(
            list(self.movies.values()),
            list(self.series.values()),
        )

    def get_full_index(self) -> IndexSnapshot:
        """Return the latest in-memory IndexSnapshot cache."""
        return self._cached_snapshot
