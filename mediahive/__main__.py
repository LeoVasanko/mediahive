"""MediaHive CLI entrypoint."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from fastapi_vue import server

DEFAULT_PORT = 8420
DEVMODE = os.getenv("MEDIAHIVE_DEV") == "1"


def _configure_windows_event_loop_policy() -> None:
    """Ensure Windows uses Proactor loop so asyncio subprocess APIs are available."""
    if sys.platform != "win32":
        return
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy_cls is None:
            return
        asyncio.set_event_loop_policy(policy_cls())


def _derive_name(path: str) -> str:
    """Derive a root name from a path."""
    p = Path(path)
    return p.name or p.anchor.strip("/\\").lower() or "media"


def _dev_reload_supervisor() -> None:
    """Windows dev-mode reloader: restart the server process on changes.

    uvicorn's own reload cannot work here: it restarts the child with
    CTRL_C_EVENT, which is never delivered to a plain spawn child (no own
    console process group), so the reloader blocks in join() after the
    first reload and the old server — scanner included — keeps running.
    And even when the child does restart, uvicorn passes it sockets bound
    by the parent; ProactorEventLoop cannot register inherited sockets
    with IOCP (WinError 87 on accept), while the selector loop would lose
    asyncio subprocess support (ffmpeg/ffprobe showreel generation).

    So: watch the package directory ourselves and respawn a fresh child
    process that binds its own sockets.  The child runs with
    MEDIAHIVE_DEV_CHILD=1 and reload disabled.  Scanner state is persisted
    after every scan, so a non-graceful child exit on reload loses nothing.
    """
    import subprocess

    import watchfiles

    watch_dir = Path(__file__).parent
    argv = [sys.executable, "-m", "mediahive", *sys.argv[1:]]
    child_env = dict(os.environ, MEDIAHIVE_DEV_CHILD="1")

    print(f"Dev reloader: watching {watch_dir}", file=sys.stderr)
    proc = subprocess.Popen(argv, env=child_env)
    try:
        for changes in watchfiles.watch(watch_dir):
            changed = sorted({str(Path(p).name) for _, p in changes})
            print(
                f"Dev reloader: change in {', '.join(changed[:5])} — restarting",
                file=sys.stderr,
            )
            proc.terminate()
            proc.wait()
            proc = subprocess.Popen(argv, env=child_env)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main() -> None:
    _configure_windows_event_loop_policy()

    parser = argparse.ArgumentParser(
        description="MediaHive - Media scanning, indexing, and streaming"
    )
    parser.add_argument(
        "media_folders",
        nargs="*",
        metavar="MEDIA_FOLDER",
        help=(
            "One or more media folders to index "
            "(default: none — configure via UI or API)"
        ),
    )
    parser.add_argument(
        "-l",
        "--listen",
        action="append",
        help=(f"Endpoint (default: localhost:{DEFAULT_PORT})."),
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Run with GUI (fails if GUI dependencies are not installed)",
    )

    args = parser.parse_args()

    # --listen implies server-only mode; use --gui to force GUI even with --listen.
    use_gui = args.gui or not args.listen

    if use_gui:
        try:
            from mediahive.winmain import gui_main
        except ImportError as exc:
            if args.gui:
                raise RuntimeError(
                    "GUI dependencies are not installed. "
                    "Install with: uv pip install mediahive[gui]"
                ) from exc
        else:
            gui_main()
            return

    if args.media_folders:
        roots: dict[str, str] = {}
        for path in args.media_folders:
            # Defer filesystem validation to the server so startup is never
            # blocked by macOS permission dialogs or missing paths.
            p = Path(path).expanduser()
            name = _derive_name(p.as_posix())
            # Resolve collisions
            base_name = name
            suffix = 2
            while name in roots:
                name = f"{base_name}{suffix}"
                suffix += 1
            roots[name] = p.as_posix()
        os.environ["MEDIAHIVE_ROOTS"] = json.dumps(roots)

    if (
        DEVMODE
        and sys.platform == "win32"
        and os.environ.get("MEDIAHIVE_DEV_CHILD") != "1"
    ):
        _dev_reload_supervisor()
        return

    server.run(
        "mediahive.server:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        server_header=False,
        loop="none" if sys.platform == "win32" else "auto",
        reload=Path(__file__).parent if DEVMODE and sys.platform != "win32" else False,
    )


if __name__ == "__main__":
    main()
