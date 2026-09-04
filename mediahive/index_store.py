"""In-memory index store with disk snapshot and WebSocket broadcast.

The IndexStore is the single source of truth for the media index.
All mutations happen synchronously in the asyncio event loop — no locks needed.
index.json on disk is a recovery snapshot only, written periodically via a
debounced background task.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import msgspec
from aiopathlib import AsyncPath
from fastapi import WebSocket

from mediahive.models.data import (
    Episode,
    IndexSnapshot,
    Movie,
    Season,
    Series,
    TaskInfo,
    Torrent,
)
from mediahive.models.events import Remove, Task, Upsert
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
        self.snapshot_loaded = False

        # The index: keyed by item id
        self.movies: dict[str, Movie] = {}
        self.series: dict[str, Series] = {}
        self.people: dict[int, Person] = {}
        self._movie_tmdb_ids: dict[int, str] = {}
        self._series_tmdb_ids: dict[int, str] = {}

        # Connected WebSocket clients
        self._clients: set[WebSocket] = set()
        # Passive listeners for broadcast events (used by server-level WS fan-in)
        self._listeners: set[Callable[[object], None]] = set()

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
        self.snapshot_loaded = False
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
            self.snapshot_loaded = True

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

    @staticmethod
    def _newest_from_files(files: dict[str, Torrent]) -> int | None:
        timestamps = [t.added_at for t in files.values() if t.added_at]
        return max(timestamps) if timestamps else None

    # ------------------------------------------------------------------
    # Merge helpers (partial rescan support)
    # ------------------------------------------------------------------

    def _merge_movie(self, existing: Movie, new: Movie, scanned: set[str]) -> Movie:
        """Merge a partially rebuilt movie into the existing entry.

        File entries belonging to torrents in ``scanned`` are replaced by the
        new data; everything else is preserved.
        """
        files = {k: v for k, v in existing.files.items() if k not in scanned}
        files.update(new.files)
        return Movie(
            title=new.title or existing.title,
            info=new.info or existing.info,
            year=new.year if new.year is not None else existing.year,
            newest=(
                self._newest_from_files(files)
                or max(filter(None, [existing.newest, new.newest]), default=None)
            ),
            cover_path=new.cover_path or existing.cover_path,
            backdrop_path=new.backdrop_path or existing.backdrop_path,
            showreel_images=new.showreel_images or existing.showreel_images,
            showreel_source_sets=new.showreel_source_sets
            or existing.showreel_source_sets,
            files=files,
        )

    def _merge_series(
        self, existing: Series, new: Series, scanned: set[str]
    ) -> Series:
        """Merge a partially rebuilt series into the existing entry.

        File entries belonging to torrents in ``scanned`` are replaced by the
        new data; seasons/episodes/files from torrents that were not rescanned
        are preserved.  Episodes and seasons left without files are dropped.
        """
        seasons: dict[int, Season] = {}
        for season in existing.seasons:
            episodes: dict[int, Episode] = {}
            for ep in season.episodes:
                files = {k: v for k, v in ep.files.items() if k not in scanned}
                if files:
                    episodes[ep.episode_number] = msgspec.structs.replace(
                        ep, files=files
                    )
            if episodes:
                seasons[season.season_number] = msgspec.structs.replace(
                    season,
                    episodes=list(episodes.values()),
                    episode_count=len(episodes),
                )

        for season in new.seasons:
            current = seasons.get(season.season_number)
            if current is None:
                seasons[season.season_number] = season
                continue
            episodes = {ep.episode_number: ep for ep in current.episodes}
            for ep in season.episodes:
                old = episodes.get(ep.episode_number)
                if old is None:
                    episodes[ep.episode_number] = ep
                    continue
                # Same episode from an unscanned torrent too: union the files,
                # prefer fresh metadata/reel info from the new scan.
                files = dict(old.files)
                files.update(ep.files)
                episodes[ep.episode_number] = msgspec.structs.replace(
                    ep,
                    files=files,
                    reel_image=ep.reel_image or old.reel_image,
                    reel_sources=ep.reel_sources or old.reel_sources,
                )
            ordered = [episodes[k] for k in sorted(episodes)]
            seasons[season.season_number] = msgspec.structs.replace(
                season,
                episodes=ordered,
                episode_count=len(ordered),
                poster_path=season.poster_path or current.poster_path,
            )

        alt_titles = sorted(
            set(existing.alternative_titles or [])
            | set(new.alternative_titles or [])
        )
        return Series(
            title=new.title or existing.title,
            info=new.info or existing.info,
            alternative_titles=alt_titles or None,
            newest=max(filter(None, [existing.newest, new.newest]), default=None),
            cover_path=new.cover_path or existing.cover_path,
            backdrop_path=new.backdrop_path or existing.backdrop_path,
            seasons=[seasons[k] for k in sorted(seasons)],
        )

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
        """Fold other entries that share a TMDb id into the kept one."""
        for item_id, movie in list(self.movies.items()):
            if item_id == keep_id:
                continue
            if self._get_tmdb_id(movie) != tmdb_id:
                continue
            kept = self.movies.get(keep_id)
            if kept is not None:
                # Preserve any file versions the duplicate alone carried.
                self.movies[keep_id] = self._merge_movie(movie, kept, set())
            self.movies.pop(item_id, None)
            if self._movie_tmdb_ids.get(tmdb_id) == item_id:
                self._movie_tmdb_ids[tmdb_id] = keep_id

    def _collapse_series_tmdb_duplicates(self, tmdb_id: int, keep_id: str) -> None:
        """Fold other entries that share a TMDb id into the kept one."""
        for item_id, series in list(self.series.items()):
            if item_id == keep_id:
                continue
            if self._get_tmdb_id(series) != tmdb_id:
                continue
            kept = self.series.get(keep_id)
            if kept is not None:
                # Preserve any seasons/episodes the duplicate alone carried.
                self.series[keep_id] = self._merge_series(series, kept, set())
            self.series.pop(item_id, None)
            if self._series_tmdb_ids.get(tmdb_id) == item_id:
                self._series_tmdb_ids[tmdb_id] = keep_id

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
        scanned: list[str] | None = None,
    ) -> bool:
        """Insert or update a movie. Returns True if it was a real change.

        When ``scanned`` is given, the item is a partial rebuild covering only
        those torrent paths; it is merged into the existing entry instead of
        replacing it.
        """
        tmdb_id = self._get_tmdb_id(item)
        existing_id = self._movie_tmdb_ids.get(tmdb_id) if tmdb_id is not None else None
        if existing_id is not None and existing_id != item_id:
            item_id = existing_id

        existing = self.movies.get(item_id)
        if tmdb_id is not None:
            self._movie_tmdb_ids[tmdb_id] = item_id
            self._collapse_movie_tmdb_duplicates(tmdb_id, item_id)
            existing = self.movies.get(item_id)

        if existing is not None and scanned is not None:
            item = self._merge_movie(existing, item, set(scanned))

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
        scanned: list[str] | None = None,
    ) -> bool:
        """Insert or update a series. Returns True if it was a real change.

        When ``scanned`` is given, the item is a partial rebuild covering only
        those torrent paths; it is merged into the existing entry instead of
        replacing it.
        """
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
            existing = self.series.get(item_id)

        if existing is not None and scanned is not None:
            item = self._merge_series(existing, item, set(scanned))

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
        movie = self.movies.pop(item_id, None)
        if movie is None:
            return
        tmdb_id = self._get_tmdb_id(movie)
        if tmdb_id is not None and self._movie_tmdb_ids.get(tmdb_id) == item_id:
            self._movie_tmdb_ids.pop(tmdb_id, None)
        self._schedule_snapshot()
        self._broadcast(Remove(kind="movie", id=item_id))

    def remove_series(self, item_id: str) -> None:
        """Remove a series from the index and broadcast."""
        series = self.series.pop(item_id, None)
        if series is None:
            return
        tmdb_id = self._get_tmdb_id(series)
        if tmdb_id is not None and self._series_tmdb_ids.get(tmdb_id) == item_id:
            self._series_tmdb_ids.pop(tmdb_id, None)
        self._schedule_snapshot()
        self._broadcast(Remove(kind="series", id=item_id))

    # ------------------------------------------------------------------
    # Scanner-driven maintenance
    # ------------------------------------------------------------------

    def sync_torrent_paths(self, paths: set[str]) -> None:
        """Drop file entries whose torrent path no longer exists on disk.

        ``paths`` is the complete set of media-root-relative torrent paths the
        scanner found during a fully completed discovery pass.  Episodes and
        seasons left without files are dropped; items left without any files
        are removed entirely.
        """
        for item_id, movie in list(self.movies.items()):
            kept = {k: v for k, v in movie.files.items() if k in paths}
            if len(kept) == len(movie.files):
                continue
            if not kept:
                self.remove_movie(item_id)
                continue
            updated = msgspec.structs.replace(
                movie, files=kept, newest=self._newest_from_files(kept)
            )
            self.movies[item_id] = updated
            self._schedule_snapshot()
            self._broadcast(Upsert(kind="movie", id=item_id, item=updated))

        for item_id, series in list(self.series.items()):
            removed_any = False
            new_seasons: list[Season] = []
            for season in series.seasons:
                new_episodes: list[Episode] = []
                for ep in season.episodes:
                    files = {k: v for k, v in ep.files.items() if k in paths}
                    if len(files) < len(ep.files):
                        removed_any = True
                    if files:
                        new_episodes.append(
                            msgspec.structs.replace(ep, files=files)
                        )
                    else:
                        removed_any = True
                if not new_episodes:
                    removed_any = True
                    continue
                if len(new_episodes) < len(season.episodes):
                    season = msgspec.structs.replace(
                        season,
                        episodes=new_episodes,
                        episode_count=len(new_episodes),
                    )
                new_seasons.append(season)
            if not removed_any:
                continue
            if not new_seasons:
                self.remove_series(item_id)
                continue
            updated_series = msgspec.structs.replace(series, seasons=new_seasons)
            self.series[item_id] = updated_series
            self._schedule_snapshot()
            self._broadcast(Upsert(kind="series", id=item_id, item=updated_series))

    def set_movie_showreel(
        self,
        item_id: str,
        showreel_images: list[str] | None,
        showreel_source_sets: list[list[str]] | None,
    ) -> None:
        """Update only the showreel fields of a movie (reel worker callback)."""
        movie = self.movies.get(item_id)
        if movie is None:
            return
        updated = msgspec.structs.replace(
            movie,
            showreel_images=showreel_images,
            showreel_source_sets=showreel_source_sets,
        )
        if msgspec.json.encode(updated) == msgspec.json.encode(movie):
            return
        self.movies[item_id] = updated
        self._schedule_snapshot()
        self._broadcast(Upsert(kind="movie", id=item_id, item=updated))

    def set_episode_reel(
        self,
        item_id: str,
        season_num: int,
        episode_num: int,
        reel_image: str | None,
        reel_sources: list[str] | None,
    ) -> None:
        """Update only the reel fields of one episode (reel worker callback)."""
        series = self.series.get(item_id)
        if series is None:
            return
        for season in series.seasons:
            if season.season_number != season_num:
                continue
            for ep in season.episodes:
                if ep.episode_number != episode_num:
                    continue
                if ep.reel_image == reel_image and ep.reel_sources == reel_sources:
                    return
                new_episodes = [
                    msgspec.structs.replace(
                        e, reel_image=reel_image, reel_sources=reel_sources
                    )
                    if e is ep
                    else e
                    for e in season.episodes
                ]
                new_seasons = [
                    msgspec.structs.replace(s, episodes=new_episodes)
                    if s is season
                    else s
                    for s in series.seasons
                ]
                updated = msgspec.structs.replace(series, seasons=new_seasons)
                self.series[item_id] = updated
                self._schedule_snapshot()
                self._broadcast(Upsert(kind="series", id=item_id, item=updated))
                return

    # ------------------------------------------------------------------
    # WebSocket management
    # ------------------------------------------------------------------

    def add_listener(self, listener: Callable[[object], None]) -> None:
        """Register a listener called for each broadcast message."""
        self._listeners.add(listener)

    def remove_listener(self, listener: Callable[[object], None]) -> None:
        """Unregister a previously registered broadcast listener."""
        self._listeners.discard(listener)

    async def connect(self, ws: WebSocket) -> None:
        """Accept a WS client and send the full index as init."""
        await ws.accept()
        self._clients.add(ws)
        logger.info("WS client connected (%d total)", len(self._clients))
        # Send full current state
        msg = {
            "type": "init",
            "roots": {
                "": {
                    "movies": dict(self.movies),
                    "series": dict(self.series),
                    "people": dict(self.people),
                }
            },
        }
        await ws.send_bytes(msgspec.json.encode(msg))

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a WS client."""
        self._clients.discard(ws)
        logger.info("WS client disconnected (%d remaining)", len(self._clients))

    def _broadcast(self, msg: object) -> None:
        """Broadcast a message to all connected WS clients (non-blocking)."""
        for listener in tuple(self._listeners):
            try:
                listener(msg)
            except Exception:
                logger.exception("IndexStore listener failed")

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
