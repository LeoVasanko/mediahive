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

    args = parser.parse_args()

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

    dev = {"reload": True, "reload_dirs": ["mediahive"], "loop": "none"}
    server.run(
        "mediahive.server:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        **(dev if DEVMODE else {}),
    )


if __name__ == "__main__":
    main()
