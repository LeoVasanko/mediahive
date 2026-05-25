"""Scan orchestration — background tasks for continuous media scanning.

All scanning logic lives here in hivescan.  Communication with the mediahive
server happens exclusively through an async ``send`` callable that pushes
:class:`~mediahive.models.events.ScanEvent` messages (``Upsert`` /
``Task``) onto an :class:`asyncio.Queue` owned by the caller.

The scanner recursively walks the media root, respecting ignore patterns
defined in ``.mediahive/scanignore`` (gitignore-style syntax).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiopathlib import AsyncPath

from mediahive.hivescan.indexer import _process_movies, _process_series
from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.parsing import parse_download
from mediahive.hivescan.scanignore import ScanIgnore
from mediahive.hivescan.scanning import categorize_downloads
from mediahive.hivescan.showreel import (
    episode_reel_exists,
    generate_episode_reel,
    generate_showreel_images,
    get_existing_episode_reel_sources,
    get_existing_showreel_source_sets,
    movie_showreels_exist,
)
from mediahive.hivescan.tmdb_client import set_cache_dir
from mediahive.hivescan.utils import (
    DEFAULT_OUTPUT_FOLDER,
    make_relative_path,
)
from mediahive.models.data import Movie, Series, TaskInfo
from mediahive.models.events import ScanEvent, Task, Upsert

logger = logging.getLogger("hivescan.scanner")

# Type alias for the send callable
Send = Callable[[ScanEvent], Awaitable[None]]


# ---------------------------------------------------------------------------
# RootScanner — per-root scanning runtime
# ---------------------------------------------------------------------------


class RootScanner:
    """Scanner instance for a single media root."""

    def __init__(
        self,
        root_id: str,
        media_root: Path,
        send: Send,
    ) -> None:
        self.root_id = root_id
        self.media_root = media_root
        self._send = send
        self._output_dir = media_root / DEFAULT_OUTPUT_FOLDER
        self._scanignore = ScanIgnore(media_root)

        # Runtime state
        self._scan_task: asyncio.Task | None = None
        self._showreel_queue: asyncio.Queue = asyncio.Queue()
        self._showreel_worker_task: asyncio.Task | None = None
        self._rescan_worker_task: asyncio.Task | None = None
        self._seen_mtimes: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise and start background workers."""
        await AsyncPath(self._output_dir).mkdir(parents=True, exist_ok=True)
        set_cache_dir(self._output_dir / ".tmdb-cache")

        logger.info(
            "Scanner started for root %s — path=%s, scanignore=%s",
            self.root_id,
            self.media_root,
            "loaded" if self._scanignore.file_path.exists() else "defaults only",
        )

        self._showreel_worker_task = asyncio.create_task(self._showreel_worker())
        self._rescan_worker_task = asyncio.create_task(self._rescan_loop())

    async def stop(self) -> None:
        """Cancel all background tasks."""
        for task in (
            self._scan_task,
            self._showreel_worker_task,
            self._rescan_worker_task,
        ):
            if task and not task.done():
                task.cancel()
        # Wait briefly for graceful shutdown
        for task in (
            self._scan_task,
            self._showreel_worker_task,
            self._rescan_worker_task,
        ):
            if task and not task.done():
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2.0)

    def is_scanning(self) -> bool:
        return self._scan_task is not None and not self._scan_task.done()

    def showreel_queue_size(self) -> int:
        return self._showreel_queue.qsize()

    def trigger_scan(self) -> bool:
        """Start a scan. Returns False if one is already running."""
        if self.is_scanning():
            return False
        self._start_scan()
        return True

    # ------------------------------------------------------------------
    # Internal scan orchestration
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        self._scan_task = asyncio.create_task(self._run_scan())

    async def _rescan_loop(self) -> None:
        try:
            while True:
                self._start_scan()
                if self._scan_task:
                    await self._scan_task
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Rescan loop error")

    async def _discover_downloads(self, task_id: str) -> list[ParsedContent]:
        """Recursively walk the media root, respecting scanignore rules."""
        downloads: list[ParsedContent] = []
        media_root_str = self.media_root.as_posix()
        dirs_visited = 0

        async def _report(detail: str) -> None:
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=detail,
                    )
                )
            )

        media_container_dirs = {"BDMV", "VIDEO_TS", "HVDVD_TS"}
        video_extensions = {
            ".mkv",
            ".mp4",
            ".avi",
            ".m4v",
            ".mov",
            ".wmv",
            ".flv",
            ".webm",
            ".ts",
            ".m2ts",
        }

        async def _walk(directory: Path) -> None:
            nonlocal dirs_visited
            ap = AsyncPath(directory)
            if not await ap.is_dir():
                return

            child_dirs: list[Path] = []
            child_files: list[Path] = []
            is_media_container = False

            try:
                entries = await asyncio.to_thread(lambda: list(ap.iterdir()))
                for item_async in entries:
                    item = Path(item_async)
                    if item.name.startswith("."):
                        continue
                    if self._scanignore and self._scanignore.is_excluded(item):
                        continue

                    if await AsyncPath(item).is_dir():
                        if item.name.upper() in media_container_dirs:
                            is_media_container = True
                        child_dirs.append(item)
                    else:
                        child_files.append(item)
            except OSError, PermissionError:
                logger.debug("Cannot list directory: %s", directory)
                return

            if is_media_container:
                relpath = make_relative_path(str(directory), media_root_str)
                try:
                    stat_info = await ap.stat()
                    mtime = int(stat_info.st_mtime)
                except OSError:
                    return
                if (
                    relpath not in self._seen_mtimes
                    or self._seen_mtimes[relpath] != mtime
                ):
                    self._seen_mtimes[relpath] = mtime
                    downloads.append(await parse_download(directory))
                return

            if child_dirs:
                dirs_visited += 1
                rel = make_relative_path(str(directory), media_root_str) or str(
                    directory
                )
                if dirs_visited % 5 == 1:
                    await _report(f"Scanning: {rel} ({len(downloads)} found)")
                    logger.info("Scanning: %s (%d found so far)", rel, len(downloads))
                for child in child_dirs:
                    await _walk(child)
                    await asyncio.sleep(0)
                for child_file in child_files:
                    if child_file.suffix.lower() in video_extensions:
                        relpath = make_relative_path(str(child_file), media_root_str)
                        try:
                            stat_info = await AsyncPath(child_file).stat()
                            mtime = int(stat_info.st_mtime)
                        except OSError:
                            continue
                        if (
                            relpath in self._seen_mtimes
                            and self._seen_mtimes[relpath] == mtime
                        ):
                            continue
                        self._seen_mtimes[relpath] = mtime
                        downloads.append(await parse_download(child_file))
            else:
                relpath = make_relative_path(str(directory), media_root_str)
                try:
                    stat_info = await ap.stat()
                    mtime = int(stat_info.st_mtime)
                except OSError:
                    return
                if relpath in self._seen_mtimes and self._seen_mtimes[relpath] == mtime:
                    return
                self._seen_mtimes[relpath] = mtime
                downloads.append(await parse_download(directory))

        logger.info("Starting filesystem discovery at %s", self.media_root)
        await _report(f"Scanning: {self.media_root}")

        root_ap = AsyncPath(self.media_root)
        try:
            root_children = await asyncio.to_thread(lambda: list(root_ap.iterdir()))
        except OSError, PermissionError:
            logger.exception("Cannot list media root: %s", self.media_root)
            return downloads

        for item_async in root_children:
            item = Path(item_async)
            if item.name.startswith("."):
                continue
            if self._scanignore and self._scanignore.is_excluded(item):
                logger.debug("Excluded: %s", item.name)
                continue
            if await AsyncPath(item).is_dir():
                await _walk(item)
            else:
                relpath = make_relative_path(str(item), media_root_str)
                try:
                    stat_info = await AsyncPath(item).stat()
                    mtime = int(stat_info.st_mtime)
                except OSError:
                    continue
                if relpath in self._seen_mtimes and self._seen_mtimes[relpath] == mtime:
                    continue
                self._seen_mtimes[relpath] = mtime
                downloads.append(await parse_download(item))

        logger.info(
            "Discovery complete: %d downloads found, %d directories visited",
            len(downloads),
            dirs_visited,
        )
        return downloads

    async def _run_scan(self) -> None:
        """Full scan pipeline:

        1. Discover downloads
        2. Categorise → movies / series
        3. Iterate async generators, send each item as Upsert
        4. Queue showreel tasks.
        """
        task_id = f"scan-{uuid.uuid4().hex[:8]}"
        media_root_str = self.media_root.as_posix()

        try:
            logger.info("Scan started (%s) for root %s", task_id, self.root_id)
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail="Starting scan...",
                    )
                )
            )

            downloads = await self._discover_downloads(task_id)

            if not downloads:
                logger.info("No new downloads found (%s)", task_id)
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="completed",
                            progress=1,
                            detail="No new items",
                        )
                    )
                )
                return

            logger.info("Found %d items to process", len(downloads))
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=f"Processing {len(downloads)} items...",
                    )
                )
            )

            categories = categorize_downloads(downloads)
            n_movies = len(categories[ContentType.MOVIE])
            n_series = len(categories[ContentType.SERIES])
            total = n_movies + n_series
            processed = 0

            logger.info("Categorised: %d movies, %d series", n_movies, n_series)

            # Process movies
            if n_movies:
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=0,
                            detail=f"Processing {n_movies} movies...",
                        )
                    )
                )
            async for movie, showreel_task in _process_movies(
                categories,
                self._output_dir,
                fetch_covers=True,
                generate_showreels=True,
                media_root=media_root_str,
                root_id=self.root_id,
            ):
                await self._send(Upsert(kind="movie", item=movie))
                if showreel_task:
                    await self._showreel_queue.put(("movie", showreel_task, movie))
                    logger.info(
                        "[%d/%d] Movie: %s (showreel queued, queue=%d)",
                        processed + 1,
                        total,
                        movie.title,
                        self._showreel_queue.qsize(),
                    )
                else:
                    logger.info(
                        "[%d/%d] Movie: %s (no showreel task)",
                        processed + 1,
                        total,
                        movie.title,
                    )
                processed += 1
                progress = round(processed / total, 3) if total else 1
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=progress,
                            detail=movie.title,
                        )
                    )
                )

            # Process series
            if n_series:
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=processed / total if total else 0.5,
                            detail=f"Processing {n_series} series...",
                        )
                    )
                )
            async for series, ep_reel_tasks in _process_series(
                categories,
                self._output_dir,
                fetch_covers=True,
                generate_showreels=True,
                media_root=media_root_str,
                root_id=self.root_id,
            ):
                await self._send(Upsert(kind="series", item=series))
                for task in ep_reel_tasks:
                    await self._showreel_queue.put(("episode", task, series))
                if ep_reel_tasks:
                    logger.info(
                        "[%d/%d] Series: %s (%d episode reels queued, queue=%d)",
                        processed + 1,
                        total,
                        series.title,
                        len(ep_reel_tasks),
                        self._showreel_queue.qsize(),
                    )
                else:
                    logger.info(
                        "[%d/%d] Series: %s (no reel tasks)",
                        processed + 1,
                        total,
                        series.title,
                    )
                processed += 1
                progress = round(processed / total, 3) if total else 1
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=progress,
                            detail=series.title,
                        )
                    )
                )

            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="completed",
                        progress=1,
                        detail=f"Done — {n_movies} movies, {n_series} series",
                    )
                )
            )
            logger.info(
                "Scan complete (%s): %d movies, %d series, showreel queue=%d",
                task_id,
                n_movies,
                n_series,
                self._showreel_queue.qsize(),
            )

        except asyncio.CancelledError:
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="cancelled",
                        progress=0,
                        detail="Scan cancelled",
                    )
                )
            )
            logger.info("Scan cancelled (%s)", task_id)
        except Exception:
            logger.exception("Scan failed (%s)", task_id)
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="error",
                        progress=0,
                        detail="Scan error",
                    )
                )
            )

    # ------------------------------------------------------------------
    # Showreel worker
    # ------------------------------------------------------------------

    async def _showreel_worker(self) -> None:
        """Background worker that generates showreels one at a time."""
        logger.info("Showreel worker started for root %s", self.root_id)
        media_root_path = self.media_root
        media_root_str = self.media_root.as_posix()

        while True:
            try:
                kind, task_data, item = await self._showreel_queue.get()
                task_id = f"showreel-{uuid.uuid4().hex[:8]}"
                remaining = self._showreel_queue.qsize()

                if kind == "movie":
                    movie: Movie = item
                    video_path, media_folder, title = task_data
                    logger.info(
                        "Showreel dequeued: %s (video=%s, folder=%s, %d remaining)",
                        title,
                        video_path,
                        media_folder,
                        remaining,
                    )
                    if await movie_showreels_exist(media_folder):
                        logger.info("Showreel skipped (already exists): %s", title)
                        self._showreel_queue.task_done()
                        continue
                    await self._send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="running",
                                progress=0,
                                detail=f"Showreel: {title}",
                            )
                        )
                    )
                    generated = await generate_showreel_images(
                        video_path,
                        media_folder,
                        title=title,
                    )
                    if generated:
                        source_sets = get_existing_showreel_source_sets(
                            media_folder, media_root=media_root_path
                        )
                        paths = [sources[0] for sources in source_sets if sources]
                        movie.showreel_images = paths or None
                        movie.showreel_source_sets = source_sets or None
                        await self._send(Upsert(kind="movie", item=movie))
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="completed",
                                    progress=1,
                                    detail=(
                                        f"Showreel: {title} ({len(generated)} reels)"
                                    ),
                                )
                            )
                        )
                    else:
                        logger.warning(
                            "Showreel generation returned nothing: %s", title
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="error",
                                    progress=0,
                                    detail=f"Showreel failed: {title}",
                                )
                            )
                        )

                elif kind == "episode":
                    series: Series = item
                    video_path, media_folder, season_num, episode_num, series_title = (
                        task_data
                    )
                    ep_code = f"S{season_num:02d}E{episode_num:02d}"
                    logger.info(
                        "Episode reel dequeued: %s %s (video=%s, %d remaining)",
                        series_title,
                        ep_code,
                        video_path,
                        remaining,
                    )
                    if await episode_reel_exists(media_folder, season_num, episode_num):
                        logger.info(
                            "Episode reel skipped (already exists): %s %s",
                            series_title,
                            ep_code,
                        )
                        self._showreel_queue.task_done()
                        continue
                    await self._send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="running",
                                progress=0,
                                detail=f"Reel: {series_title} {ep_code}",
                            )
                        )
                    )
                    reel_path = await generate_episode_reel(
                        video_path,
                        media_folder,
                        season_num,
                        episode_num,
                    )
                    if reel_path:
                        reel_sources = get_existing_episode_reel_sources(
                            media_folder,
                            season_num,
                            episode_num,
                            media_root=media_root_path,
                        )
                        for season in series.seasons:
                            if season.season_number == season_num:
                                for episode in season.episodes:
                                    if episode.episode_number == episode_num:
                                        episode.reel_image = (
                                            reel_sources[0]
                                            if reel_sources
                                            else make_relative_path(
                                                reel_path,
                                                media_root_str,
                                            )
                                        )
                                        episode.reel_sources = reel_sources or None
                        await self._send(Upsert(kind="series", item=series))
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="completed",
                                    progress=1,
                                    detail=f"Reel: {series_title} {ep_code}",
                                )
                            )
                        )
                    else:
                        logger.warning(
                            "Episode reel generation failed: %s %s",
                            series_title,
                            ep_code,
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="error",
                                    progress=0,
                                    detail=f"Reel failed: {series_title} {ep_code}",
                                )
                            )
                        )

                self._showreel_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Showreel worker shutting down for root %s", self.root_id)
                return
            except Exception:
                logger.exception(
                    "Showreel worker error (queue size=%d)",
                    self._showreel_queue.qsize(),
                )
                with contextlib.suppress(ValueError):
                    self._showreel_queue.task_done()


# ---------------------------------------------------------------------------
# Legacy module-level API removed — use RootScanner per root instead.
# ---------------------------------------------------------------------------
