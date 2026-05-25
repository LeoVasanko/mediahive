"""GUI launcher for MediaHive using pywebview.

Run with: python -m mediahive.winmain [media_folder]
Or from PyInstaller: MediaHive.exe [media_folder]
"""

import argparse
import asyncio
import ctypes
import html
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import msgspec.structs
import uvicorn
import webview

from mediahive.config import load_config, save_config

logger = logging.getLogger("mediahive.winmain")

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8420
HEALTH_TIMEOUT = 2  # seconds
BACKEND_HEALTH_REQUEST_TIMEOUT = 2  # seconds
BACKEND_HEALTH_POLL_SECONDS = 0.25
MPC_BE_URL = "http://127.0.0.1:13579"
GAMEPAD_REPEAT_SECONDS = 0.008
GAMEPAD_POLL_SECONDS = 0.008
MPC_BE_FRAME_REPEAT_SECONDS = 0.016
MPC_BE_SEEK_BEGIN_HOLD_SECONDS = 1.0
MPC_BE_REQUEST_TIMEOUT = 0.15
MPC_BE_MAX_INFLIGHT_REQUESTS = 12
MPC_BE_REQUEST_WORKERS = 4
MPC_BE_STATUS_POLL_SECONDS = 0.1
MPC_BE_STATUS_MISS_THRESHOLD = 5
MPC_BE_STATE_STOPPED = 0
MPC_BE_STATE_PAUSED = 1
MPC_BE_STATE_RUNNING = 2
MPC_BE_SEEK_BEGIN_COMMAND = 1085
MPC_BE_RESUME_APPLY_THRESHOLD_MS = 15000
MPC_BE_RESUME_CLEAR_MARGIN_MS = 15000
MPC_BE_PLAYBACK_STATE_FLUSH_SECONDS = 1.0


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", _XINPUT_GAMEPAD),
    ]


_XINPUT_BUTTONS = {
    0x0001: "DPAD_UP",
    0x0002: "DPAD_DOWN",
    0x0004: "DPAD_LEFT",
    0x0008: "DPAD_RIGHT",
    0x0010: "START",
    0x0020: "BACK",
    0x0040: "L3",
    0x0080: "R3",
    0x0100: "LB",
    0x0200: "RB",
    0x1000: "A",
    0x2000: "B",
    0x4000: "X",
    0x8000: "Y",
}

_MPC_BE_COMMANDS = {
    0x0001: 907,
    0x0002: 908,
    0x1000: 889,
    0x2000: 816,
    0x8000: 909,
}

_MPC_BE_SEEK_MASK_TO_COMMANDS = {
    0x0004: (892, 901),
    0x0008: (891, 902),
}

_MPC_BE_REPEATABLE_MASKS = {
    0x0001,
    0x0002,
    *_MPC_BE_SEEK_MASK_TO_COMMANDS,
}

_STATE_RE = re.compile(r'<p id="state">(\d+)</p>')
_FILEPATH_RE = re.compile(r'<p id="filepath">(.*?)</p>', re.DOTALL)
_POSITION_RE = re.compile(r'<p id="position">(\d+)</p>')
_DURATION_RE = re.compile(r'<p id="duration">(\d+)</p>')


def _default_playback_state() -> dict[str, object]:
    return {
        "current": None,
        "resume_positions": {},
    }


def _load_playback_state(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, TypeError, json.JSONDecodeError:
        return _default_playback_state()

    if not isinstance(raw, dict):
        return _default_playback_state()

    current = raw.get("current")
    resume_positions = raw.get("resume_positions")
    normalized: dict[str, object] = {
        "current": current if isinstance(current, dict) else None,
        "resume_positions": {},
    }

    if isinstance(resume_positions, dict):
        cleaned_positions: dict[str, int] = {}
        for key, value in resume_positions.items():
            if isinstance(key, str) and isinstance(value, (int, float)):
                cleaned_positions[key] = max(0, int(value))
        normalized["resume_positions"] = cleaned_positions

    return normalized


def _save_playback_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _media_key_for_filepath(
    filepath: str, roots: list[Path]
) -> tuple[str, Path] | None:
    """Resolve a filepath to a (relative_key, matched_root) tuple."""
    for root in roots:
        try:
            relative = Path(filepath).resolve().relative_to(root.resolve())
            return relative.as_posix(), root
        except OSError, RuntimeError, ValueError:
            continue
    return None


def _should_clear_resume(position_ms: int, duration_ms: int) -> bool:
    if position_ms <= MPC_BE_RESUME_CLEAR_MARGIN_MS:
        return True
    if duration_ms <= 0:
        return False
    return duration_ms - position_ms <= MPC_BE_RESUME_CLEAR_MARGIN_MS


def _load_xinput_get_state():
    """Load XInputGetState from available XInput DLLs (XInput only)."""
    candidates = ["xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll"]
    for dll_name in candidates:
        try:
            dll = ctypes.WinDLL(dll_name)
            fn = dll.XInputGetState
            fn.argtypes = [ctypes.c_uint, ctypes.POINTER(_XINPUT_STATE)]
            fn.restype = ctypes.c_ulong
            return fn
        except AttributeError, OSError:
            continue
    raise RuntimeError("XInput DLL not found")


def _mpcbe_request(path: str, timeout: float = MPC_BE_REQUEST_TIMEOUT) -> bool:
    """Call MPC-BE's local web interface and return True on HTTP success."""
    req = urllib.request.Request(url=f"{MPC_BE_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError, TimeoutError, OSError:
        return False


def _send_mpcbe_command(command_id: int) -> bool:
    return _mpcbe_request(f"/command.html?wm_command={command_id}")


def _format_mpcbe_position(position_ms: int) -> str:
    total_seconds = max(0, position_ms // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _seek_mpcbe_to_position(position_ms: int) -> bool:
    query = urllib.parse.urlencode({
        "wm_command": -1,
        "position": _format_mpcbe_position(position_ms),
    })
    return _mpcbe_request(f"/command.html?{query}")


def _mpcbe_fetch_status() -> tuple[str, int, int, int] | None:
    """Fetch current file path, position, duration, and playback state from MPC-BE."""
    req = urllib.request.Request(url=f"{MPC_BE_URL}/variables.html", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=MPC_BE_REQUEST_TIMEOUT) as resp:
            response_html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError, TimeoutError, OSError:
        return None

    state_match = _STATE_RE.search(response_html)
    filepath_match = _FILEPATH_RE.search(response_html)
    position_match = _POSITION_RE.search(response_html)
    duration_match = _DURATION_RE.search(response_html)
    if not state_match or not position_match or not duration_match:
        return None

    filepath = html.unescape(filepath_match.group(1)).strip() if filepath_match else ""
    return (
        filepath,
        int(position_match.group(1)),
        int(duration_match.group(1)),
        int(state_match.group(1)),
    )


def _start_gamepad_remote(
    stop_event: threading.Event, roots: list[Path]
) -> threading.Thread:
    """Start background XInput polling and send mapped commands to MPC-BE."""
    get_state = _load_xinput_get_state()
    last_connected = [False, False, False, False]
    last_pressed_masks = [0, 0, 0, 0]
    seek_begin_hold_started_at: list[float | None] = [None, None, None, None]
    seek_begin_fired = [False, False, False, False]
    last_repeat_at = [
        dict.fromkeys(
            (*_MPC_BE_COMMANDS.keys(), *_MPC_BE_SEEK_MASK_TO_COMMANDS.keys()), 0.0
        )
        for _ in range(4)
    ]
    request_pool = ThreadPoolExecutor(
        max_workers=MPC_BE_REQUEST_WORKERS,
        thread_name_prefix="mediahive-mpcbe",
    )
    pending_requests: list[Future[bool]] = []
    status_lock = threading.Lock()
    status_future: Future[tuple[str, int, int, int] | None] | None = None
    player_filepath = ""
    player_position_ms: int | None = None
    player_duration_ms: int | None = None
    player_state: int | None = None
    status_updated_at = 0.0
    status_miss_count = 0

    # Use the first root's playback state path as primary
    primary_root = roots[0] if roots else Path.cwd()
    playback_state_path = primary_root / ".mediahive" / "playback-state.json"
    playback_state = _load_playback_state(playback_state_path)
    resume_positions = playback_state["resume_positions"]
    if not isinstance(resume_positions, dict):
        resume_positions = {}
        playback_state["resume_positions"] = resume_positions
    if playback_state.get("current") is not None:
        playback_state["current"] = None
        _save_playback_state(playback_state_path, playback_state)
    tracked_media_key: str | None = None
    tracked_filepath = ""
    resume_applied_for_key: str | None = None
    last_playback_state_flush_at = 0.0

    def queue_command(command_id: int) -> None:
        pending_requests.append(request_pool.submit(_send_mpcbe_command, command_id))

    def queue_seek_to_position(position_ms: int) -> None:
        pending_requests.append(
            request_pool.submit(_seek_mpcbe_to_position, position_ms)
        )

    def flush_playback_state() -> None:
        _save_playback_state(playback_state_path, playback_state)

    def clear_tracked_current(*, clear_resume_applied: bool) -> None:
        nonlocal \
            tracked_media_key, \
            tracked_filepath, \
            last_playback_state_flush_at, \
            resume_applied_for_key
        if tracked_media_key is None and playback_state.get("current") is None:
            if clear_resume_applied:
                resume_applied_for_key = None
            return
        tracked_media_key = None
        tracked_filepath = ""
        playback_state["current"] = None
        last_playback_state_flush_at = 0.0
        if clear_resume_applied:
            resume_applied_for_key = None
        flush_playback_state()

    def finalize_tracked_current() -> None:
        nonlocal \
            tracked_media_key, \
            tracked_filepath, \
            resume_applied_for_key, \
            last_playback_state_flush_at
        if tracked_media_key is None:
            if playback_state.get("current") is not None:
                playback_state["current"] = None
                flush_playback_state()
            return

        position_ms = player_position_ms or 0
        duration_ms = player_duration_ms or 0
        if _should_clear_resume(position_ms, duration_ms):
            resume_positions.pop(tracked_media_key, None)
        else:
            resume_positions[tracked_media_key] = position_ms

        tracked_media_key = None
        tracked_filepath = ""
        playback_state["current"] = None
        resume_applied_for_key = None
        last_playback_state_flush_at = 0.0
        flush_playback_state()

    def persist_tracked_current(now: float, *, force: bool = False) -> None:
        nonlocal last_playback_state_flush_at
        if tracked_media_key is None:
            return
        if (
            not force
            and now - last_playback_state_flush_at < MPC_BE_PLAYBACK_STATE_FLUSH_SECONDS
        ):
            return

        playback_state["current"] = {
            "file_key": tracked_media_key,
            "file_path": tracked_filepath,
            "position_ms": player_position_ms or 0,
            "duration_ms": player_duration_ms or 0,
            "updated_at": int(time.time()),
        }
        last_playback_state_flush_at = now
        flush_playback_state()

    def maybe_apply_resume(now: float) -> None:
        nonlocal player_position_ms, resume_applied_for_key
        if tracked_media_key is None:
            return
        if resume_applied_for_key == tracked_media_key:
            return

        saved_position = resume_positions.get(tracked_media_key)
        if not isinstance(saved_position, int):
            resume_applied_for_key = tracked_media_key
            return
        if player_position_ms is None or player_duration_ms is None:
            return
        if player_position_ms > MPC_BE_RESUME_APPLY_THRESHOLD_MS:
            resume_applied_for_key = tracked_media_key
            return
        if _should_clear_resume(saved_position, player_duration_ms):
            resume_positions.pop(tracked_media_key, None)
            resume_applied_for_key = tracked_media_key
            flush_playback_state()
            return
        if len(pending_requests) >= MPC_BE_MAX_INFLIGHT_REQUESTS:
            return

        target_ms = min(saved_position, max(player_duration_ms - 1000, 0))
        queue_seek_to_position(target_ms)
        player_position_ms = target_ms
        resume_applied_for_key = tracked_media_key
        persist_tracked_current(now, force=True)

    def update_status_from_future() -> None:
        nonlocal \
            status_future, \
            player_filepath, \
            player_position_ms, \
            player_duration_ms, \
            player_state, \
            status_updated_at, \
            status_miss_count, \
            tracked_media_key, \
            tracked_filepath, \
            resume_applied_for_key
        if status_future is None or not status_future.done():
            return

        try:
            status = status_future.result()
        except OSError, RuntimeError, ValueError:
            status = None
        status_future = None

        if status is None:
            status_miss_count += 1
            if status_miss_count >= MPC_BE_STATUS_MISS_THRESHOLD:
                finalize_tracked_current()
                with status_lock:
                    player_filepath = ""
                    player_position_ms = None
                    player_duration_ms = None
                    player_state = None
                    status_updated_at = 0.0
            return

        status_miss_count = 0

        filepath, position_ms, duration_ms, state = status
        resolved = _media_key_for_filepath(filepath, roots) if filepath else None
        media_key = resolved[0] if resolved else None

        if tracked_media_key is not None and media_key != tracked_media_key:
            finalize_tracked_current()

        if media_key is None:
            clear_tracked_current(clear_resume_applied=True)
        elif tracked_media_key != media_key:
            tracked_media_key = media_key
            tracked_filepath = filepath
            resume_applied_for_key = None

        player_filepath = filepath

        with status_lock:
            player_position_ms = position_ms
            player_duration_ms = duration_ms
            player_state = state
            status_updated_at = time.monotonic()

        maybe_apply_resume(status_updated_at)
        persist_tracked_current(status_updated_at)

    def queue_status_refresh(now: float, *, force: bool = False) -> None:
        nonlocal status_future
        if status_future is not None:
            return

        with status_lock:
            is_stale = now - status_updated_at >= MPC_BE_STATUS_POLL_SECONDS

        if force or is_stale:
            status_future = request_pool.submit(_mpcbe_fetch_status)

    def command_for_seek(mask: int) -> int:
        paused_command, seek_command = _MPC_BE_SEEK_MASK_TO_COMMANDS[mask]
        with status_lock:
            is_paused = player_state == MPC_BE_STATE_PAUSED
        return paused_command if is_paused else seek_command

    def repeat_seconds_for_seek(mask: int) -> float:
        paused_command, _seek_command = _MPC_BE_SEEK_MASK_TO_COMMANDS[mask]
        with status_lock:
            active_command = (
                paused_command if player_state == MPC_BE_STATE_PAUSED else None
            )
        return (
            MPC_BE_FRAME_REPEAT_SECONDS
            if active_command == paused_command
            else GAMEPAD_REPEAT_SECONDS
        )

    def _run() -> None:
        try:
            while not stop_event.is_set():
                now = time.monotonic()
                pending_requests[:] = [
                    future for future in pending_requests if not future.done()
                ]
                update_status_from_future()
                queue_status_refresh(now)

                for slot in range(4):
                    state = _XINPUT_STATE()
                    rc = get_state(slot, ctypes.byref(state))
                    is_connected = rc == 0
                    current_mask = state.Gamepad.wButtons if is_connected else 0

                    is_seek_begin_pressed = bool(current_mask & 0x4000)
                    if is_seek_begin_pressed:
                        if seek_begin_hold_started_at[slot] is None:
                            seek_begin_hold_started_at[slot] = now
                            seek_begin_fired[slot] = False
                        elif (
                            not seek_begin_fired[slot]
                            and now - seek_begin_hold_started_at[slot]
                            >= MPC_BE_SEEK_BEGIN_HOLD_SECONDS
                            and len(pending_requests) < MPC_BE_MAX_INFLIGHT_REQUESTS
                        ):
                            queue_command(MPC_BE_SEEK_BEGIN_COMMAND)
                            seek_begin_fired[slot] = True
                    else:
                        seek_begin_hold_started_at[slot] = None
                        seek_begin_fired[slot] = False

                    if is_connected != last_connected[slot]:
                        last_connected[slot] = is_connected

                    for mask, command_id in _MPC_BE_COMMANDS.items():
                        is_pressed = bool(current_mask & mask)
                        was_pressed = bool(last_pressed_masks[slot] & mask)
                        should_fire = is_pressed and not was_pressed

                        if (
                            not should_fire
                            and is_pressed
                            and mask in _MPC_BE_REPEATABLE_MASKS
                            and now - last_repeat_at[slot][mask]
                            >= GAMEPAD_REPEAT_SECONDS
                        ):
                            should_fire = True

                        if not should_fire:
                            continue

                        if len(pending_requests) >= MPC_BE_MAX_INFLIGHT_REQUESTS:
                            continue

                        queue_command(command_id)
                        last_repeat_at[slot][mask] = now

                    for mask in _MPC_BE_SEEK_MASK_TO_COMMANDS:
                        is_pressed = bool(current_mask & mask)
                        was_pressed = bool(last_pressed_masks[slot] & mask)
                        should_fire = is_pressed and not was_pressed
                        repeat_seconds = repeat_seconds_for_seek(mask)

                        if (
                            not should_fire
                            and is_pressed
                            and now - last_repeat_at[slot][mask] >= repeat_seconds
                        ):
                            should_fire = True

                        if not should_fire:
                            continue

                        if len(pending_requests) >= MPC_BE_MAX_INFLIGHT_REQUESTS:
                            continue

                        queue_command(command_for_seek(mask))
                        last_repeat_at[slot][mask] = now

                    last_pressed_masks[slot] = current_mask

                stop_event.wait(GAMEPAD_POLL_SECONDS)
        finally:
            finalize_tracked_current()
            request_pool.shutdown(wait=False, cancel_futures=True)

    thread = threading.Thread(target=_run, daemon=True, name="mediahive-gamepad-remote")
    thread.start()
    return thread


def _setup_logging() -> Path:
    """Redirect stdout/stderr and configure logging to a log file in %APPDATA%/mediahive/.

    In a PyInstaller --windowed build there is no console, so any print() or
    unhandled exception traceback would be lost.  This ensures everything ends
    up in a persistent log file the user can send for bug reports.
    Returns the path to the log file.
    """
    from mediahive.config import config_dir

    log_dir = config_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mediahive.log"

    # Rotate: keep previous run as .log.1
    prev = log_path.with_suffix(".log.1")
    if log_path.exists():
        if prev.exists():
            prev.unlink()
        log_path.rename(prev)

    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    log_file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)  # line-buffered

    # Redirect raw stdout/stderr so print() and tracebacks go to the file
    sys.stdout = log_file
    sys.stderr = log_file

    # force=True removes handlers added by uvicorn/fastapi during import so that
    # basicConfig actually takes effect (without it, it's a silent no-op)
    logging.basicConfig(
        force=True,
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("mediahive.winmain").info("MediaHive started")
    return log_path


# Minimal branded setup page shown while the native folder dialog is open.
_SETUP_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #141414; color: #fff;
         font-family: 'Segoe UI', system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; text-align: center; }
  h1 { font-size: 2rem; color: #e50914; margin-bottom: .5rem; }
  p  { color: #aaa; }
</style></head><body>
  <div><h1>MediaHive</h1><p>Choose a folder that contains your media…</p></div>
</body></html>"""


class JsApi:
    """Python methods exposed to the frontend via window.pywebview.api."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def pick_folder(self) -> str | None:
        """Open a native OS folder picker and return the chosen path (or None)."""
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None


def _prepend_meipass_to_path() -> None:
    """When frozen, ensure bundled binaries (ffmpeg) are found first on PATH."""
    if getattr(sys, "frozen", False):
        meipass = sys._MEIPASS  # type: ignore[attr-defined]
        os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")


def _wait_for_backend(timeout: int | None = None) -> bool:
    url = os.environ["MEDIAHIVE_BACKEND_URL"] + "/api/health"
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            return False
        try:
            with urllib.request.urlopen(url, timeout=BACKEND_HEALTH_REQUEST_TIMEOUT):
                return True
        except urllib.error.URLError, TimeoutError, OSError:
            time.sleep(BACKEND_HEALTH_POLL_SECONDS)


def _icon_path() -> str | None:
    """Locate the application icon at runtime (frozen or development)."""
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        base = meipass / "mediahive" if (meipass / "mediahive").exists() else meipass
    else:
        base = Path(__file__).parent
    ico = base / "assets" / "mediahive.ico"
    return str(ico) if ico.exists() else None


def _webview_start_kwargs() -> dict[str, str]:
    """Return platform-specific pywebview startup kwargs."""
    if sys.platform == "darwin":
        return {"gui": "qt"}
    return {}


def _selected_webview_backend() -> str:
    """Return the configured pywebview GUI backend name for logging."""
    return _webview_start_kwargs().get("gui", "default")


def _run_initial_setup() -> str | None:
    """Show a setup window, prompt for a folder, then close and return the path.

    Returns the chosen folder path, or None if the user cancelled.
    The window is always destroyed before this function returns so winmain()
    can continue without a process restart.
    """
    chosen: list[str] = []
    window = webview.create_window(
        "MediaHive — Setup",
        html=_SETUP_HTML,
        width=520,
        height=300,
        resizable=False,
    )

    def on_shown() -> None:
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            chosen.append(result[0])
        window.destroy()

    webview.start(func=on_shown, icon=_icon_path(), **_webview_start_kwargs())
    return chosen[0] if chosen else None


def _normalize_media_root_input(path: str) -> Path:
    """Normalize configured media path without touching filesystem.

    This intentionally avoids exists()/is_dir()/resolve() checks so startup can
    continue even if macOS shows a permission dialog for the selected folder.
    """
    parts = Path(path).expanduser().parts
    match parts:
        case (*rest, ".mediahive", "index.json"):
            ...
        case (*rest, ".mediahive"):
            ...
        case rest:
            ...

    base = Path(*rest)
    if not base.is_absolute():
        base = Path.cwd() / base
    return base


def _supports_gamepad_remote() -> bool:
    return sys.platform == "win32"


def _reserve_backend_port() -> int:
    """Reserve an ephemeral localhost port for the embedded backend."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((BACKEND_HOST, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _configure_windows_event_loop_policy() -> None:
    """Ensure Windows uses Proactor loop so asyncio subprocess APIs are available."""
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    asyncio.set_event_loop_policy(policy_cls())


def winmain() -> None:
    _configure_windows_event_loop_policy()

    parser = argparse.ArgumentParser(description="MediaHive")
    parser.add_argument(
        "media_folder",
        nargs="?",
        help="Path to the media folder (default: saved config or initial setup dialog)",
    )
    args = parser.parse_args()

    _prepend_meipass_to_path()

    # In a frozen (windowed) build there is no console — redirect output to a log file
    if getattr(sys, "frozen", False):
        _setup_logging()

    cfg = load_config()

    # Build initial roots dict (filesystem is NOT touched here — validation is
    # deferred to the server's background activation task).
    initial_roots: dict[str, str] = {}
    if args.media_folder:
        p = _normalize_media_root_input(args.media_folder)
        name = p.name or "media"
        initial_roots[name] = p.as_posix()
    elif cfg.roots:
        initial_roots = cfg.roots
    elif cfg.media_folder:
        p = _normalize_media_root_input(cfg.media_folder)
        name = p.name or "media"
        initial_roots[name] = p.as_posix()

    if not initial_roots:
        folder = _run_initial_setup()
        if not folder:
            return  # user cancelled
        p = _normalize_media_root_input(folder)
        name = p.name or "media"
        initial_roots[name] = p.as_posix()

    # Persist resolved roots
    if cfg.roots != initial_roots:
        save_config(msgspec.structs.replace(cfg, roots=initial_roots))

    # Pass roots to the server via env (validation deferred to server startup)
    os.environ["MEDIAHIVE_ROOTS"] = json.dumps(initial_roots)

    backend_port = _reserve_backend_port()
    backend_url = f"http://{BACKEND_HOST}:{backend_port}"
    os.environ["MEDIAHIVE_BACKEND_URL"] = backend_url

    # Run the FastAPI backend on a background thread
    config = uvicorn.Config(
        "mediahive.server:app",
        host=BACKEND_HOST,
        port=backend_port,
        loop="asyncio",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    backend_thread = threading.Thread(
        target=server.run, daemon=True, name="mediahive-backend"
    )
    backend_thread.start()

    def _activate_initial_roots() -> None:
        body = json.dumps({"roots": initial_roots}).encode("utf-8")
        req = urllib.request.Request(
            url=f"{backend_url}/api/roots",
            data=body,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                logger.info("Requested initial roots activation")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Initial roots activation request failed: %s", exc)

    if not _wait_for_backend(timeout=HEALTH_TIMEOUT):
        server.should_exit = True
        raise RuntimeError(f"Backend did not become ready within {HEALTH_TIMEOUT}s")

    api = JsApi()
    logger.info("Configured pywebview backend: %s", _selected_webview_backend())
    window = webview.create_window(
        title="MediaHive",
        url=backend_url,
        fullscreen=True,
        js_api=api,
    )

    poll_stop = threading.Event()
    poll_thread: threading.Thread | None = None

    # Resolve all root paths for gamepad remote
    gamepad_roots = [Path(p) for p in initial_roots.values()]

    def on_shown() -> None:
        api._window = window
        try:
            user_agent = window.evaluate_js("navigator.userAgent")
            if isinstance(user_agent, str):
                logger.info("Embedded webview user agent: %s", user_agent)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Could not read embedded user agent: %s", exc)

        nonlocal poll_thread
        if poll_thread is None and _supports_gamepad_remote():
            poll_thread = _start_gamepad_remote(poll_stop, gamepad_roots)

        threading.Thread(
            target=_activate_initial_roots,
            daemon=True,
            name="mediahive-initial-roots-activation",
        ).start()

    webview.start(func=on_shown, icon=_icon_path(), **_webview_start_kwargs())

    poll_stop.set()
    if poll_thread is not None:
        poll_thread.join(timeout=1)

    server.should_exit = True
    backend_thread.join(timeout=10)


if __name__ == "__main__":
    winmain()
