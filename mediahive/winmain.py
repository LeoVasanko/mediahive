"""GUI launcher for MediaHive using pywebview.

Run with: python -m mediahive.winmain [media_folder]
Or from PyInstaller: MediaHive.exe [media_folder]
"""

import argparse
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview
import msgspec.structs

from mediahive.__main__ import DEFAULT_PORT, resolve_media_root
from mediahive.config import Config, load_config, save_config

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8420
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
HEALTH_TIMEOUT = 2  # seconds


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

    log_file = open(log_path, "w", encoding="utf-8", buffering=1)  # line-buffered

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


def _wait_for_backend(timeout: int = HEALTH_TIMEOUT) -> bool:
    url = BACKEND_URL + "/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def _icon_path() -> str | None:
    """Locate the application icon at runtime (frozen or development)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    ico = base / "assets" / "mediahive.ico"
    return str(ico) if ico.exists() else None


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

    webview.start(func=on_shown, icon=_icon_path())
    return chosen[0] if chosen else None


def winmain() -> None:
    parser = argparse.ArgumentParser(description="MediaHive")
    parser.add_argument(
        "media_folder",
        nargs="?",
        help="Path to the media folder (default: saved config, MEDIAHIVE_PATH, or cwd)",
    )
    args = parser.parse_args()

    _prepend_meipass_to_path()

    # In a frozen (windowed) build there is no console — redirect output to a log file
    if getattr(sys, "frozen", False):
        _setup_logging()

    # Resolution order: CLI arg → MEDIAHIVE_PATH env → saved config → ask user
    folder = args.media_folder or os.environ.get("MEDIAHIVE_PATH") or load_config().media_folder

    if not folder:
        folder = _run_initial_setup()
        if not folder:
            return  # user cancelled the folder picker

    mediaroot = resolve_media_root(folder)
    os.environ["MEDIAHIVE_PATH"] = mediaroot.as_posix()

    # Persist the resolved path so subsequent launches remember it.
    cfg = load_config()
    if cfg.media_folder != mediaroot.as_posix():
        save_config(msgspec.structs.replace(cfg, media_folder=mediaroot.as_posix()))

    # Run the FastAPI backend on a background thread so the main thread is
    # free for pywebview (Edge WebView2 requires the GUI on the main thread).
    config = uvicorn.Config(
        "mediahive.server:app",
        host=BACKEND_HOST,
        port=DEFAULT_PORT,
        loop="asyncio",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    backend_thread = threading.Thread(
        target=server.run, daemon=True, name="mediahive-backend"
    )
    backend_thread.start()

    if not _wait_for_backend():
        server.should_exit = True
        raise RuntimeError(f"Backend did not become ready within {HEALTH_TIMEOUT}s")

    api = JsApi()
    window = webview.create_window(
        title="MediaHive",
        url=BACKEND_URL,
        fullscreen=True,
        js_api=api,
    )

    def on_shown() -> None:
        api._window = window

    webview.start(func=on_shown, icon=_icon_path())

    server.should_exit = True
    backend_thread.join(timeout=10)


if __name__ == "__main__":
    winmain()
