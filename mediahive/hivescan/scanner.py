"""
Scan orchestration — background tasks for continuous media scanning.

All scanning logic lives here in hivescan.  Communication with the mediahive
server happens exclusively through an async ``send`` callable that pushes
:class:`~mediahive.models.events.ScanEvent` messages (``Upsert`` /
``Task``) onto an :class:`asyncio.Queue` owned by the caller.

The scanner recursively walks the media root, respecting ignore patterns
defined in ``.mediahive/scanignore`` (gitignore-style syntax).
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

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
    get_expected_showreel_paths,
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
# Configuration (populated by ``start``)
# ---------------------------------------------------------------------------

_output_dir: Optional[Path] = None
_media_root: Optional[Path] = None
_scanignore: Optional[ScanIgnore] = None

# Runtime state
_send: Optional[Send] = None
_scan_task: Optional[asyncio.Task] = None
_showreel_queue: asyncio.Queue = asyncio.Queue()
_showreel_worker_task: Optional[asyncio.Task] = None
_rescan_worker_task: Optional[asyncio.Task] = None
_seen_mtimes: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Public lifecycle
# ---------------------------------------------------------------------------


async def start(send: Send) -> None:
    """
    Initialise and start the scanner.

    Reads ``MEDIAHIVE_PATH`` from the environment, loads the scanignore
    rules from ``.mediahive/scanignore``, and starts background workers
    that recursively walk the media root.
    """
    global _send, _output_dir, _media_root, _scanignore
    global _showreel_worker_task, _rescan_worker_task

    _send = send

    media_path = os.environ.get("MEDIAHIVE_PATH", "")
    if not media_path:
        logger.error("MEDIAHIVE_PATH environment variable must be set")
        return

    _media_root = Path(media_path).resolve()
    _output_dir = _media_root / DEFAULT_OUTPUT_FOLDER
    _scanignore = ScanIgnore(_media_root)

    await AsyncPath(_output_dir).mkdir(parents=True, exist_ok=True)
    set_cache_dir(_output_dir / ".tmdb-cache")

    logger.info(
        "Scanner started — root=%s, scanignore=%s",
        _media_root,
        "loaded" if _scanignore.file_path.exists() else "defaults only",
    )

    _showreel_worker_task = asyncio.create_task(_showreel_worker())
    _rescan_worker_task = asyncio.create_task(_rescan_loop())


async def stop() -> None:
    """Cancel all background tasks."""
    for task in (_scan_task, _showreel_worker_task, _rescan_worker_task):
        if task and not task.done():
            task.cancel()


def is_scanning() -> bool:
    return _scan_task is not None and not _scan_task.done()


def showreel_queue_size() -> int:
    return _showreel_queue.qsize()


def trigger_scan() -> bool:
    """Start a scan. Returns False if one is already running."""
    if is_scanning():
        return False
    _start_scan()
    return True


# ---------------------------------------------------------------------------
# Internal scan orchestration
# ---------------------------------------------------------------------------


def _start_scan():
    global _scan_task
    _scan_task = asyncio.create_task(_run_scan())


async def _rescan_loop():
    try:
        while True:
            _start_scan()
            if _scan_task:
                await _scan_task
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Rescan loop error")


async def _discover_downloads(task_id: str) -> List[ParsedContent]:
    """Recursively walk the media root, respecting scanignore rules.

    Each non-ignored **leaf directory** (a directory whose children are only
    files, i.e. a single download/torrent folder) and each non-ignored
    top-level file is treated as a download to parse.

    Sends live task progress so the user can see which directories are being
    explored and how many items have been found so far.
    """
    downloads: List[ParsedContent] = []
    media_root_str = str(_media_root) if _media_root else None
    dirs_visited = 0

    async def _report(detail: str) -> None:
        await _send(
            Task(
                data=TaskInfo(
                    id=task_id,
                    status="running",
                    progress=0,
                    detail=detail,
                )
            )
        )

    # Media container folder names (indicates the parent is a single media item)
    MEDIA_CONTAINER_DIRS = {"BDMV", "VIDEO_TS", "HVDVD_TS"}
    VIDEO_EXTENSIONS = {
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
            for item_async in ap.iterdir():
                item = Path(item_async)
                if item.name.startswith("."):
                    continue
                if _scanignore and _scanignore.is_excluded(item):
                    continue

                if await AsyncPath(item).is_dir():
                    # Check if this is a BluRay/DVD structure
                    if item.name.upper() in MEDIA_CONTAINER_DIRS:
                        is_media_container = True
                    child_dirs.append(item)
                else:
                    child_files.append(item)
        except OSError, PermissionError:
            logger.debug("Cannot list directory: %s", directory)
            return

        # If directory contains BDMV/VIDEO_TS, treat entire directory as a single download
        if is_media_container:
            relpath = make_relative_path(str(directory), media_root_str)
            try:
                stat_info = await ap.stat()
                mtime = int(stat_info.st_mtime)
            except OSError:
                return
            if relpath not in _seen_mtimes or _seen_mtimes[relpath] != mtime:
                _seen_mtimes[relpath] = mtime
                downloads.append(await parse_download(directory))
            return

        if child_dirs:
            # Branch directory — log it and recurse into subdirectories
            dirs_visited += 1
            rel = make_relative_path(str(directory), media_root_str) or str(directory)
            if dirs_visited % 5 == 1:  # throttle progress updates
                await _report(f"Scanning: {rel} ({len(downloads)} found)")
                logger.info("Scanning: %s (%d found so far)", rel, len(downloads))
            for child in child_dirs:
                await _walk(child)
                # Yield control periodically so WS messages flush
                await asyncio.sleep(0)
            # Also process any video files directly in this directory
            for child_file in child_files:
                if child_file.suffix.lower() in VIDEO_EXTENSIONS:
                    relpath = make_relative_path(str(child_file), media_root_str)
                    try:
                        stat_info = await AsyncPath(child_file).stat()
                        mtime = int(stat_info.st_mtime)
                    except OSError:
                        continue
                    if relpath in _seen_mtimes and _seen_mtimes[relpath] == mtime:
                        continue
                    _seen_mtimes[relpath] = mtime
                    downloads.append(await parse_download(child_file))
        else:
            # Leaf directory — treat the directory itself as a download
            relpath = make_relative_path(str(directory), media_root_str)
            try:
                stat_info = await ap.stat()
                mtime = int(stat_info.st_mtime)
            except OSError:
                return
            if relpath in _seen_mtimes and _seen_mtimes[relpath] == mtime:
                return
            _seen_mtimes[relpath] = mtime
            downloads.append(await parse_download(directory))

    # Walk immediate children of the media root (skip root itself)
    logger.info("Starting filesystem discovery at %s", _media_root)
    await _report(f"Scanning: {_media_root}")

    root_ap = AsyncPath(_media_root)
    try:
        root_children = list(root_ap.iterdir())
    except OSError, PermissionError:
        logger.error("Cannot list media root: %s", _media_root)
        return downloads

    for item_async in root_children:
        item = Path(item_async)
        if item.name.startswith("."):
            continue
        if _scanignore and _scanignore.is_excluded(item):
            logger.debug("Excluded: %s", item.name)
            continue
        if await AsyncPath(item).is_dir():
            await _walk(item)
        else:
            # Top-level file — parse directly
            relpath = make_relative_path(str(item), media_root_str)
            try:
                stat_info = await AsyncPath(item).stat()
                mtime = int(stat_info.st_mtime)
            except OSError:
                continue
            if relpath in _seen_mtimes and _seen_mtimes[relpath] == mtime:
                continue
            _seen_mtimes[relpath] = mtime
            downloads.append(await parse_download(item))

    logger.info(
        "Discovery complete: %d downloads found, %d directories visited",
        len(downloads),
        dirs_visited,
    )
    return downloads


async def _run_scan():
    """
    Full scan pipeline:
      1. Recursively walk media root (respecting scanignore) — with live progress
      2. Categorise → movies / series
      3. Iterate async generators, send each item as Upsert
      4. Queue showreel tasks
    """
    task_id = f"scan-{uuid.uuid4().hex[:8]}"
    media_root_str = str(_media_root) if _media_root else None

    try:
        logger.info("Scan started (%s)", task_id)
        await _send(
            Task(
                data=TaskInfo(
                    id=task_id,
                    status="running",
                    progress=0,
                    detail="Starting scan...",
                )
            )
        )

        downloads = await _discover_downloads(task_id)

        if not downloads:
            logger.info("No new downloads found (%s)", task_id)
            await _send(
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
        await _send(
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
            await _send(
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
            _output_dir,
            fetch_covers=True,
            generate_showreels=True,
            media_root=media_root_str,
        ):
            await _send(Upsert(kind="movie", item=movie))
            if showreel_task:
                await _showreel_queue.put(("movie", showreel_task, movie))
                logger.info(
                    "[%d/%d] Movie: %s (showreel queued, queue=%d)",
                    processed + 1,
                    total,
                    movie.title,
                    _showreel_queue.qsize(),
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
            await _send(
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
            await _send(
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
            _output_dir,
            fetch_covers=True,
            generate_showreels=True,
            media_root=media_root_str,
        ):
            await _send(Upsert(kind="series", item=series))
            for task in ep_reel_tasks:
                await _showreel_queue.put(("episode", task, series))
            if ep_reel_tasks:
                logger.info(
                    "[%d/%d] Series: %s (%d episode reels queued, queue=%d)",
                    processed + 1,
                    total,
                    series.title,
                    len(ep_reel_tasks),
                    _showreel_queue.qsize(),
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
            await _send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=progress,
                        detail=series.title,
                    )
                )
            )

        await _send(
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
            _showreel_queue.qsize(),
        )

    except asyncio.CancelledError:
        await _send(
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
        await _send(
            Task(
                data=TaskInfo(
                    id=task_id,
                    status="error",
                    progress=0,
                    detail="Scan error",
                )
            )
        )


# ---------------------------------------------------------------------------
# Showreel worker
# ---------------------------------------------------------------------------


async def _showreel_worker():
    """Background worker that generates showreels one at a time."""
    logger.info("Showreel worker started")
    media_root_path = Path(_media_root) if _media_root else None
    media_root_str = str(_media_root) if _media_root else None

    while True:
        try:
            kind, task_data, item = await _showreel_queue.get()
            task_id = f"showreel-{uuid.uuid4().hex[:8]}"
            remaining = _showreel_queue.qsize()

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
                    _showreel_queue.task_done()
                    continue
                await _send(
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
                    paths = get_expected_showreel_paths(
                        media_folder, media_root=media_root_path
                    )
                    movie.showreel_images = paths if paths else None
                    await _send(Upsert(kind="movie", item=movie))
                    await _send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="completed",
                                progress=1,
                                detail=f"Showreel: {title} ({len(generated)} reels)",
                            )
                        )
                    )
                else:
                    logger.warning("Showreel generation returned nothing: %s", title)
                    await _send(
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
                    _showreel_queue.task_done()
                    continue
                await _send(
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
                    for season in series.seasons:
                        if season.season_number == season_num:
                            for episode in season.episodes:
                                if episode.episode_number == episode_num:
                                    episode.reel_image = make_relative_path(
                                        reel_path,
                                        media_root_str,
                                    )
                    await _send(Upsert(kind="series", item=series))
                    await _send(
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
                    await _send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="error",
                                progress=0,
                                detail=f"Reel failed: {series_title} {ep_code}",
                            )
                        )
                    )

            _showreel_queue.task_done()

        except asyncio.CancelledError:
            logger.info("Showreel worker shutting down")
            return
        except Exception:
            logger.exception(
                "Showreel worker error (queue size=%d)", _showreel_queue.qsize()
            )
            try:
                _showreel_queue.task_done()
            except ValueError:
                pass
