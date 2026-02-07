"""
Hivescan FastAPI server — scanning, TMDb lookups, showreel generation.

Exposes:
  GET  /ws          — WebSocket for live index updates & task progress
  POST /api/scan    — trigger a new scan
  GET  /api/status  — current server status
  GET  /api/index   — full index as JSON (HTTP fallback)
"""

import asyncio
import glob
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import msgspec
from hivescan.index_store import IndexStore
from hivescan.indexer import _process_movies, _process_series
from hivescan.structs import MsgspecResponse, ScanRequest, StatusResponse, TaskInfo
from hivescan.models import ContentType, ParsedContent
from hivescan.parsing import parse_download
from hivescan.scanning import categorize_downloads
from hivescan.showreel import (
    generate_episode_reel,
    generate_showreel_images,
    episode_reel_exists,
    movie_showreels_exist,
)
from hivescan.tmdb_client import set_cache_dir
from hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root, make_relative_path

logger = logging.getLogger("hivescan.server")

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

SCAN_PATHS: List[str] = []  # set in lifespan from HIVESCAN_PATHS
OUTPUT_DIR: Optional[Path] = None  # .mediahive folder
MEDIA_ROOT: Optional[Path] = None  # parent of OUTPUT_DIR

# Global state
store: Optional[IndexStore] = None
_scan_task: Optional[asyncio.Task] = None
_showreel_queue: asyncio.Queue = asyncio.Queue()
_showreel_worker_task: Optional[asyncio.Task] = None
_rescan_worker_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, OUTPUT_DIR, MEDIA_ROOT, SCAN_PATHS, _showreel_worker_task

    # Parse configuration
    raw_paths = os.environ.get("HIVESCAN_PATHS", "")
    if not raw_paths:
        logger.error("HIVESCAN_PATHS environment variable must be set")
        sys.exit(1)

    # Expand globs
    all_paths: List[Path] = []
    for pattern in raw_paths.split(os.pathsep):
        pattern = pattern.strip()
        if not pattern:
            continue
        expanded = glob.glob(pattern)
        if expanded:
            all_paths.extend(Path(p) for p in expanded)
        else:
            all_paths.append(Path(pattern))

    SCAN_PATHS = [str(p) for p in all_paths]

    if os.environ.get("HIVESCAN_OUTPUT"):
        OUTPUT_DIR = Path(os.environ["HIVESCAN_OUTPUT"])
        MEDIA_ROOT = OUTPUT_DIR.parent
    else:
        MEDIA_ROOT = find_common_root(all_paths)
        if MEDIA_ROOT is None:
            logger.error("Cannot determine common root; set HIVESCAN_OUTPUT")
            sys.exit(1)
        OUTPUT_DIR = MEDIA_ROOT / DEFAULT_OUTPUT_FOLDER

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_cache_dir(OUTPUT_DIR / ".tmdb-cache")

    # Initialise index store and load snapshot
    store = IndexStore(OUTPUT_DIR / "index.json", media_root=str(MEDIA_ROOT))
    store.load_snapshot()
    logger.info(
        "Index store ready: %d movies, %d series (from snapshot)",
        len(store.movies),
        len(store.series),
    )

    # Start showreel worker
    _showreel_worker_task = asyncio.create_task(_showreel_worker())

    # Start scan loop (initial scan + periodic rescans)
    _rescan_worker_task = asyncio.create_task(_rescan_loop())

    yield

    # Shutdown — cancel running tasks, flush snapshot
    if _scan_task and not _scan_task.done():
        _scan_task.cancel()
    if _showreel_worker_task and not _showreel_worker_task.done():
        _showreel_worker_task.cancel()
    if _rescan_worker_task and not _rescan_worker_task.done():
        _rescan_worker_task.cancel()
    await store.flush_snapshot()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Hivescan Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Live index updates and task progress."""
    await store.connect(ws)
    try:
        while True:
            # Keep connection alive; ignore client messages for now
            await ws.receive_text()
    except WebSocketDisconnect:
        store.disconnect(ws)
    except Exception:
        store.disconnect(ws)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.post("/api/scan")
async def trigger_scan(request: Request):
    """Trigger a new scan. If a scan is already running, returns 409."""
    if _scan_task and not _scan_task.done():
        return {"status": "already_running"}
    body_bytes = await request.body()
    req = (
        msgspec.json.decode(body_bytes, type=ScanRequest)
        if body_bytes
        else ScanRequest()
    )
    override = req.paths if req.paths else None
    _start_scan(override)
    return {"status": "started"}


@app.get("/api/status")
async def server_status():
    """Return current server status."""
    scanning = _scan_task is not None and not _scan_task.done()
    return MsgspecResponse(
        StatusResponse(
            scanning=scanning,
            movies=len(store.movies),
            series=len(store.series),
            showreel_queue=_showreel_queue.qsize(),
        )
    )


@app.get("/api/index")
async def get_index():
    """Full index as JSON (HTTP fallback for non-WS clients)."""
    return MsgspecResponse(store.get_full_index())


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------


def _start_scan(paths: Optional[List[str]] = None):
    """Launch a background scan task."""
    global _scan_task
    _scan_task = asyncio.create_task(_run_scan(paths))


async def _rescan_loop():
    """Run scans in a loop with a short sleep between each."""
    try:
        while True:
            _start_scan()
            # Wait for the current scan to finish
            if _scan_task:
                await _scan_task
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Rescan loop error")


async def _run_scan(override_paths: Optional[List[str]] = None):
    """
    Full scan pipeline:
      1. Walk filesystem, parse torrents
      2. Categorise → movies / series
      3. Iterate async generators, upsert each item into IndexStore
      4. Queue showreel tasks
    """
    task_id = f"scan-{uuid.uuid4().hex[:8]}"
    logger.info("Scan started (%s)", task_id)

    store.broadcast_task(
        TaskInfo(
            id=task_id, status="running", progress=0, detail="Scanning filesystem..."
        )
    )

    paths_to_scan = override_paths or SCAN_PATHS
    media_root_str = str(MEDIA_ROOT) if MEDIA_ROOT else None

    try:
        # 1. Discover downloads (sync filesystem walk — fast enough)
        downloads: List[ParsedContent] = []
        for pattern in paths_to_scan:
            p = Path(pattern)
            if p.is_dir():
                for item in p.iterdir():
                    if not item.name.startswith("."):
                        downloads.append(parse_download(item))
            elif p.exists():
                downloads.append(parse_download(p))

        logger.info("Found %d items to process", len(downloads))
        categories = categorize_downloads(downloads)
        total = len(categories[ContentType.MOVIE]) + len(categories[ContentType.SERIES])
        processed = 0

        # 2. Process movies
        store.broadcast_task(
            TaskInfo(
                id=task_id, status="running", progress=0, detail="Processing movies..."
            )
        )
        async for movie, showreel_task in _process_movies(
            categories,
            OUTPUT_DIR,
            fetch_covers=True,
            generate_showreels=True,
            media_root=media_root_str,
        ):
            store.upsert_movie(movie)
            if showreel_task:
                await _showreel_queue.put(("movie", showreel_task, movie.id))
            processed += 1
            progress = processed / total if total else 1
            store.broadcast_task(
                TaskInfo(
                    id=task_id,
                    status="running",
                    progress=round(progress, 3),
                    detail=movie.title,
                )
            )

        # 3. Process series
        store.broadcast_task(
            TaskInfo(
                id=task_id,
                status="running",
                progress=processed / total if total else 0.5,
                detail="Processing series...",
            )
        )
        async for series, ep_reel_tasks in _process_series(
            categories,
            OUTPUT_DIR,
            fetch_covers=True,
            generate_showreels=True,
            media_root=media_root_str,
        ):
            store.upsert_series(series)
            for task in ep_reel_tasks:
                await _showreel_queue.put(("episode", task, series.id))
            processed += 1
            progress = processed / total if total else 1
            store.broadcast_task(
                TaskInfo(
                    id=task_id,
                    status="running",
                    progress=round(progress, 3),
                    detail=series.title,
                )
            )

        store.broadcast_task(
            TaskInfo(id=task_id, status="completed", progress=1, detail="Scan complete")
        )
        logger.info(
            "Scan complete (%s): %d movies, %d series",
            task_id,
            len(store.movies),
            len(store.series),
        )

    except asyncio.CancelledError:
        store.broadcast_task(
            TaskInfo(
                id=task_id, status="cancelled", progress=0, detail="Scan cancelled"
            )
        )
        logger.info("Scan cancelled (%s)", task_id)
    except Exception:
        logger.exception("Scan failed (%s)", task_id)
        store.broadcast_task(
            TaskInfo(id=task_id, status="error", progress=0, detail="Scan error")
        )


# ---------------------------------------------------------------------------
# Showreel worker — processes one task at a time from the queue
# ---------------------------------------------------------------------------


async def _showreel_worker():
    """Background worker that generates showreels one at a time."""
    logger.info("Showreel worker started")
    while True:
        try:
            kind, task_data, item_id = await _showreel_queue.get()
            task_id = f"showreel-{uuid.uuid4().hex[:8]}"

            if kind == "movie":
                video_path, media_folder, title = task_data
                if movie_showreels_exist(media_folder):
                    _showreel_queue.task_done()
                    continue
                store.broadcast_task(
                    TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=f"Showreel: {title}",
                    )
                )
                await generate_showreel_images(video_path, media_folder, title=title)
                # Update the movie item with generated showreel paths
                if item_id in store.movies:
                    movie = store.movies[item_id]
                    from hivescan.showreel import get_expected_showreel_paths

                    media_root_path = (
                        Path(store.media_root) if store.media_root else None
                    )
                    paths = get_expected_showreel_paths(
                        media_folder, media_root=media_root_path
                    )
                    movie.showreel_images = paths if paths else None
                    store.upsert_movie(movie)
                store.broadcast_task(
                    TaskInfo(
                        id=task_id,
                        status="completed",
                        progress=1,
                        detail=f"Showreel: {title}",
                    )
                )

            elif kind == "episode":
                video_path, media_folder, season_num, episode_num, series_title = (
                    task_data
                )
                if episode_reel_exists(media_folder, season_num, episode_num):
                    _showreel_queue.task_done()
                    continue
                ep_code = f"S{season_num:02d}E{episode_num:02d}"
                store.broadcast_task(
                    TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=f"Reel: {series_title} {ep_code}",
                    )
                )
                reel_path = await generate_episode_reel(
                    video_path, media_folder, season_num, episode_num
                )
                # Update the series item with the generated reel path
                if item_id in store.series and reel_path:
                    series = store.series[item_id]
                    media_root_str = store.media_root
                    for season in series.seasons:
                        if season.season_number == season_num:
                            for episode in season.episodes:
                                if episode.episode_number == episode_num:
                                    episode.reel_image = make_relative_path(
                                        reel_path, media_root_str
                                    )
                    store.upsert_series(series)
                store.broadcast_task(
                    TaskInfo(
                        id=task_id,
                        status="completed",
                        progress=1,
                        detail=f"Reel: {series_title} {ep_code}",
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


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def run(host: str = "0.0.0.0", port: int = 8421):
    """Run the hivescan server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
