"""GUI launcher for MediaHive using pywebview.

Run with: python -m mediahive.winmain [media_folder]
Or from PyInstaller: MediaHive.exe [media_folder]
"""

import argparse
import asyncio
import contextlib
import ctypes
import html
import importlib.metadata
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

# Must be set before fastapi_vue env bindings are created (mediahive.config);
# this module is the PyInstaller entry point and may run without __main__.
os.environ.setdefault("FASTAPI_VUE", "MEDIAHIVE")

import msgspec.structs
import uvicorn
import velopack
import webview
from fastapi_vue import env
from fastapi_vue.logging import patch_log_config
from fastapi_vue.startupbox import print_box
from tracerite.html import html_traceback

from mediahive.config import config, load_config, log_dir, save_config
from mediahive.volume_control import get_volume, set_volume, volume_max

logger = logging.getLogger("mediahive.winmain")

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8420
HEALTH_TIMEOUT = 2  # seconds
BACKEND_HEALTH_REQUEST_TIMEOUT = 2  # seconds
BACKEND_HEALTH_POLL_SECONDS = 0.25
MPC_BE_URL = "http://127.0.0.1:13579"
VELOPACK_REPO_URL = "https://git.zi.fi/LeoVasanko/mediahive"
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
# Watching (or presumably watching) less than this leaves no position data:
# brief peeks and seeks back to re-view a scene are not true progress.
MPC_BE_RESUME_MIN_WATCH_MS = 5 * 60 * 1000
MPC_BE_PLAYBACK_STATE_FLUSH_SECONDS = 1.0
VOLUME_MIN = 0.0
VOLUME_MAX = 1.5
VOLUME_STEP = 0.01
VOLUME_REPEAT_SECONDS = 0.02


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
    0x1000: 889,
    0x2000: 816,
    0x8000: 909,
}

_MPC_BE_SEEK_MASK_TO_COMMANDS = {
    0x0004: (892, 901),
    0x0008: (891, 902),
}

_MPC_BE_REPEATABLE_MASKS = {
    *_MPC_BE_SEEK_MASK_TO_COMMANDS,
}

_STATE_RE = re.compile(r'<p id="state">(\d+)</p>')
_FILEPATH_RE = re.compile(r'<p id="filepath">(.*?)</p>', re.DOTALL)
_POSITION_RE = re.compile(r'<p id="position">(\d+)</p>')
_DURATION_RE = re.compile(r'<p id="duration">(\d+)</p>')


def _default_playback_state() -> dict[str, object]:
    return {
        "current": None,
    }


def _normalize_media_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _expand_playable_file(file_key: str, playable_file: str | None) -> str:
    if not playable_file:
        return file_key
    if playable_file.startswith("concat:") or "://" in playable_file:
        return playable_file
    if playable_file.startswith(f"{file_key}/"):
        return playable_file
    if playable_file.startswith("/"):
        return playable_file.lstrip("/")
    return f"{file_key}/{playable_file}"


def _fetch_resume_positions(
    backend_url: str,
) -> tuple[
    dict[str, tuple[int, int | None, int | None]],
    dict[tuple[str, int, int], int],
]:
    """Fetch resume state from the backend.

    Returns (continue_points, episode_positions): continue_points map a slug
    to (pos_ms, season_number, episode_number) — season/episode set for the
    series' single continue point, None for movies. episode_positions map
    (slug, season, episode) to pos_ms for partially watched episodes;
    fully watched episodes are absent.
    """
    req = urllib.request.Request(
        url=f"{backend_url}/api/meta/playback-state",
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError:
        return {}, {}

    data = raw.get("data") if isinstance(raw, dict) else None
    positions = data.get("resume_positions") if isinstance(data, dict) else None
    if not isinstance(positions, dict):
        return {}, {}

    cleaned: dict[str, tuple[int, int | None, int | None]] = {}
    episode_positions: dict[tuple[str, int, int], int] = {}
    for slug, value in positions.items():
        if not isinstance(slug, str) or not isinstance(value, dict):
            continue
        pos = value.get("pos")
        season = value.get("season")
        episode = value.get("episode")
        if isinstance(pos, int) and pos > 0:
            cleaned[slug] = (
                pos * 1000,
                season if isinstance(season, int) else None,
                episode if isinstance(episode, int) else None,
            )
        elif pos == 0 and isinstance(season, int) and isinstance(episode, int):
            # Series episode boundary marker (previous episode completed).
            cleaned[slug] = (0, season, episode)

        episodes = value.get("episodes")
        if not isinstance(episodes, dict):
            continue
        for key, watch in episodes.items():
            match = re.fullmatch(r"S(\d+)E(\d+)", str(key))
            if not match or not isinstance(watch, dict):
                continue
            ep_pos = watch.get("pos")
            if watch.get("done") or not isinstance(ep_pos, int) or ep_pos <= 0:
                continue
            episode_positions[slug, int(match.group(1)), int(match.group(2))] = (
                ep_pos * 1000
            )
    return cleaned, episode_positions


def _post_resume_position(
    backend_url: str,
    root_id: str,
    file_path: str,
    pos: int | None,
) -> bool:
    body = json.dumps({
        "root_id": root_id,
        "file_path": file_path,
        "pos": pos,
    }).encode("utf-8")
    req = urllib.request.Request(
        url=f"{backend_url}/api/meta/playback-state",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2):
            return True
    except OSError, TimeoutError, urllib.error.URLError:
        logger.warning("Failed to post playback-state update for %s", file_path)
        return False


def _media_file_key(
    mapping: dict[str, str], file_key: str, torrent: object, media_key: str
) -> None:
    """Map both the raw file key and its expanded playable path to a media key."""
    mapping[_normalize_media_path(file_key)] = media_key
    playable_file = torrent.get("playable_file") if isinstance(torrent, dict) else None
    expanded = _expand_playable_file(
        file_key, playable_file if isinstance(playable_file, str) else None
    )
    mapping[_normalize_media_path(expanded)] = media_key


def _split_media_key(media_key: str) -> tuple[str, int | None, int | None]:
    """Split a media key into (slug, season_number, episode_number)."""
    slug, separator, ep_ref = media_key.partition("#")
    if not separator:
        return slug, None, None
    match = re.fullmatch(r"S(\d+)E(\d+)", ep_ref)
    if not match:
        return slug, None, None
    return slug, int(match.group(1)), int(match.group(2))


def _load_media_key_map(index_path: Path) -> dict[str, str]:
    """Map normalized playable file paths to media keys.

    Movies map to their movie id; series episodes map to
    "<series_id>#S<season>E<episode>" so episode switches are detected while
    the backend keeps a single continue point per series.
    """
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except OSError, TypeError, json.JSONDecodeError:
        return {}

    if not isinstance(raw, dict):
        return {}

    mapping: dict[str, str] = {}

    movies = raw.get("movies")
    if isinstance(movies, dict):
        for movie_id, movie in movies.items():
            if not isinstance(movie_id, str) or not isinstance(movie, dict):
                continue
            files = movie.get("files")
            if not isinstance(files, dict):
                continue
            for file_key, torrent in files.items():
                if not isinstance(file_key, str):
                    continue
                _media_file_key(mapping, file_key, torrent, movie_id)

    series = raw.get("series")
    if isinstance(series, dict):
        for series_id, show in series.items():
            if not isinstance(series_id, str) or not isinstance(show, dict):
                continue
            seasons = show.get("seasons")
            if not isinstance(seasons, list):
                continue
            for season in seasons:
                if not isinstance(season, dict):
                    continue
                season_number = season.get("season_number")
                episodes = season.get("episodes")
                if not isinstance(season_number, int) or not isinstance(episodes, list):
                    continue
                for episode in episodes:
                    if not isinstance(episode, dict):
                        continue
                    episode_number = episode.get("episode_number")
                    files = episode.get("files")
                    if not isinstance(episode_number, int) or not isinstance(
                        files, dict
                    ):
                        continue
                    media_key = f"{series_id}#S{season_number}E{episode_number}"
                    for file_key, torrent in files.items():
                        if not isinstance(file_key, str):
                            continue
                        _media_file_key(mapping, file_key, torrent, media_key)

    return mapping


def _media_key_for_filepath(
    filepath: str, roots: dict[str, Path]
) -> tuple[str | None, str, str] | None:
    """Resolve a filepath to a (media_key, root_id, relative_key) tuple."""
    for root_id, root in roots.items():
        try:
            relative = Path(filepath).resolve().relative_to(root.resolve())
            relative_key = relative.as_posix()
            index_path = root / ".mediahive" / "index.json"
            media_key = _load_media_key_map(index_path).get(
                _normalize_media_path(relative_key)
            )
            return media_key, root_id, relative_key
        except OSError, RuntimeError, ValueError:
            continue
    return None


def _should_clear_resume(position_ms: int, duration_ms: int) -> bool:
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
    stop_event: threading.Event, roots: dict[str, Path], backend_url: str
) -> threading.Thread:
    """Start background XInput polling and send mapped commands to MPC-BE."""
    get_state = _load_xinput_get_state()
    last_connected = [False, False, False, False]
    last_pressed_masks = [0, 0, 0, 0]
    seek_begin_hold_started_at: list[float | None] = [None, None, None, None]
    seek_begin_fired = [False, False, False, False]
    last_volume_repeat_up_at = [0.0, 0.0, 0.0, 0.0]
    last_volume_repeat_down_at = [0.0, 0.0, 0.0, 0.0]
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

    playback_state = _default_playback_state()
    resume_positions, episode_positions = _fetch_resume_positions(backend_url)
    tracked_media_key: str | None = None
    tracked_root_id: str | None = None
    tracked_relative_path = ""
    tracked_filepath = ""
    resume_applied_for_key: str | None = None
    last_playback_state_flush_at = 0.0

    def queue_command(command_id: int) -> None:
        pending_requests.append(request_pool.submit(_send_mpcbe_command, command_id))

    def queue_seek_to_position(position_ms: int) -> None:
        pending_requests.append(
            request_pool.submit(_seek_mpcbe_to_position, position_ms)
        )

    def clear_tracked_current(*, clear_resume_applied: bool) -> None:
        nonlocal \
            tracked_media_key, \
            tracked_root_id, \
            tracked_relative_path, \
            tracked_filepath, \
            last_playback_state_flush_at, \
            resume_applied_for_key
        if tracked_media_key is None and playback_state.get("current") is None:
            if clear_resume_applied:
                resume_applied_for_key = None
            return
        tracked_media_key = None
        tracked_root_id = None
        tracked_relative_path = ""
        tracked_filepath = ""
        playback_state["current"] = None
        last_playback_state_flush_at = 0.0
        if clear_resume_applied:
            resume_applied_for_key = None

    def finalize_tracked_current() -> None:
        nonlocal \
            tracked_media_key, \
            tracked_root_id, \
            tracked_relative_path, \
            tracked_filepath, \
            resume_applied_for_key, \
            last_playback_state_flush_at
        if tracked_media_key is None:
            if playback_state.get("current") is not None:
                playback_state["current"] = None
            return

        position_ms = player_position_ms or 0
        duration_ms = player_duration_ms or 0
        tracked_slug, tracked_season, tracked_episode = _split_media_key(
            tracked_media_key
        )
        if _should_clear_resume(position_ms, duration_ms):
            resume_positions.pop(tracked_slug, None)
            if tracked_season is not None and tracked_episode is not None:
                episode_positions.pop(
                    (tracked_slug, tracked_season, tracked_episode), None
                )
            if tracked_root_id and tracked_relative_path:
                _post_resume_position(
                    backend_url, tracked_root_id, tracked_relative_path, None
                )
        elif position_ms < MPC_BE_RESUME_MIN_WATCH_MS:
            # Peeks and brief seeks are not true progress; keep the previous
            # saved resume position.
            pass
        else:
            position_seconds = max(0, position_ms // 1000)
            resume_positions[tracked_slug] = (
                position_ms,
                tracked_season,
                tracked_episode,
            )
            if tracked_season is not None and tracked_episode is not None:
                episode_positions[tracked_slug, tracked_season, tracked_episode] = (
                    position_ms
                )
            if tracked_root_id and tracked_relative_path:
                _post_resume_position(
                    backend_url,
                    tracked_root_id,
                    tracked_relative_path,
                    position_seconds,
                )

        tracked_media_key = None
        tracked_root_id = None
        tracked_relative_path = ""
        tracked_filepath = ""
        playback_state["current"] = None
        resume_applied_for_key = None
        last_playback_state_flush_at = 0.0

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

    def maybe_apply_resume(now: float) -> None:
        nonlocal player_position_ms, resume_applied_for_key
        if tracked_media_key is None:
            return
        if resume_applied_for_key == tracked_media_key:
            return

        tracked_slug, tracked_season, tracked_episode = _split_media_key(
            tracked_media_key
        )
        if tracked_season is not None and tracked_episode is not None:
            # Series: the episode's own saved position wins; fall back to the
            # series continue point when it points at this very episode.
            saved_position = episode_positions.get((
                tracked_slug,
                tracked_season,
                tracked_episode,
            ))
            if saved_position is None:
                saved = resume_positions.get(tracked_slug)
                if saved is None or (saved[1], saved[2]) != (
                    tracked_season,
                    tracked_episode,
                ):
                    # The series continue point belongs to a different episode.
                    resume_applied_for_key = tracked_media_key
                    return
                saved_position = saved[0]
        else:
            saved = resume_positions.get(tracked_slug)
            if saved is None:
                resume_applied_for_key = tracked_media_key
                return
            saved_position = saved[0]

        if saved_position <= 0:
            # Episode boundary marker (previous episode completed): start at 0.
            resume_applied_for_key = tracked_media_key
            return
        if player_position_ms is None or player_duration_ms is None:
            return
        if player_position_ms > MPC_BE_RESUME_APPLY_THRESHOLD_MS:
            resume_applied_for_key = tracked_media_key
            return
        if _should_clear_resume(saved_position, player_duration_ms):
            resume_positions.pop(tracked_slug, None)
            if tracked_season is not None and tracked_episode is not None:
                episode_positions.pop(
                    (tracked_slug, tracked_season, tracked_episode), None
                )
            resume_applied_for_key = tracked_media_key
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
            tracked_root_id, \
            tracked_relative_path, \
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
        root_id = resolved[1] if resolved else None
        relative_path = resolved[2] if resolved else ""

        if tracked_media_key is not None and media_key != tracked_media_key:
            finalize_tracked_current()

        if media_key is None:
            clear_tracked_current(clear_resume_applied=True)
        elif tracked_media_key != media_key:
            tracked_media_key = media_key
            tracked_root_id = root_id
            tracked_relative_path = relative_path
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

                    # Volume control via D-pad up/down with fixed repeat cadence
                    is_vol_up_pressed = bool(current_mask & 0x0001)
                    was_vol_up_pressed = bool(last_pressed_masks[slot] & 0x0001)
                    if is_vol_up_pressed and (
                        not was_vol_up_pressed
                        or now - last_volume_repeat_up_at[slot] >= VOLUME_REPEAT_SECONDS
                    ):
                        set_volume(min(volume_max(), get_volume() + VOLUME_STEP))
                        last_volume_repeat_up_at[slot] = now
                    elif not is_vol_up_pressed:
                        last_volume_repeat_up_at[slot] = 0.0

                    is_vol_down_pressed = bool(current_mask & 0x0002)
                    was_vol_down_pressed = bool(last_pressed_masks[slot] & 0x0002)
                    if is_vol_down_pressed and (
                        not was_vol_down_pressed
                        or now - last_volume_repeat_down_at[slot]
                        >= VOLUME_REPEAT_SECONDS
                    ):
                        set_volume(max(VOLUME_MIN, get_volume() - VOLUME_STEP))
                        last_volume_repeat_down_at[slot] = now
                    elif not is_vol_down_pressed:
                        last_volume_repeat_down_at[slot] = 0.0

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


def _rotate_and_open_log(log_path: Path):
    """Rotate mediahive.log to .log.1 and open a fresh log file.

    Raises OSError when a previous MediaHive instance still holds the file
    open (Windows forbids renaming a file that is open without delete
    sharing) — callers treat that as "previous instance not dead yet".
    """
    prev = log_path.with_suffix(".log.1")
    if log_path.exists():
        if prev.exists():
            prev.unlink()
        log_path.rename(prev)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    return os.fdopen(fd, "w", encoding="utf-8", buffering=1)  # line-buffered


def _wait_for_previous_instance(log_path: Path, timeout: float = 15.0):
    """Show a waiting notice while a previous MediaHive instance exits.

    Returns an open log file handle, or None on timeout.
    """
    result: list = []
    window = webview.create_window(
        "MediaHive", html=_WAIT_HTML, width=520, height=280, resizable=False
    )

    def poll() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result.append(_rotate_and_open_log(log_path))
                break
            except OSError:
                time.sleep(0.5)
        window.destroy()

    webview.start(func=poll, icon=_icon_path(), **_webview_start_kwargs())
    return result[0] if result else None


def _setup_logging() -> Path:
    """Redirect stdout/stderr and configure logging to a file in the platform log dir.

    In a PyInstaller --windowed build there is no console, so any print() or
    unhandled exception traceback would be lost.  This ensures everything ends
    up in a persistent log file the user can send for bug reports.
    Returns the path to the log file.
    """
    log_directory = log_dir()
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "mediahive.log"

    try:
        log_file = _rotate_and_open_log(log_path)
    except OSError:
        # A previous instance still holds the log file.  It is usually on its
        # way out — give it a couple of seconds silently first.
        log_file = None
        deadline = time.monotonic() + 2.0
        while log_file is None and time.monotonic() < deadline:
            time.sleep(0.25)
            with contextlib.suppress(OSError):
                log_file = _rotate_and_open_log(log_path)
        if log_file is None:
            log_file = _wait_for_previous_instance(log_path)
        if log_file is None:
            # Never fail startup over logging: fall back to a per-process file.
            log_path = log_directory / f"mediahive-{os.getpid()}.log"
            with contextlib.suppress(OSError):
                fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                log_file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)

    if log_file is not None:
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

# Shown when a previous instance is still shutting down.
_WAIT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #141414; color: #fff;
         font-family: 'Segoe UI', system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; text-align: center; }
  h1 { font-size: 2rem; color: #e50914; margin-bottom: .5rem; }
  p  { color: #aaa; }
</style></head><body>
  <div><h1>MediaHive</h1>
  <p>Waiting for the previous MediaHive instance to finish exiting…</p></div>
</body></html>"""


def _show_fatal_error(exc: BaseException) -> None:
    """Show an unhandled exception as a TraceRite HTML page in a webview.

    Frozen --windowed builds otherwise surface crashes only as PyInstaller's
    plain-text error dialog (or nothing at all).
    """
    fragment = str(html_traceback(exc))
    page = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>MediaHive — Error</title></head>"
        f"<body style='margin:1.5rem'>{fragment}</body></html>"
    )
    webview.create_window("MediaHive — Error", html=page, width=1100, height=750)
    webview.start(icon=_icon_path(), **_webview_start_kwargs())


def _velopack_startup() -> None:
    """Handle Velopack install/update/uninstall hooks and pending updates.

    Must be the first thing at startup: when Velopack launches the app with
    --veloapp-* hook arguments (during install/update/uninstall), run()
    executes the hook and exits the process, so the GUI never starts.
    Applies downloaded-but-pending updates. No-op in development and
    portable-ZIP runs.
    """
    velopack.App().run()


def _check_for_updates() -> None:
    """Download available updates in the background.

    Downloaded updates are applied automatically by Velopack on the next app
    start (via _velopack_startup), so the running session is never
    interrupted. Not a Velopack install (dev/portable) and network failures
    are expected and skipped quietly.
    """
    try:
        mgr = velopack.UpdateManager(velopack.GiteaSource(VELOPACK_REPO_URL))
        info = mgr.check_for_updates()
        if info is None:
            logger.info("Velopack: no update available")
            return
        version = info.TargetFullRelease.Version
        logger.info("Velopack: downloading update %s", version)
        mgr.download_updates(info)
        logger.info("Velopack: update %s staged, applies on next launch", version)
    except (RuntimeError, OSError) as exc:
        logger.info("Velopack update check skipped: %s", exc)


def gui_main() -> None:
    """Run the GUI, rendering fatal exceptions as a TraceRite HTML window."""
    _velopack_startup()
    try:
        winmain()
    except Exception as exc:
        logger.exception("Fatal error")
        _show_fatal_error(exc)


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

    def set_volume(self, x: float) -> None:
        """Set system master volume from slider position ``x`` (0.0 .. 1.5)."""
        # Clamp to the platform's maximum so the slider never exceeds what
        # the OS can actually apply (1.0 on Windows/macOS, 1.5 on Linux).
        clamped = max(VOLUME_MIN, min(volume_max(), float(x)))
        set_volume(clamped)

    def get_volume(self) -> float:
        """Return current volume slider position (0.0 .. 1.5)."""
        return get_volume()

    def volume_max(self) -> float:
        """Return the maximum volume slider position for this platform."""
        return volume_max()


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


def _strip_mark_of_the_web() -> None:
    """Remove Zone.Identifier streams from bundled DLLs (frozen Windows only).

    Files extracted from a downloaded ZIP carry the Mark-of-the-Web, and the
    .NET Framework CLR refuses to load such assemblies — pythonnet then fails
    with "Failed to resolve Python.Runtime.Loader.Initialize from
    .../Python.Runtime.dll". Strip the mark from the bundled DLLs before
    pywebview loads the CLR.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    for dll in meipass.rglob("*.dll"):
        with contextlib.suppress(OSError):
            Path(f"{dll}:Zone.Identifier").unlink()


def winmain() -> None:
    _configure_windows_event_loop_policy()
    _strip_mark_of_the_web()

    parser = argparse.ArgumentParser(description="MediaHive")
    parser.add_argument(
        "media_folder",
        nargs="?",
        help="Path to the media folder (default: saved config or initial setup dialog)",
    )
    args, _unknown = parser.parse_known_args()

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

    # Pass roots to the in-process server via the shared env config
    # (validation deferred to server startup)
    config.roots = initial_roots

    backend_port = _reserve_backend_port()
    backend_url = f"http://{BACKEND_HOST}:{backend_port}"
    os.environ["MEDIAHIVE_BACKEND_URL"] = backend_url

    # Startup banner, same as fastapi-vue's server.run() prints in CLI mode.
    # Goes to stderr, which frozen builds redirect to the log file.
    try:
        version = importlib.metadata.version("mediahive")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    print_box(f"MediaHive {version}\n{backend_url}")

    # Run the FastAPI backend on a background thread.  fastapi-vue's patched
    # log config wires up its access-log middleware, emoji level prefixes and
    # tracerite tracebacks (colors are auto-disabled when stderr is not a tty,
    # e.g. redirected to the log file in frozen builds).
    log_config = patch_log_config(uvicorn.config.LOGGING_CONFIG)
    # fastapi-vue routes the root logger at INFO in dev / WARNING in prod;
    # keep our own loggers visible in production too.
    log_config.setdefault("loggers", {})["mediahive"] = {
        "level": "DEBUG" if env.dev else "INFO"
    }
    uvicorn_config = uvicorn.Config(
        "mediahive.server:app",
        host=BACKEND_HOST,
        port=backend_port,
        loop="asyncio",
        server_header=False,
        timeout_graceful_shutdown=0,
        access_log=False,  # fastapi-vue's middleware replaces uvicorn's
        log_config=log_config,
    )
    server = uvicorn.Server(uvicorn_config)
    backend_thread = threading.Thread(
        target=server.run, daemon=True, name="mediahive-backend"
    )
    backend_thread.start()

    def _activate_initial_roots() -> None:
        body = json.dumps({"roots": initial_roots}).encode("utf-8")
        req = urllib.request.Request(
            url=f"{backend_url}/api/config/roots",
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

    threading.Thread(
        target=_check_for_updates, daemon=True, name="mediahive-update-check"
    ).start()

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
    gamepad_roots = {root_id: Path(p) for root_id, p in initial_roots.items()}

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
            poll_thread = _start_gamepad_remote(poll_stop, gamepad_roots, backend_url)

        threading.Thread(
            target=_activate_initial_roots,
            daemon=True,
            name="mediahive-initial-roots-activation",
        ).start()

    def on_closing() -> None:
        # Begin backend shutdown as soon as the window starts closing so that
        # by the time webview.start() returns the backend is already done.
        server.should_exit = True

    window.events.closing += on_closing

    webview.start(func=on_shown, icon=_icon_path(), **_webview_start_kwargs())

    # Ensure backend shutdown has been requested (in case closing event
    # was not fired or we are on a platform that does not support it).
    server.should_exit = True
    backend_thread.join(timeout=2)

    poll_stop.set()
    if poll_thread is not None:
        poll_thread.join(timeout=1)

    # Close log file handles so mediahive.log is not left locked.
    if getattr(sys, "frozen", False):
        logging.shutdown()
        for handler in logging.root.handlers[:]:
            handler.close()
            logging.root.removeHandler(handler)
        if sys.stdout is not sys.__stdout__:
            with contextlib.suppress(Exception):
                sys.stdout.close()
        if sys.stderr is not sys.__stderr__:
            with contextlib.suppress(Exception):
                sys.stderr.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


if __name__ == "__main__":
    gui_main()
