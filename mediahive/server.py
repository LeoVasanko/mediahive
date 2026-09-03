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
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
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
from mediahive.models.events import Remove, Task, Upsert
from mediahive.models.protocol import (
    OpenFolderRequest,
    PlaybackStateUpdateRequest,
    PlayMediaRequest,
    RootsRequest,
    WsInit,
    WsRemove,
    WsRootInitData,
    WsRoots,
    WsRootStatus,
    WsTask,
    WsUpsert,
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
frontend = Frontend(
    Path(__file__).with_name("frontend-build"), cached=["/assets/"], spa=True
)

# Supervisor manages all root contexts
supervisor = Supervisor()
_attach_scanners_lock = asyncio.Lock()

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
_ROOT_ASSET_TYPES = {"movies", "series", "people"}

if sys.platform == "win32":
    from ctypes import wintypes


@dataclass
class _PlaybackEntry:
    """Single resume position entry with timestamp."""

    pos: int
    ts: datetime

    def to_dict(self) -> dict:
        return {
            "pos": self.pos,
            "ts": self.ts,
        }

    @staticmethod
    def from_dict(data: dict) -> _PlaybackEntry | None:
        if not isinstance(data, dict):
            return None
        pos = data.get("pos")
        ts = data.get("ts")
        if not isinstance(pos, int) or pos < 0:
            return None
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError, TypeError:
                return None
        elif isinstance(ts, datetime):
            pass
        else:
            return None
        return _PlaybackEntry(pos=pos, ts=ts)


@dataclass
class _PlaybackRootSnapshot:
    file_path: Path
    signature: tuple[bool, int, int] | None = None
    entries: dict[str, _PlaybackEntry] = field(default_factory=dict)


class PlaybackStateCache:
    """Background cache for merged playback-state across all active roots.

    Stores resume positions by movie slug with timestamps. When merging
    across roots, picks the most recent entry for each slug.
    """

    def __init__(self, poll_interval: float = 60.0) -> None:
        self._poll_interval = poll_interval
        self._roots: dict[str, _PlaybackRootSnapshot] = {}
        self._merged_entries: dict[str, _PlaybackEntry] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mediahive-playback-state-cache",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self._poll_interval + 1.0))
        self._thread = None

    def get_merged_entries(self) -> dict[str, _PlaybackEntry]:
        """Return merged resume entries keyed by slug (most recent wins)."""
        with self._lock:
            return {
                slug: _PlaybackEntry(e.pos, e.ts)
                for slug, e in self._merged_entries.items()
            }

    def update_resume_position(
        self,
        root_id: str,
        root_path: Path,
        slug: str,
        pos: int | None,
    ) -> None:
        """Read-modify-write one root file and refresh the in-memory cache immediately."""
        file_path = root_path / ".mediahive" / "playback-state.json"
        entries = self._read_resume_entries(file_path)

        if pos is None:
            entries.pop(slug, None)
        else:
            entries[slug] = _PlaybackEntry(pos=pos, ts=datetime.now())

        self._write_resume_entries(file_path, entries)

        snapshot = _PlaybackRootSnapshot(
            file_path=file_path,
            signature=self._signature(file_path),
            entries=entries,
        )
        with self._lock:
            self._roots[root_id] = snapshot
            self._merged_entries = self._build_merged_entries(self._roots)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_once()
            except Exception:
                logger.exception("Playback-state cache refresh failed")
            if self._stop.wait(self._poll_interval):
                break

    def _refresh_once(self) -> None:
        contexts = supervisor.all_contexts()

        with self._lock:
            previous = self._roots

        next_roots: dict[str, _PlaybackRootSnapshot] = {}
        merged: dict[str, _PlaybackEntry] = {}

        for root_id, ctx in contexts.items():
            file_path = ctx.root_path / ".mediahive" / "playback-state.json"
            snapshot = previous.get(root_id)
            if snapshot is None or snapshot.file_path != file_path:
                snapshot = _PlaybackRootSnapshot(file_path=file_path)

            signature = self._signature(file_path)
            if signature != snapshot.signature:
                snapshot.signature = signature
                snapshot.entries = self._read_resume_entries(file_path)

            next_roots[root_id] = snapshot

            # Merge: for each slug, keep the entry with the most recent timestamp
            for slug, entry in snapshot.entries.items():
                existing = merged.get(slug)
                if existing is None or entry.ts > existing.ts:
                    merged[slug] = entry

        with self._lock:
            self._roots = next_roots
            self._merged_entries = merged

    @staticmethod
    def _signature(path: Path) -> tuple[bool, int, int]:
        try:
            stat = path.stat()
            return (True, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (False, 0, 0)

    @staticmethod
    def _read_resume_entries(path: Path) -> dict[str, _PlaybackEntry]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return {}

        if not isinstance(raw, dict):
            return {}

        positions = raw.get("resume_positions")
        if not isinstance(positions, dict):
            return {}

        entries: dict[str, _PlaybackEntry] = {}
        for slug, data in positions.items():
            entry = _PlaybackEntry.from_dict(data)
            if entry is not None:
                entries[str(slug)] = entry

        return entries

    @staticmethod
    def _write_resume_entries(path: Path, entries: dict[str, _PlaybackEntry]) -> None:
        data = {
            "resume_positions": {
                slug: entry.to_dict() for slug, entry in sorted(entries.items())
            }
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(f"{path.suffix}.tmp")
            tmp_path.write_text(
                json.dumps(data, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            tmp_path.replace(path)
        except OSError:
            logger.exception("Failed to write playback-state: %s", path)

    @staticmethod
    def _build_merged_entries(
        roots: dict[str, _PlaybackRootSnapshot],
    ) -> dict[str, _PlaybackEntry]:
        merged: dict[str, _PlaybackEntry] = {}
        for snapshot in roots.values():
            for slug, entry in snapshot.entries.items():
                existing = merged.get(slug)
                if existing is None or entry.ts > existing.ts:
                    merged[slug] = entry
        return merged


class EventLoopLagMonitor:
    """Tracks event-loop scheduling lag over a sliding window."""

    def __init__(
        self, sample_interval: float = 0.05, window_seconds: float = 10.0
    ) -> None:
        self._sample_interval = sample_interval
        self._window_seconds = window_seconds
        self._task: asyncio.Task | None = None
        self._last_lag_ms = 0.0
        self._samples: deque[tuple[float, float]] = deque()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="mediahive-event-loop-lag")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def snapshot(self) -> tuple[float, float]:
        now = time.perf_counter()
        self._prune(now)
        max_window_ms = max((lag for _, lag in self._samples), default=0.0)
        return self._last_lag_ms, max_window_ms

    async def _run(self) -> None:
        interval = self._sample_interval
        next_tick = time.perf_counter() + interval
        while True:
            await asyncio.sleep(interval)
            now = time.perf_counter()
            lag_ms = max(0.0, (now - next_tick) * 1000.0)
            self._last_lag_ms = lag_ms
            self._samples.append((now, lag_ms))
            self._prune(now)
            next_tick = now + interval

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


playback_state_cache = PlaybackStateCache()
event_loop_lag_monitor = EventLoopLagMonitor()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_context(root_id: str):
    ctx = supervisor.get(root_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Root not found: {root_id}")
    return ctx


def _normalize_media_path_value(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _expand_torrent_playable_path(file_key: str, playable_file: str | None) -> str:
    if not playable_file:
        return file_key
    if playable_file.startswith("concat:") or "://" in playable_file:
        return playable_file
    if playable_file.startswith(f"{file_key}/"):
        return playable_file
    if playable_file.startswith("/"):
        return playable_file.lstrip("/")
    return f"{file_key}/{playable_file}"


def _resolve_movie_slug_for_file_path(ctx, file_path: str) -> str | None:
    target = _normalize_media_path_value(file_path)
    for movie_id, movie in ctx.store.movies.items():
        for file_key, torrent in movie.files.items():
            normalized_key = _normalize_media_path_value(file_key)
            if normalized_key == target:
                return movie_id
            playable_path = _expand_torrent_playable_path(
                file_key, torrent.playable_file
            )
            if _normalize_media_path_value(playable_path) == target:
                return movie_id
    return None


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


def _all_root_statuses() -> list[WsRootStatus]:
    """Return root statuses in WebSocket wire format."""
    return [
        WsRootStatus(
            root_id=s["root_id"],
            path=s["path"],
            status=s["status"],
            error=s.get("error"),
            snapshot_loaded=bool(s.get("snapshot_loaded")),
            movies=int(s.get("movies", 0)),
            series=int(s.get("series", 0)),
        )
        for s in supervisor.all_statuses()
    ]


def _full_ws_init(root_ids: set[str] | None = None) -> WsInit:
    """Build an init payload for all roots or only selected root_ids."""
    roots: dict[str, WsRootInitData] = {}
    for rid, ctx in supervisor.all_contexts().items():
        if root_ids is not None and rid not in root_ids:
            continue
        roots[rid] = WsRootInitData(
            movies=dict(ctx.store.movies),
            series=dict(ctx.store.series),
            people=dict(ctx.store.people),
        )
    return WsInit(roots=roots)


def _translate_store_event(root_id: str, event: object):
    """Convert per-root store events into unified websocket messages."""
    if isinstance(event, Upsert):
        return WsUpsert(
            root_id=root_id,
            kind=event.kind,
            id=event.id,
            item=event.item,
            people=event.people,
        )
    if isinstance(event, Remove):
        return WsRemove(root_id=root_id, kind=event.kind, id=event.id)
    if isinstance(event, Task):
        return WsTask(root_id=root_id, data=event.data)
    return None


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
        logger.info("No roots configured; waiting for PUT /api/config/roots")
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
    playback_state_cache.start()
    event_loop_lag_monitor.start()

    # Defer root activation to a background task so the server starts
    # immediately and macOS permission dialogs do not block startup.
    activation_task = asyncio.create_task(_activate_all_roots())
    scanner_attach_task = asyncio.create_task(_attach_scanners_loop())

    logger.info("Server ready; waiting for root activation")

    try:
        yield
    finally:
        playback_state_cache.stop()
        await event_loop_lag_monitor.stop()

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


@app.put("/api/config/roots")
async def put_roots(request: Request):
    """Atomically replace the full root set."""
    body = msgspec.json.decode(await request.body(), type=RootsRequest)
    accepted, failed = await supervisor.replace_roots(body.roots)

    # Start scanners for newly accepted roots
    await _attach_scanners()

    return {
        "status": "ok",
        "accepted": [{"path": e.path, "root_id": e.root_id} for e in accepted],
        "failed": failed,
    }


# --- Unified WebSocket ---


@app.websocket("/api/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Live updates stream for all roots and all connected clients."""
    listeners: dict[str, object] = {}
    attached_contexts = supervisor.all_contexts()
    outbound: asyncio.Queue[bytes] = asyncio.Queue()

    prev_root_ids: set[str] = set()
    prev_meta: dict[str, tuple[str, str, str | None, bool]] = {}

    def _sync_listeners() -> tuple[
        set[str], dict[str, tuple[str, str, str | None, bool]]
    ]:
        nonlocal attached_contexts
        current_contexts = supervisor.all_contexts()
        current_ids = set(current_contexts.keys())

        for rid in list(listeners.keys()):
            if rid in current_ids:
                continue
            old_ctx = attached_contexts.get(rid)
            listener = listeners.pop(rid)
            if old_ctx is not None:
                old_ctx.store.remove_listener(listener)

        for rid, ctx in current_contexts.items():
            if rid in listeners:
                continue

            def _listener(event: object, *, _rid=rid) -> None:
                translated = _translate_store_event(_rid, event)
                if translated is None:
                    return
                outbound.put_nowait(msgspec.json.encode(translated))

            listeners[rid] = _listener
            ctx.store.add_listener(_listener)

        attached_contexts = current_contexts
        meta = {
            s.root_id: (s.path, s.status, s.error, s.snapshot_loaded)
            for s in _all_root_statuses()
        }
        return current_ids, meta

    async def _send_outbound() -> None:
        while True:
            payload = await outbound.get()
            await ws.send_bytes(payload)

    async def _watch_roots() -> None:
        nonlocal prev_root_ids, prev_meta
        while True:
            current_ids, current_meta = _sync_listeners()
            root_set_changed = current_ids != prev_root_ids
            meta_changed = current_meta != prev_meta

            if root_set_changed or meta_changed:
                outbound.put_nowait(
                    msgspec.json.encode(WsRoots(roots=_all_root_statuses()))
                )

            if root_set_changed:
                outbound.put_nowait(msgspec.json.encode(_full_ws_init()))
            else:
                became_loaded = {
                    rid
                    for rid, meta in current_meta.items()
                    if rid in prev_meta and not prev_meta[rid][3] and meta[3]
                }
                if became_loaded:
                    outbound.put_nowait(
                        msgspec.json.encode(_full_ws_init(became_loaded))
                    )

            prev_root_ids = current_ids
            prev_meta = current_meta
            await asyncio.sleep(1.0)

    await ws.accept()

    current_ids, current_meta = _sync_listeners()
    prev_root_ids = current_ids
    prev_meta = current_meta
    await ws.send_bytes(msgspec.json.encode(WsRoots(roots=_all_root_statuses())))
    await ws.send_bytes(msgspec.json.encode(_full_ws_init()))

    sender_task = asyncio.create_task(_send_outbound())
    watcher_task = asyncio.create_task(_watch_roots())

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect, OSError, RuntimeError:
        pass
    finally:
        sender_task.cancel()
        watcher_task.cancel()
        with suppress(asyncio.CancelledError):
            await sender_task
        with suppress(asyncio.CancelledError):
            await watcher_task

        for rid, listener in list(listeners.items()):
            ctx = attached_contexts.get(rid)
            if ctx is not None:
                ctx.store.remove_listener(listener)


# --- Media actions ---


@app.get("/api/players")
async def list_players():
    """Return detected media players available on this system."""
    players = detect_players()
    return {"players": [msgspec.structs.asdict(p) for p in players]}


@app.post("/api/play/{root_id}")
async def play_media(root_id: str, request: Request, response: Response):
    """Open a media file with the selected player."""
    req_start = time.perf_counter()
    trace_id = request.headers.get("x-mediahive-trace-id", "")
    client_sent_ms_hdr = request.headers.get("x-mediahive-client-sent-ms")
    client_to_server_ms: float | None = None
    if client_sent_ms_hdr:
        with suppress(ValueError):
            client_to_server_ms = max(
                0.0, (time.time() * 1000.0) - float(client_sent_ms_hdr)
            )

    ctx = _get_context(root_id)
    body_t0 = time.perf_counter()
    req = msgspec.json.decode(await request.body(), type=PlayMediaRequest)
    decode_ms = (time.perf_counter() - body_t0) * 1000.0

    resolve_t0 = time.perf_counter()
    file_path = _resolve_root_scoped_path(ctx.root_path, req.file_path)
    resolve_ms = (time.perf_counter() - resolve_t0) * 1000.0

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    # Resolve player path if a specific detected player was chosen
    player_path: str | None = None
    detect_ms = 0.0
    if req.player_id and req.player_id not in ("default", "custom"):
        detect_t0 = time.perf_counter()
        for p in detect_players():
            if p.id == req.player_id:
                player_path = p.path
                break
        detect_ms = (time.perf_counter() - detect_t0) * 1000.0
        if not player_path:
            raise HTTPException(
                status_code=400, detail=f"Player not found: {req.player_id}"
            )

    try:
        launch_t0 = time.perf_counter()
        launch_player(
            req.player_id or "default",
            file_path,
            player_path=player_path,
            custom_cmd=req.player_custom_cmd,
        )

        launch_ms = (time.perf_counter() - launch_t0) * 1000.0
        total_ms = (time.perf_counter() - req_start) * 1000.0
        loop_lag_ms, loop_lag_max_ms = event_loop_lag_monitor.snapshot()

        if trace_id:
            response.headers["X-MediaHive-Trace-Id"] = trace_id
        response.headers["Server-Timing"] = (
            f"app;dur={total_ms:.1f},"
            f"decode;dur={decode_ms:.1f},"
            f"resolve;dur={resolve_ms:.1f},"
            f"detect;dur={detect_ms:.1f},"
            f"launch;dur={launch_ms:.1f},"
            f"looplag;dur={loop_lag_ms:.1f},"
            f"looplagmax;dur={loop_lag_max_ms:.1f}"
        )
        request.state.log_extra = (
            f"trace={trace_id or '-'} "
            f"phase[decode={decode_ms:.1f}ms resolve={resolve_ms:.1f}ms "
            f"detect={detect_ms:.1f}ms launch={launch_ms:.1f}ms] "
            f"loopLag={loop_lag_ms:.1f}/{loop_lag_max_ms:.1f}ms"
        )
        if client_to_server_ms is not None:
            request.state.log_extra = (
                f"{request.state.log_extra} clientToServer={client_to_server_ms:.1f}ms"
            )

        return {"status": "ok"}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to play media: {e}")


@app.post("/api/open-folder/{root_id}")
async def open_folder(root_id: str, request: Request, response: Response):
    """Open a folder in the system file explorer."""
    req_start = time.perf_counter()
    trace_id = request.headers.get("x-mediahive-trace-id", "")
    client_sent_ms_hdr = request.headers.get("x-mediahive-client-sent-ms")
    client_to_server_ms: float | None = None
    if client_sent_ms_hdr:
        with suppress(ValueError):
            client_to_server_ms = max(
                0.0, (time.time() * 1000.0) - float(client_sent_ms_hdr)
            )

    ctx = _get_context(root_id)
    body_t0 = time.perf_counter()
    req = msgspec.json.decode(await request.body(), type=OpenFolderRequest)
    decode_ms = (time.perf_counter() - body_t0) * 1000.0

    resolve_t0 = time.perf_counter()
    target_path = _resolve_root_scoped_path(ctx.root_path, req.folder_path)
    resolve_ms = (time.perf_counter() - resolve_t0) * 1000.0

    if not target_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Path not found: {req.folder_path}"
        )

    try:
        open_t0 = time.perf_counter()
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

        open_ms = (time.perf_counter() - open_t0) * 1000.0
        total_ms = (time.perf_counter() - req_start) * 1000.0
        loop_lag_ms, loop_lag_max_ms = event_loop_lag_monitor.snapshot()

        if trace_id:
            response.headers["X-MediaHive-Trace-Id"] = trace_id
        response.headers["Server-Timing"] = (
            f"app;dur={total_ms:.1f},"
            f"decode;dur={decode_ms:.1f},"
            f"resolve;dur={resolve_ms:.1f},"
            f"open;dur={open_ms:.1f},"
            f"looplag;dur={loop_lag_ms:.1f},"
            f"looplagmax;dur={loop_lag_max_ms:.1f}"
        )
        request.state.log_extra = (
            f"trace={trace_id or '-'} "
            f"phase[decode={decode_ms:.1f}ms resolve={resolve_ms:.1f}ms open={open_ms:.1f}ms] "
            f"loopLag={loop_lag_ms:.1f}/{loop_lag_max_ms:.1f}ms"
        )
        if client_to_server_ms is not None:
            request.state.log_extra = (
                f"{request.state.log_extra} clientToServer={client_to_server_ms:.1f}ms"
            )

        return {"status": "ok"}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


@app.get("/api/meta/{root_id}/{meta_key}")
async def root_metadata(root_id: str, meta_key: str):
    """Return a root metadata value from .mediahive for allowed keys."""
    ctx = _get_context(root_id)
    return {"key": meta_key, "data": _load_root_metadata(ctx.root_path, meta_key)}


@app.get("/api/meta/playback-state")
async def merged_playback_state():
    """Return merged playback-state resume positions from in-memory cache.

    Merges across all roots, preferring the most recent timestamp for each slug.
    Format: {"key": "playback-state", "data": {"resume_positions": {slug: {pos, ts}}}}
    """
    entries = playback_state_cache.get_merged_entries()
    positions = {slug: entry.to_dict() for slug, entry in entries.items()}
    return {"key": "playback-state", "data": {"resume_positions": positions}}


@app.post("/api/meta/playback-state")
async def write_playback_state(request: Request):
    """Update one playback-state entry via backend-managed read-modify-write."""
    req = msgspec.json.decode(await request.body(), type=PlaybackStateUpdateRequest)
    ctx = _get_context(req.root_id)
    slug = _resolve_movie_slug_for_file_path(ctx, req.file_path)
    if slug is None:
        raise HTTPException(
            status_code=404,
            detail=f"Movie not found for file path: {req.file_path}",
        )

    pos = None if req.pos is None or req.pos <= 0 else int(req.pos)
    playback_state_cache.update_resume_position(req.root_id, ctx.root_path, slug, pos)
    return {"status": "ok", "slug": slug, "pos": pos}


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


class StreamingFileResponse(StreamingResponse):
    """Stream a file from disk with optional single-range support.

    Unlike plain ``StreamingResponse`` with a generator, the file handle is
    held in ``stream_response`` scope across the send loop (the same pattern
    as Starlette's ``FileResponse``), so it is always closed promptly and in
    flow — including on client disconnect, where a generator's cleanup would
    be deferred to GC and its exceptions lost.
    """

    chunk_size = 64 * 1024

    def __init__(
        self,
        path: Path,
        *,
        file_size: int,
        etag: str,
        cache_control: str,
        media_type: str,
        range_header: str | None = None,
    ) -> None:
        if range_header:
            self._start, self._end = _parse_range_header(range_header, file_size)
            status_code = 206
        else:
            self._start, self._end = 0, file_size - 1
            status_code = 200

        headers = {
            "Cache-Control": cache_control,
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Content-Length": str(self._end - self._start + 1),
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {self._start}-{self._end}/{file_size}"

        super().__init__(  # body_iterator is unused; stream_response is overridden
            content=(),
            status_code=status_code,
            headers=headers,
            media_type=media_type,
        )
        self.path = path

    async def stream_response(self, send) -> None:
        await send({
            "type": "http.response.start",
            "status": self.status_code,
            "headers": self.raw_headers,
        })
        async with aiofiles.open(self.path, "rb") as f:
            await f.seek(self._start)
            remaining = self._end - self._start + 1
            while remaining > 0:
                chunk = await f.read(min(self.chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": True,
                })
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _serve_file_response(full_path: Path, file_path: str, request: Request):
    """Serve a file with range + cache support."""
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    file_stat = full_path.stat()
    file_size = file_stat.st_size
    etag = _build_file_etag(file_size, file_stat.st_mtime_ns)
    cache_control = "public, max-age=604800, immutable"

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

    return StreamingFileResponse(
        full_path,
        file_size=file_size,
        etag=etag,
        cache_control=cache_control,
        media_type=content_type,
        range_header=range_header,
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
