"""
FastAPI server for MediaHive.

Serves media files, the Vue frontend, and runs the continuous scanning
pipeline with live WebSocket updates.  Excluded paths are controlled by
``.mediahive/scanignore`` (gitignore-style syntax).
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
import msgspec
import msgspec.structs
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi_vue import Frontend

from mediahive.__main__ import DEVMODE
from mediahive.config import load_config, save_config
from mediahive.hivescan.scanner import start as start_scanner
from mediahive.hivescan.scanner import stop as stop_scanner
from mediahive.index_store import IndexStore
from mediahive.models.events import ScanEvent, Task, Upsert
from mediahive.models.protocol import (
    ChangeFolderRequest,
    MsgspecResponse,
    OpenFolderRequest,
    PlayMediaRequest,
    StatusResponse,
)

logger = logging.getLogger("mediahive.server")

MPC_BE_BASE_URL = "http://127.0.0.1:13579"

# Suppress console windows when spawning subprocesses on Windows
_POPEN_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# Vue Frontend static files
frontend = Frontend(Path(__file__).with_name("frontend-build"), cached=["/assets/"])

# Media root path (initialized in lifespan)
MEDIAROOT = None

# In-memory index store (available immediately, switched to real root later)
_BOOTSTRAP_SNAPSHOT = Path(tempfile.gettempdir()) / "mediahive" / "index.json"
store: IndexStore = IndexStore(_BOOTSTRAP_SNAPSHOT, media_root=None)

# Whether the scanner subsystem is active
_scanner_active = False

# Background folder switch task and lock so startup/change-folder cannot race
_folder_switch_task: asyncio.Task | None = None
_folder_switch_lock = asyncio.Lock()

# Queue for scanner → server events
_scan_events: asyncio.Queue[ScanEvent] = asyncio.Queue()
_consumer_task: asyncio.Task | None = None
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def _require_media_root() -> Path:
    if MEDIAROOT is None:
        raise HTTPException(status_code=503, detail="Media root not initialized yet")
    return MEDIAROOT


async def _send_event(event: ScanEvent) -> None:
    """Push a scan event onto the queue (passed to hivescan as *send*)."""
    await _scan_events.put(event)


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a single HTTP bytes range header into inclusive start/end offsets."""
    match = _RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(
            status_code=416,
            detail="Invalid Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_str, end_str = match.groups()
    if not start_str and not end_str:
        raise HTTPException(
            status_code=416,
            detail="Invalid Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    if not start_str:
        suffix_length = int(end_str)
        if suffix_length <= 0:
            raise HTTPException(
                status_code=416,
                detail="Invalid Range header",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1

    if file_size <= 0 or start >= file_size or start < 0 or end < start:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return start, min(end, file_size - 1)


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
    global MEDIAROOT, store, _scanner_active, _consumer_task, _folder_switch_task

    await frontend.load()

    # Bring up the API immediately with an empty in-memory store.
    # Media-root initialization/scanner startup are deferred to a background task
    # so macOS permission prompts cannot block server readiness.
    store = IndexStore(_BOOTSTRAP_SNAPSHOT, media_root=None)
    await store.load_snapshot()
    _scanner_active = False

    logger.info(
        "Server started without active media root; waiting for folder activation"
    )

    yield

    # Shutdown
    if _folder_switch_task and not _folder_switch_task.done():
        _folder_switch_task.cancel()
        try:
            await _folder_switch_task
        except asyncio.CancelledError:
            pass

    await stop_scanner()
    _scanner_active = False
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
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
    return _require_media_root() / clean_path


def _load_resume_positions() -> dict[str, int]:
    playback_state_path = _require_media_root() / ".mediahive" / "playback-state.json"
    try:
        raw = json.loads(playback_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    resume_positions = raw.get("resume_positions") if isinstance(raw, dict) else None
    if not isinstance(resume_positions, dict):
        return {}

    cleaned: dict[str, int] = {}
    for key, value in resume_positions.items():
        if isinstance(key, str) and isinstance(value, (int, float)):
            cleaned[key] = max(0, int(value))
    return cleaned


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
        return

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], **_POPEN_KWARGS)


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
        raise HTTPException(
            status_code=400, detail=f"Folder does not exist: {new_root}"
        )

    # Persist first — if the background switch crashes, the next launch still uses the new path
    cfg = load_config()
    save_config(msgspec.structs.replace(cfg, media_folder=str(new_root)))
    logger.info("Config saved: media_folder=%s", new_root)

    # Schedule the in-memory switch without blocking this response
    asyncio.create_task(_switch_folder(new_root))
    return {"status": "ok"}


async def _switch_folder(new_root: Path) -> None:
    global MEDIAROOT, store, _consumer_task, _scan_events, _scanner_active

    async with _folder_switch_lock:
        try:
            # Cancel scanner tasks immediately — no need to wait 30 s
            await stop_scanner()
            _scanner_active = False

            # Tear down the old event consumer
            if _consumer_task and not _consumer_task.done():
                _consumer_task.cancel()
                try:
                    await _consumer_task
                except asyncio.CancelledError:
                    pass

            # Flush the old index snapshot
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
            logger.info(
                "Index store ready: %d movies, %d series",
                len(store.movies),
                len(store.series),
            )

            # Restart consumer and scanner
            _consumer_task = asyncio.create_task(_consume_scan_events())
            await start_scanner(_send_event)
            _scanner_active = True

            logger.info("Switched media folder to %s", MEDIAROOT)
        except Exception:
            logger.exception("Error switching media folder to %s", new_root)


@app.get("/api/index")
async def get_index():
    """Return the full media index from the in-memory store."""
    return MsgspecResponse(store.get_full_index())


@app.get("/api/playback/resume-positions")
async def playback_resume_positions():
    """Return saved per-file resume positions under the current media root."""
    return {"resume_positions": _load_resume_positions()}


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
    file_path = _require_media_root() / req.file_path

    if not file_path.exists():
        print(f"[play] File not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    try:
        _open_with_default_app(file_path)

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
    target_path = _require_media_root() / req.folder_path

    if not target_path.exists():
        print(f"[open-folder] Path not found: {target_path}")
        raise HTTPException(
            status_code=404, detail=f"Path not found: {req.folder_path}"
        )

    try:
        if sys.platform == "win32":
            if target_path.is_file():
                # Open parent folder and select the file
                subprocess.Popen(
                    ["explorer", "/select,", str(target_path)], **_POPEN_KWARGS
                )
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


def _mpcbe_request(path: str, timeout: float = 0.75) -> bool:
    """Call MPC-BE's local web interface and return True on HTTP success."""
    if sys.platform != "win32":
        return False

    url = f"{MPC_BE_BASE_URL}{path}"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError, TimeoutError, OSError:
        return False


@app.get("/api/mpcbe/status")
async def mpcbe_status():
    """Check whether MPC-BE web interface is reachable."""
    return {"reachable": _mpcbe_request("/")}


@app.get("/api/player/status")
async def player_status():
    """Return whether remote player control is currently available."""
    return {"remote": _mpcbe_request("/")}


@app.get("/api/media/{file_path:path}")
async def serve_media_file(file_path: str, request: Request):
    """
    Serve a media file asynchronously.
    """
    full_path = normalize_path(file_path)
    media_root = _require_media_root()

    # Security: ensure path doesn't escape base
    try:
        full_path.resolve().relative_to(media_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    file_size = full_path.stat().st_size

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

    async def stream_file(start: int, end: int):
        async with aiofiles.open(full_path, "rb") as f:
            await f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = await f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Cache-Control": "public, max-age=86400",
        "Accept-Ranges": "bytes",
    }

    range_header = request.headers.get("range")
    if range_header:
        start, end = _parse_range_header(range_header, file_size)
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(
            stream_file(start, end),
            status_code=206,
            media_type=content_type,
            headers=headers,
        )

    headers["Content-Length"] = str(file_size)

    return StreamingResponse(
        stream_file(0, file_size - 1),
        media_type=content_type,
        headers=headers,
    )


# Serve the Vue frontend (needs to be last if SPA catch-all is used)
frontend.route(app, "/")
