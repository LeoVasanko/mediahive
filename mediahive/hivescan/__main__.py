"""Hivescan CLI entrypoint."""

import os

# Must be set before fastapi_vue env bindings are created (mediahive.config).
os.environ.setdefault("FASTAPI_VUE", "MEDIAHIVE")

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mediahive.config import config


def _configure_windows_event_loop_policy() -> None:
    """Ensure Windows uses Proactor loop so asyncio subprocess APIs are available."""
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    asyncio.set_event_loop_policy(policy_cls())


def main() -> None:
    _configure_windows_event_loop_policy()

    parser = argparse.ArgumentParser(
        description="Hivescan server — continuous media scanning with live WS updates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m mediahive.hivescan /srv/media              # Scan a media root
  python -m mediahive.hivescan Z:\\                     # Windows drive
  python -m mediahive.hivescan /srv/media --port 9000  # Custom port

Exclude paths by creating .mediahive/scanignore (gitignore syntax).

The server exposes a unified endpoint:
    WS   /api/ws  Live index updates, task progress, and root status changes
        """,
    )
    parser.add_argument(
        "media_folder",
        help="Root folder to scan recursively",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8421,
        help="Port to listen on (default: 8421)",
    )

    args = parser.parse_args()

    # Defer filesystem validation to the server; pass raw path via env config.
    media_root = Path(args.media_folder).expanduser()
    config.roots = {media_root.name or "media": media_root.as_posix()}

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    import uvicorn

    uvicorn.run(
        "mediahive.server:app", host=args.host, port=args.port, log_level="info"
    )


if __name__ == "__main__":
    main()
