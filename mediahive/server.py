"""FastAPI server for MediaHive.

Serves media files, the Vue frontend, and runs the continuous scanning
pipeline with live WebSocket updates.  Excluded paths are controlled by
``.mediahive/scanignore`` (gitignore-style syntax).
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import aiofiles
import msgspec
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi_vue import Frontend

from mediahive.__main__ import DEVMODE
from mediahive.config import load_config
from mediahive.hivescan.images import close_image_client
from mediahive.hivescan.scanner import RootScanner
from mediahive.hivescan.tmdb_client import close_http_client
from mediahive.models.protocol import (
    OpenFolderRequest,
    PlayMediaRequest,
    RootsRequest,
)
from mediahive.players import detect_players, launch_player
from mediahive.root_registry import Supervisor

logger = logging.getLogger("mediahive.server")

MPC_BE_DEFAULT_PORT = 13579

# Suppress console windows when spawning subprocesses on Windows
_POPEN_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# Vue Frontend static files
frontend = Frontend(Path(__file__).with_name("frontend-build"), cached=["/assets/"])

# Supervisor manages all root contexts
supervisor = Supervisor()
_attach_scanners_lock = asyncio.Lock()

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
_ROOT_ASSET_TYPES = {"movies", "series", "people"}

if sys.platform == "win32":
    from ctypes import wintypes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_context(root_id: str):
    ctx = supervisor.get(root_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Root not found: {root_id}")
    return ctx


def _load_root_metadata(root_path: Path, meta_key: str):
    """Load allowed per-root metadata values from .mediahive."""
    key = meta_key.strip().lower().strip("/")
    allowed: dict[str, tuple[str, str]] = {
        "playback-state": ("playback-state.json", "json"),
        "scanignore": ("scanignore", "text"),
    }

    mapped = allowed.get(key)
    if mapped is None:
        raise HTTPException(status_code=404, detail=f"Unknown metadata key: {meta_key}")

    rel_path, mode = mapped
    full_path = _resolve_root_scoped_path(root_path / ".mediahive", rel_path)
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"Metadata not found: {meta_key}")

    try:
        if mode == "json":
            return json.loads(full_path.read_text(encoding="utf-8"))
        return full_path.read_text(encoding="utf-8")
    except OSError, TypeError, json.JSONDecodeError:
        raise HTTPException(
            status_code=500, detail=f"Failed to load metadata: {meta_key}"
        )


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
        return

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], **_POPEN_KWARGS)


def _select_file_in_windows_explorer(path: Path) -> bool:
    """Select a file in Explorer using Shell APIs to avoid CLI parsing issues."""
    if sys.platform != "win32":
        return False

    if not path.exists() or not path.is_file():
        return False

    # Use shell32 APIs directly; this avoids explorer.exe argument parsing edge cases.
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    pidl_folder = wintypes.LPVOID()
    pidl_file = wintypes.LPVOID()

    sh_parse_display_name = shell32.SHParseDisplayName
    sh_parse_display_name.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    sh_parse_display_name.restype = ctypes.c_long

    sh_open_folder_and_select_items = shell32.SHOpenFolderAndSelectItems
    sh_open_folder_and_select_items.argtypes = [
        wintypes.LPVOID,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.DWORD,
    ]
    sh_open_folder_and_select_items.restype = ctypes.c_long

    co_initialize = ole32.CoInitialize
    co_initialize.argtypes = [wintypes.LPVOID]
    co_initialize.restype = ctypes.c_long

    co_uninitialize = ole32.CoUninitialize
    co_uninitialize.argtypes = []
    co_uninitialize.restype = None

    co_task_mem_free = ole32.CoTaskMemFree
    co_task_mem_free.argtypes = [wintypes.LPVOID]
    co_task_mem_free.restype = None

    hr = co_initialize(None)
    if hr < 0:
        return False

    try:
        attrs = wintypes.DWORD(0)
        folder_path = str(path.parent)
        hr = sh_parse_display_name(
            folder_path,
            None,
            ctypes.byref(pidl_folder),
            0,
            ctypes.byref(attrs),
        )
        if hr < 0:
            return False

        attrs2 = wintypes.DWORD(0)
        file_path = str(path)
        hr = sh_parse_display_name(
            file_path,
            None,
            ctypes.byref(pidl_file),
            0,
            ctypes.byref(attrs2),
        )
        if hr < 0:
            return False

        item_array = (wintypes.LPVOID * 1)()
        item_array[0] = pidl_file
        hr = sh_open_folder_and_select_items(pidl_folder, 1, item_array, 0)
        return hr >= 0
    finally:
        if pidl_file:
            co_task_mem_free(pidl_file)
        if pidl_folder:
            co_task_mem_free(pidl_folder)
        co_uninitialize()


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


def _build_file_etag(file_size: int, mtime_ns: int) -> str:
    """Build a weak ETag from file metadata for cache validation."""
    return f'W/"{file_size:x}-{mtime_ns:x}"'


def _etag_matches_if_none_match(if_none_match: str | None, etag: str) -> bool:
    """Return True when the request If-None-Match header matches the resource ETag."""
    if not if_none_match:
        return False

    if if_none_match.strip() == "*":
        return True

    def normalize(value: str) -> str:
        v = value.strip()
        if v.startswith("W/"):
            v = v[2:].strip()
        return v

    wanted = normalize(etag)
    return any(normalize(candidate) == wanted for candidate in if_none_match.split(","))


def _validate_root_paths(roots: dict[str, str]) -> dict[str, str]:
    """Validate root paths on the filesystem.

    Runs in a thread pool so macOS permission dialogs (and other blocking
    filesystem checks) do not halt the asyncio event loop.
    """
    validated: dict[str, str] = {}
    for name, path_str in roots.items():
        configured_posix = Path(path_str).expanduser().as_posix()
        if (
            len(configured_posix) == 2
            and configured_posix[1] == ":"
            and configured_posix[0].isalpha()
        ):
            configured_posix = f"{configured_posix}/"

        configured_path = Path(configured_posix)
        if not configured_path.exists() or not configured_path.is_dir():
            logger.warning("Root path invalid, skipping: %s", path_str)
            continue
        # Keep the configured path form (POSIX separators) so downstream naming
        # can reflect user intent (e.g. mapped drive "Z:") instead of UNC.
        validated[name] = configured_posix
    return validated


async def _attach_scanners() -> None:
    """Ensure every active root context has a running scanner."""
    async with _attach_scanners_lock:
        for ctx in supervisor.all_contexts().values():
            if ctx.scanner is None and ctx.status == "ready":
                try:
                    scanner = RootScanner(ctx.root_id, ctx.root_path, ctx.send_event)
                    await scanner.start()
                    ctx.scanner = scanner
                except Exception:
                    logger.exception(
                        "Failed to attach scanner for root %s", ctx.root_id
                    )


async def _attach_scanners_loop() -> None:
    """Periodically attach scanners as roots transition to ready."""
    while True:
        await _attach_scanners()
        await asyncio.sleep(1)


async def _activate_all_roots() -> None:
    """Background task: validate and activate all configured roots.

    This is deferred from lifespan startup so the server can begin accepting
    requests immediately.  Filesystem validation runs in a thread pool to avoid
    blocking the event loop (and to let macOS permission dialogs appear without
    stalling the server).
    """
    desired: dict[str, str] = {}

    # 1. CLI roots via MEDIAHIVE_ROOTS (JSON dict)
    env_roots_raw = os.environ.get("MEDIAHIVE_ROOTS")
    env_roots: dict[str, str] | None = None
    if env_roots_raw:
        try:
            parsed = json.loads(env_roots_raw)
            if isinstance(parsed, dict):
                env_roots = parsed
        except Exception:
            logger.exception("Failed to parse MEDIAHIVE_ROOTS")

    # 2. Persisted config roots (used only when CLI roots are not provided)
    cfg = load_config()
    if env_roots is not None:
        desired.update(env_roots)
    elif cfg.roots:
        desired.update(cfg.roots)

    if not desired:
        logger.info("No roots configured; waiting for PUT /api/roots")
        return

    # Validate paths in a thread pool (macOS permission-dialog safe)
    validated = await asyncio.to_thread(_validate_root_paths, desired)
    if not validated:
        logger.warning("No valid roots found after validation")
        return

    try:
        await supervisor.replace_roots(validated)
    except Exception:
        logger.exception("Failed to replace roots during background activation")
        return

    await _attach_scanners()
    logger.info(
        "Background root activation complete; %d root(s) active",
        len(supervisor.all_contexts()),
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await frontend.load()

    # Defer root activation to a background task so the server starts
    # immediately and macOS permission dialogs do not block startup.
    activation_task = asyncio.create_task(_activate_all_roots())
    scanner_attach_task = asyncio.create_task(_attach_scanners_loop())

    logger.info("Server ready; waiting for root activation")

    try:
        yield
    finally:
        activation_task.cancel()
        with suppress(asyncio.CancelledError):
            await activation_task

        scanner_attach_task.cancel()
        with suppress(asyncio.CancelledError):
            await scanner_attach_task

        await supervisor.shutdown()

        with suppress(Exception):
            await close_http_client()
        with suppress(Exception):
            await close_image_client()


app = FastAPI(title="MediaHive Server", lifespan=lifespan, debug=DEVMODE)

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/config")
async def get_config():
    """Return current server configuration."""
    cfg = load_config()
    return {"roots": cfg.roots}


# --- Root management ---


@app.get("/api/roots")
async def get_roots():
    """List all active roots with their status."""
    return {"roots": supervisor.all_statuses()}


@app.put("/api/roots")
async def put_roots(request: Request):
    """Atomically replace the full root set."""
    body = msgspec.json.decode(await request.body(), type=RootsRequest)
    accepted, failed = await supervisor.replace_roots(body.roots)

    # Start scanners for newly accepted roots
    await _attach_scanners()

    return {
        "status": "ok",
        "accepted": [
            {"name": e.name, "path": e.path, "root_id": e.root_id} for e in accepted
        ],
        "failed": failed,
    }


# --- Per-root WebSocket ---


@app.websocket("/api/ws/{root_id}")
async def ws_endpoint(ws: WebSocket, root_id: str) -> None:
    """Live index updates and task progress for a single root."""
    ctx = supervisor.get(root_id)
    if ctx is None:
        await ws.close(code=1008, reason="Unknown root")
        return

    await ctx.store.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ctx.store.disconnect(ws)
    except OSError, RuntimeError:
        ctx.store.disconnect(ws)


# --- Media actions ---


@app.get("/api/players")
async def list_players():
    """Return detected media players available on this system."""
    players = detect_players()
    return {"players": [msgspec.structs.asdict(p) for p in players]}


@app.post("/api/play/{root_id}")
async def play_media(root_id: str, request: Request):
    """Open a media file with the selected player."""
    ctx = _get_context(root_id)
    req = msgspec.json.decode(await request.body(), type=PlayMediaRequest)
    file_path = _resolve_root_scoped_path(ctx.root_path, req.file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    # Resolve player path if a specific detected player was chosen
    player_path: str | None = None
    if req.player_id and req.player_id not in ("default", "custom"):
        for p in detect_players():
            if p.id == req.player_id:
                player_path = p.path
                break
        if not player_path:
            raise HTTPException(
                status_code=400, detail=f"Player not found: {req.player_id}"
            )

    try:
        launch_player(
            req.player_id or "default",
            file_path,
            player_path=player_path,
            custom_cmd=req.player_custom_cmd,
        )
        return {"status": "ok"}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to play media: {e}")


@app.post("/api/open-folder/{root_id}")
async def open_folder(root_id: str, request: Request):
    """Open a folder in the system file explorer."""
    ctx = _get_context(root_id)
    req = msgspec.json.decode(await request.body(), type=OpenFolderRequest)
    target_path = _resolve_root_scoped_path(ctx.root_path, req.folder_path)

    if not target_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Path not found: {req.folder_path}"
        )

    try:
        if sys.platform == "win32":
            native_path = str(target_path).replace("/", "\\")
            if target_path.is_file():
                if not _select_file_in_windows_explorer(target_path):
                    select_arg = f'/n,/select,"{native_path}"'
                    subprocess.Popen(["explorer.exe", select_arg], **_POPEN_KWARGS)
            else:
                subprocess.Popen(["explorer.exe", native_path], **_POPEN_KWARGS)
        elif sys.platform == "darwin":
            if target_path.is_file():
                subprocess.Popen(["open", "-R", str(target_path)])
            else:
                subprocess.Popen(["open", str(target_path)])
        else:
            folder = target_path.parent if target_path.is_file() else target_path
            subprocess.Popen(["xdg-open", str(folder)])

        return {"status": "ok"}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


@app.get("/api/meta/{root_id}/{meta_key}")
async def root_metadata(root_id: str, meta_key: str):
    """Return a root metadata value from .mediahive for allowed keys."""
    ctx = _get_context(root_id)
    return {"key": meta_key, "data": _load_root_metadata(ctx.root_path, meta_key)}


# --- MPC-BE / Player status ---


def _mpcbe_url(port: int | None = None) -> str:
    """Build MPC-BE base URL from optional custom port."""
    return f"http://127.0.0.1:{port or MPC_BE_DEFAULT_PORT}"


@app.get("/api/mpcbe/status")
async def mpcbe_status(port: int | None = None):
    """Check whether MPC-BE web interface is reachable."""
    return {"reachable": _mpcbe_request("/", port=port)}


@app.get("/api/player/status")
async def player_status(port: int | None = None):
    """Return whether remote player control is currently available."""
    return {"remote": _mpcbe_request("/", port=port)}


def _mpcbe_request(path: str, timeout: float = 0.75, port: int | None = None) -> bool:
    """Call MPC-BE's local web interface and return True on HTTP success."""
    if sys.platform != "win32":
        return False

    url = f"{_mpcbe_url(port)}{path}"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError, TimeoutError, OSError:
        return False


# --- Media file serving ---


def _resolve_root_scoped_path(base: Path, raw_path: str) -> Path:
    """Resolve a user path under a fixed base directory and block traversal."""
    candidate = base / raw_path.lstrip("/")
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return candidate


def _serve_file_response(full_path: Path, file_path: str, request: Request):
    """Serve a file with range + cache support."""
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    file_stat = full_path.stat()
    file_size = file_stat.st_size
    etag = _build_file_etag(file_size, file_stat.st_mtime_ns)
    cache_control = "public, max-age=600"

    range_header = request.headers.get("range")
    if not range_header and _etag_matches_if_none_match(
        request.headers.get("if-none-match"), etag
    ):
        return Response(
            status_code=304,
            headers={"Cache-Control": cache_control, "ETag": etag},
        )

    content_type, _ = mimetypes.guess_type(str(full_path))
    if content_type is None:
        content_type = "application/octet-stream"

    if content_type.startswith("image/"):
        return FileResponse(
            full_path,
            media_type=content_type,
            headers={"Cache-Control": cache_control, "ETag": etag},
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
        "Cache-Control": cache_control,
        "ETag": etag,
        "Accept-Ranges": "bytes",
    }

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


@app.get("/api/media/{root_id}/{file_path:path}")
async def serve_media_file(root_id: str, file_path: str, request: Request):
    """Serve a media file asynchronously, scoped to a root."""
    ctx = _get_context(root_id)
    full_path = _resolve_root_scoped_path(ctx.root_path, file_path)
    return _serve_file_response(full_path, file_path, request)


@app.get("/api/assets/{root_id}/{asset_type}/{asset_path:path}")
async def serve_root_asset_file(
    root_id: str,
    asset_type: str,
    asset_path: str,
    request: Request,
):
    """Serve typed files from a root's .mediahive cache using logical asset paths."""
    asset_type_key = asset_type.lower()
    if asset_type_key not in _ROOT_ASSET_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown asset type: {asset_type}")

    ctx = _get_context(root_id)
    base = ctx.root_path / ".mediahive"
    logical_path = f"{asset_type_key}/{asset_path}"
    full_path = _resolve_root_scoped_path(base, logical_path)
    return _serve_file_response(full_path, logical_path, request)


# Serve the Vue frontend (needs to be last if SPA catch-all is used)
frontend.route(app, "/")
