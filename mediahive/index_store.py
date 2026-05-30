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
    Movie,
    Series,
    TaskInfo,
)
from mediahive.models.events import Remove, Task, Upsert
from mediahive.models.protocol import (
    WsInit,
    WsInitData,
)
from mediahive.models.tmdb import Person

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
    ) -> None:
        self.snapshot_path = snapshot_path

        # The index: keyed by item id
        self.movies: dict[str, Movie] = {}
        self.series: dict[str, Series] = {}
        self.people: dict[int, Person] = {}
        self._movie_tmdb_ids: dict[int, str] = {}
        self._series_tmdb_ids: dict[int, str] = {}

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
            movies={},
            series={},
            people={},
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def load_snapshot(self) -> None:
        """Load index from disk snapshot (recovery on startup)."""
        ap = AsyncPath(self.snapshot_path)
        if not await ap.exists():
            logger.info("No snapshot found at %s, starting fresh", self.snapshot_path)
            self._schedule_snapshot_cache_refresh()
            return
        try:
            raw = await ap.read_bytes()
            loaded_movies, loaded_series, loaded_people = await asyncio.to_thread(
                self._load_snapshot_sync,
                raw,
            )
            self._merge_loaded_snapshot(
                loaded_movies,
                loaded_series,
                loaded_people,
            )
            self._rebuild_tmdb_indexes()

            self._schedule_snapshot_cache_refresh()
        except Exception:
            logger.exception("Failed to load snapshot from %s", self.snapshot_path)

    def _load_snapshot_sync(
        self,
        raw: bytes,
    ) -> tuple[dict[str, Movie], dict[str, Series], dict[int, Person]]:
        """Parse snapshot bytes in a thread-pool context."""
        data = msgspec.json.decode(raw, type=IndexSnapshot)
        loaded_movies = dict(data.movies)
        loaded_series = dict(data.series)
        loaded_people = dict(data.people)
        logger.info(
            "Loaded snapshot: %d movies, %d series, %d people",
            len(loaded_movies),
            len(loaded_series),
            len(loaded_people),
        )
        return loaded_movies, loaded_series, loaded_people

    def _merge_loaded_snapshot(
        self,
        movies: dict[str, Movie],
        series: dict[str, Series],
        people: dict[int, Person],
    ) -> None:
        """Merge loaded snapshot items without overriding newer in-memory updates."""
        for item_id, movie in movies.items():
            if item_id not in self.movies:
                self.movies[item_id] = movie
        for item_id, show in series.items():
            if item_id not in self.series:
                self.series[item_id] = show
        for person_id, person in people.items():
            self.people[person_id] = person

    @staticmethod
    def _get_tmdb_id(item: Movie | Series) -> int | None:
        if item.info is None:
            return None
        return item.info.tmdb_id

    def _rebuild_tmdb_indexes(self) -> None:
        """Rebuild TMDb id lookup maps from the current in-memory items."""
        self._movie_tmdb_ids.clear()
        self._series_tmdb_ids.clear()
        for item_id, movie in self.movies.items():
            tmdb_id = self._get_tmdb_id(movie)
            if tmdb_id is not None:
                self._movie_tmdb_ids[tmdb_id] = item_id
        for item_id, series in self.series.items():
            tmdb_id = self._get_tmdb_id(series)
            if tmdb_id is not None:
                self._series_tmdb_ids[tmdb_id] = item_id

    def _dedupe_tmdb_duplicates(self) -> None:
        """Remove duplicate entries that point at the same TMDb item."""
        seen_movies: dict[int, str] = {}
        for item_id, movie in list(self.movies.items()):
            tmdb_id = self._get_tmdb_id(movie)
            if tmdb_id is None:
                continue
            existing_id = seen_movies.get(tmdb_id)
            if existing_id is None:
                seen_movies[tmdb_id] = item_id
                continue
            if existing_id != item_id:
                self.movies.pop(item_id, None)

        seen_series: dict[int, str] = {}
        for item_id, series in list(self.series.items()):
            tmdb_id = self._get_tmdb_id(series)
            if tmdb_id is None:
                continue
            existing_id = seen_series.get(tmdb_id)
            if existing_id is None:
                seen_series[tmdb_id] = item_id
                continue
            if existing_id != item_id:
                self.series.pop(item_id, None)

        self._rebuild_tmdb_indexes()

    def _collapse_movie_tmdb_duplicates(self, tmdb_id: int, keep_id: str) -> None:
        """Remove other movie entries that share a TMDb id."""
        for item_id, movie in list(self.movies.items()):
            if item_id == keep_id:
                continue
            if self._get_tmdb_id(movie) == tmdb_id:
                self.movies.pop(item_id, None)
        self._rebuild_tmdb_indexes()

    def _collapse_series_tmdb_duplicates(self, tmdb_id: int, keep_id: str) -> None:
        """Remove other series entries that share a TMDb id."""
        for item_id, series in list(self.series.items()):
            if item_id == keep_id:
                continue
            if self._get_tmdb_id(series) == tmdb_id:
                self.series.pop(item_id, None)
        self._rebuild_tmdb_indexes()

    async def _write_snapshot(self) -> None:
        """Write current index to disk (called from debounce task)."""
        # Copy values on the event loop thread, then do full snapshot build + disk I/O
        # in a worker thread to keep the loop responsive.
        movies = dict(self.movies)
        series = dict(self.series)
        people = dict(self.people)
        await asyncio.to_thread(self._write_snapshot_sync, movies, series, people)
        logger.debug("Snapshot written to %s", self.snapshot_path)

    def _write_snapshot_sync(
        self,
        movies: dict[str, Movie],
        series: dict[str, Series],
        people: dict[int, Person],
    ) -> None:
        """Build and write snapshot synchronously in a worker thread."""
        snapshot = self._build_snapshot_from_maps(movies, series, people)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot_path.with_suffix(".tmp")
        tmp.write_bytes(msgspec.json.encode(snapshot))
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
        movies = dict(self.movies)
        series = dict(self.series)
        people = dict(self.people)
        self._cached_snapshot = await asyncio.to_thread(
            self._build_snapshot_from_maps,
            movies,
            series,
            people,
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

    def upsert_movie(
        self,
        item_id: str,
        item: Movie,
        people: dict[int, Person] | None = None,
    ) -> bool:
        """Insert or update a movie. Returns True if it was a real change."""
        tmdb_id = self._get_tmdb_id(item)
        existing_id = self._movie_tmdb_ids.get(tmdb_id) if tmdb_id is not None else None
        if existing_id is not None and existing_id != item_id:
            item_id = existing_id

        existing = self.movies.get(item_id)
        if tmdb_id is not None:
            self._movie_tmdb_ids[tmdb_id] = item_id
            self._collapse_movie_tmdb_duplicates(tmdb_id, item_id)

        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                if people:
                    self.people.update(people)
                    self._schedule_snapshot()
                    self._broadcast(
                        Upsert(kind="movie", id=item_id, item=item, people=people)
                    )
                    return True
                return False
        self.movies[item_id] = item
        if people:
            self.people.update(people)
        self._schedule_snapshot()
        self._broadcast(Upsert(kind="movie", id=item_id, item=item, people=people))
        return True

    def upsert_series(
        self,
        item_id: str,
        item: Series,
        people: dict[int, Person] | None = None,
    ) -> bool:
        """Insert or update a series. Returns True if it was a real change."""
        tmdb_id = self._get_tmdb_id(item)
        existing_id = (
            self._series_tmdb_ids.get(tmdb_id) if tmdb_id is not None else None
        )
        if existing_id is not None and existing_id != item_id:
            item_id = existing_id

        existing = self.series.get(item_id)
        if tmdb_id is not None:
            self._series_tmdb_ids[tmdb_id] = item_id
            self._collapse_series_tmdb_duplicates(tmdb_id, item_id)

        if existing is not None:
            if msgspec.json.encode(existing) == msgspec.json.encode(item):
                if people:
                    self.people.update(people)
                    self._schedule_snapshot()
                    self._broadcast(
                        Upsert(kind="series", id=item_id, item=item, people=people)
                    )
                    return True
                return False
        self.series[item_id] = item
        if people:
            self.people.update(people)
        self._schedule_snapshot()
        self._broadcast(Upsert(kind="series", id=item_id, item=item, people=people))
        return True

    def remove_movie(self, item_id: str) -> None:
        """Remove a movie from the index and broadcast."""
        self.movies.pop(item_id, None)
        for tmdb_id, mapped_id in list(self._movie_tmdb_ids.items()):
            if mapped_id == item_id:
                self._movie_tmdb_ids.pop(tmdb_id, None)
        self._schedule_snapshot()
        self._broadcast(Remove(kind="movie", id=item_id))

    def remove_series(self, item_id: str) -> None:
        """Remove a series from the index and broadcast."""
        self.series.pop(item_id, None)
        for tmdb_id, mapped_id in list(self._series_tmdb_ids.items()):
            if mapped_id == item_id:
                self._series_tmdb_ids.pop(tmdb_id, None)
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
                movies=dict(self.movies),
                series=dict(self.series),
                people=dict(self.people),
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

    def _build_snapshot_from_maps(
        self,
        movies: dict[str, Movie],
        series: dict[str, Series],
        people: dict[int, Person],
    ) -> IndexSnapshot:
        """Build a keyed IndexSnapshot from map copies."""
        return IndexSnapshot(
            generated_at=datetime.now().isoformat(),
            movies=movies,
            series=series,
            people=people,
        )

    def _build_snapshot(self) -> IndexSnapshot:
        """Build a sorted IndexSnapshot with computed stats."""
        return self._build_snapshot_from_maps(
            dict(self.movies),
            dict(self.series),
            dict(self.people),
        )

    def get_full_index(self) -> IndexSnapshot:
        """Return the latest in-memory IndexSnapshot cache."""
        return self._cached_snapshot
