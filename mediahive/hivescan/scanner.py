"""
Scan orchestration — background tasks for continuous media scanning.

All scanning logic lives here in hivescan.  Communication with the mediahive
server happens exclusively through an async ``send`` callable that pushes
:class:`~mediahive.models.events.ScanEvent` messages (``Upsert`` /
``Task``) onto an :class:`asyncio.Queue` owned by the caller.
"""

import asyncio
import glob
import logging
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from aiopathlib import AsyncPath

from mediahive.hivescan.indexer import _process_movies, _process_series
from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.parsing import parse_download
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
    find_common_root,
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

_scan_paths: List[str] = []
_output_dir: Optional[Path] = None
_media_root: Optional[Path] = None

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

    Reads ``HIVESCAN_PATHS`` / ``HIVESCAN_OUTPUT`` from the environment,
    expands globs, sets up the TMDb cache, and starts background workers.
    """
    global _send, _output_dir, _media_root, _scan_paths
    global _showreel_worker_task, _rescan_worker_task

    _send = send

    raw_paths = os.environ.get("HIVESCAN_PATHS", "")
    if not raw_paths:
        logger.error("HIVESCAN_PATHS environment variable must be set")
        return

    all_paths: List[Path] = []
    for pattern in raw_paths.split(os.pathsep):
        pattern = pattern.strip()
        if not pattern:
            continue
        expanded = await asyncio.to_thread(glob.glob, pattern)
        if expanded:
            all_paths.extend(Path(p) for p in expanded)
        else:
            all_paths.append(Path(pattern))

    _scan_paths = [str(p) for p in all_paths]

    if os.environ.get("HIVESCAN_OUTPUT"):
        _output_dir = Path(os.environ["HIVESCAN_OUTPUT"])
        _media_root = _output_dir.parent
    else:
        _media_root = await find_common_root(all_paths)
        if _media_root is None:
            logger.error("Cannot determine common root; set HIVESCAN_OUTPUT")
            return
        _output_dir = _media_root / DEFAULT_OUTPUT_FOLDER

    await AsyncPath(_output_dir).mkdir(parents=True, exist_ok=True)
    set_cache_dir(_output_dir / ".tmdb-cache")

    logger.info(
        "Scanner started — %d scan paths, output=%s",
        len(_scan_paths),
        _output_dir,
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


def trigger_scan(paths: Optional[List[str]] = None) -> bool:
    """Start a scan. Returns False if one is already running."""
    if is_scanning():
        return False
    _start_scan(paths)
    return True


# ---------------------------------------------------------------------------
# Internal scan orchestration
# ---------------------------------------------------------------------------


def _start_scan(paths: Optional[List[str]] = None):
    global _scan_task
    _scan_task = asyncio.create_task(_run_scan(paths))


async def _rescan_loop():
    try:
        while True:
            _start_scan()
            if _scan_task:
                await _scan_task
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Rescan loop error")


async def _discover_downloads(paths_to_scan: List[str]) -> List[ParsedContent]:
    """Walk the filesystem and parse all downloads, skipping unchanged torrents."""
    downloads: List[ParsedContent] = []
    media_root_str = str(_media_root) if _media_root else None
    for pattern in paths_to_scan:
        p = Path(pattern)
        ap = AsyncPath(p)
        if await ap.is_dir():
            for item in ap.iterdir():
                if not Path(item).name.startswith("."):
                    relpath = make_relative_path(str(item), media_root_str)
                    stat_info = await AsyncPath(item).stat()
                    mtime = int(stat_info.st_mtime)
                    if relpath in _seen_mtimes and _seen_mtimes[relpath] == mtime:
                        continue
                    _seen_mtimes[relpath] = mtime
                    downloads.append(await parse_download(Path(item)))
        elif await ap.exists():
            relpath = make_relative_path(str(p), media_root_str)
            stat_info = await ap.stat()
            mtime = int(stat_info.st_mtime)
            if relpath in _seen_mtimes and _seen_mtimes[relpath] == mtime:
                continue
            _seen_mtimes[relpath] = mtime
            downloads.append(await parse_download(p))
    return downloads


async def _run_scan(override_paths: Optional[List[str]] = None):
    """
    Full scan pipeline:
      1. Walk filesystem, parse torrents
      2. Categorise → movies / series
      3. Iterate async generators, send each item as Upsert
      4. Queue showreel tasks
    """
    task_id = f"scan-{uuid.uuid4().hex[:8]}"
    paths_to_scan = override_paths or _scan_paths
    media_root_str = str(_media_root) if _media_root else None

    try:
        downloads = await _discover_downloads(paths_to_scan)

        if downloads:
            logger.info("Scan started (%s)", task_id)
            await _send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail="Scanning filesystem...",
                    )
                )
            )
            logger.info("Found %d items to process", len(downloads))

        categories = categorize_downloads(downloads)
        total = len(categories[ContentType.MOVIE]) + len(categories[ContentType.SERIES])
        processed = 0

        # Process movies
        await _send(
            Task(
                data=TaskInfo(
                    id=task_id,
                    status="running",
                    progress=0,
                    detail="Processing movies...",
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
        await _send(
            Task(
                data=TaskInfo(
                    id=task_id,
                    status="running",
                    progress=processed / total if total else 0.5,
                    detail="Processing series...",
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
                    detail="Scan complete",
                )
            )
        )
        if downloads:
            logger.info("Scan complete (%s)", task_id)

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

            if kind == "movie":
                movie: Movie = item
                video_path, media_folder, title = task_data
                if await movie_showreels_exist(media_folder):
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
                await generate_showreel_images(video_path, media_folder, title=title)
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
                            detail=f"Showreel: {title}",
                        )
                    )
                )

            elif kind == "episode":
                series: Series = item
                video_path, media_folder, season_num, episode_num, series_title = (
                    task_data
                )
                if await episode_reel_exists(media_folder, season_num, episode_num):
                    _showreel_queue.task_done()
                    continue
                ep_code = f"S{season_num:02d}E{episode_num:02d}"
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

            _showreel_queue.task_done()

        except asyncio.CancelledError:
            logger.info("Showreel worker shutting down")
            return
        except Exception:
            logger.exception("Showreel worker error")
            try:
                _showreel_queue.task_done()
            except ValueError:
                pass
