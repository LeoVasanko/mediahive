"""
FastAPI server for MediaHive.

Serves media files, the Vue frontend, and runs the continuous scanning
pipeline with live WebSocket updates.  Excluded paths are controlled by
``.mediahive/scanignore`` (gitignore-style syntax).
"""

import asyncio
import logging
import mimetypes
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Suppress console windows when spawning subprocesses on Windows
_POPEN_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

import aiofiles
import msgspec
import msgspec.structs
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi_vue import Frontend

from mediahive.config import load_config, save_config
from mediahive.hivescan.scanner import start as start_scanner
from mediahive.hivescan.scanner import stop as stop_scanner
from mediahive.index_store import IndexStore
from mediahive.models.events import ScanEvent, Task, Upsert
from mediahive.models.protocol import (
    ChangeFolderRequest,
    MsgspecResponse,
    PlayMediaRequest,
    OpenFolderRequest,
    StatusResponse,
)

from mediahive.__main__ import DEVMODE

logger = logging.getLogger("mediahive.server")

# Vue Frontend static files
frontend = Frontend(Path(__file__).with_name("frontend-build"), cached=["/assets/"])

# Media root path (initialized in lifespan)
MEDIAROOT = None

# In-memory index store (initialized in lifespan)
store: IndexStore | None = None

# Whether the scanner subsystem is active
_scanner_active = False

# Queue for scanner → server events
_scan_events: asyncio.Queue[ScanEvent] = asyncio.Queue()
_consumer_task: asyncio.Task | None = None


async def _send_event(event: ScanEvent) -> None:
    """Push a scan event onto the queue (passed to hivescan as *send*)."""
    await _scan_events.put(event)


async def _consume_scan_events() -> None:
    """Background task: apply incoming scan events to the IndexStore."""
    while True:
        try:
            event = await _scan_events.get()
            if isinstance(event, Upsert):
                if event.kind == "movie":
                    store.upsert_movie(event.item)
                else:
                    store.upsert_series(event.item)
            elif isinstance(event, Task):
                store.broadcast_task(event.data)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error processing scan event")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MEDIAROOT, store, _scanner_active, _consumer_task

    if not os.environ.get("MEDIAHIVE_PATH"):
        raise RuntimeError("MEDIAHIVE_PATH environment variable must be set")

    MEDIAROOT = Path(os.environ["MEDIAHIVE_PATH"])
    await frontend.load()

    # Initialise the in-memory index store
    snapshot_path = MEDIAROOT / ".mediahive" / "index.json"
    store = IndexStore(snapshot_path, media_root=str(MEDIAROOT))
    await store.load_snapshot()
    logger.info(
        "Index store ready: %d movies, %d series",
        len(store.movies),
        len(store.series),
    )

    # Start the scanner subsystem
    from mediahive.hivescan.scanner import (
        start as start_scanner,
        stop as stop_scanner,
    )

    _consumer_task = asyncio.create_task(_consume_scan_events())
    await start_scanner(_send_event)
    _scanner_active = True

    yield

    # Shutdown
    await stop_scanner()
    if _consumer_task:
        _consumer_task.cancel()
    await store.flush_snapshot()


app = FastAPI(title="MediaHive Server", lifespan=lifespan, debug=DEVMODE)

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def normalize_path(url_path: str) -> Path:
    """
    Convert URL path to filesystem path.
    URL: /media/.mediahive/Movies/...
    Returns: MEDIAROOT/.mediahive/Movies/...
    """
    clean_path = url_path.lstrip("/")
    return MEDIAROOT / clean_path


# === API Endpoints ===


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/config")
async def get_config():
    """Return current server configuration."""
    return {"media_folder": str(MEDIAROOT) if MEDIAROOT else None}


@app.post("/api/change-folder")
async def change_folder_endpoint(request: Request):
    """Switch the media root folder without restarting the server.

    Validates and persists the new folder, then returns immediately.
    The actual in-memory switch runs as a background task so the HTTP
    response is not held up by the (potentially slow) scanner teardown.
    The client should poll /api/config or reload after a short delay.
    """
    body = msgspec.json.decode(await request.body(), type=ChangeFolderRequest)
    new_root = Path(body.folder).resolve()
    if not new_root.exists() or not new_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder does not exist: {new_root}")

    # Persist first — if the background switch crashes, the next launch still uses the new path
    cfg = load_config()
    save_config(msgspec.structs.replace(cfg, media_folder=str(new_root)))
    logger.info("Config saved: media_folder=%s", new_root)

    # Schedule the in-memory switch without blocking this response
    asyncio.create_task(_switch_folder(new_root))
    return {"status": "ok"}


async def _switch_folder(new_root: Path) -> None:
    global MEDIAROOT, store, _consumer_task, _scan_events

    try:
        # Cancel scanner tasks immediately — no need to wait 30 s
        await stop_scanner()

        # Tear down the old event consumer
        if _consumer_task and not _consumer_task.done():
            _consumer_task.cancel()
            try:
                await _consumer_task
            except asyncio.CancelledError:
                pass

        # Flush the old index snapshot
        if store:
            await store.flush_snapshot()

        # Update env and module globals
        os.environ["MEDIAHIVE_PATH"] = str(new_root)
        MEDIAROOT = new_root

        # Fresh event queue — discard any stale events from the old folder
        _scan_events = asyncio.Queue()

        # Re-initialise the index store
        snapshot_path = MEDIAROOT / ".mediahive" / "index.json"
        store = IndexStore(snapshot_path, media_root=str(MEDIAROOT))
        await store.load_snapshot()

        # Restart consumer and scanner
        _consumer_task = asyncio.create_task(_consume_scan_events())
        await start_scanner(_send_event)

        logger.info("Switched media folder to %s", MEDIAROOT)
    except Exception:
        logger.exception("Error switching media folder to %s", new_root)


@app.get("/api/index")
async def get_index():
    """Return the full media index from the in-memory store."""
    return MsgspecResponse(store.get_full_index())


# ---------------------------------------------------------------------------
# Scanning API (active when HIVESCAN_PATHS is configured)
# ---------------------------------------------------------------------------


@app.websocket("/api/ws")
async def ws_endpoint(ws: WebSocket):
    """Live index updates and task progress."""
    await store.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        store.disconnect(ws)
    except Exception:
        store.disconnect(ws)


@app.post("/api/scan")
async def trigger_scan():
    """Trigger a new scan. Returns 409 if a scan is already running."""
    if not _scanner_active:
        raise HTTPException(status_code=503, detail="Scanner not active yet")
    from mediahive.hivescan.scanner import trigger_scan as _trigger

    started = _trigger()
    return {"status": "started" if started else "already_running"}


@app.get("/api/status")
async def server_status():
    """Return current server status."""
    if _scanner_active:
        from mediahive.hivescan.scanner import is_scanning, showreel_queue_size

        return MsgspecResponse(
            StatusResponse(
                scanning=is_scanning(),
                movies=len(store.movies),
                series=len(store.series),
                showreel_queue=showreel_queue_size(),
            )
        )
    return MsgspecResponse(
        StatusResponse(
            movies=len(store.movies),
            series=len(store.series),
        )
    )


@app.post("/api/play")
async def play_media(request: Request):
    """
    Open a media file with the system's default player.
    """
    req = msgspec.json.decode(await request.body(), type=PlayMediaRequest)
    print(f"[play] Received path: {req.file_path}")
    file_path = MEDIAROOT / req.file_path

    if not file_path.exists():
        print(f"[play] File not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    try:
        # Use os.startfile on Windows (non-blocking)
        if sys.platform == "win32":
            os.startfile(str(file_path))
        else:
            # For other platforms, use xdg-open or open
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(file_path)])

        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to play media: {e}")


@app.post("/api/open-folder")
async def open_folder(request: Request):
    """
    Open a folder in the system file explorer.
    If the path is a file, opens the parent folder and selects the file.
    """
    req = msgspec.json.decode(await request.body(), type=OpenFolderRequest)
    print(f"[open-folder] Received path: {req.folder_path}")
    target_path = MEDIAROOT / req.folder_path

    if not target_path.exists():
        print(f"[open-folder] Path not found: {target_path}")
        raise HTTPException(
            status_code=404, detail=f"Path not found: {req.folder_path}"
        )

    try:
        if sys.platform == "win32":
            if target_path.is_file():
                # Open parent folder and select the file
                subprocess.Popen(["explorer", "/select,", str(target_path)], **_POPEN_KWARGS)
            else:
                # Open the folder directly
                subprocess.Popen(["explorer", str(target_path)], **_POPEN_KWARGS)
        elif sys.platform == "darwin":
            if target_path.is_file():
                subprocess.Popen(["open", "-R", str(target_path)])
            else:
                subprocess.Popen(["open", str(target_path)])
        else:
            # Linux - just open the folder (no standard way to select)
            folder = target_path.parent if target_path.is_file() else target_path
            subprocess.Popen(["xdg-open", str(folder)])

        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


@app.get("/api/media/{file_path:path}")
async def serve_media_file(file_path: str):
    """
    Serve a media file asynchronously.
    """
    full_path = normalize_path(file_path)

    # Security: ensure path doesn't escape base
    try:
        full_path.resolve().relative_to(MEDIAROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    # Guess content type
    content_type, _ = mimetypes.guess_type(str(full_path))
    if content_type is None:
        content_type = "application/octet-stream"

    # For images, use FileResponse which handles caching headers
    if content_type.startswith("image/"):
        return FileResponse(
            full_path,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )

    # For larger files, stream them
    async def stream_file():
        async with aiofiles.open(full_path, "rb") as f:
            while chunk := await f.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        stream_file(),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )


# Serve the Vue frontend (needs to be last if SPA catch-all is used)
frontend.route(app, "/")
