import argparse
import os
import sys
from pathlib import Path

from fastapi_vue import server

DEFAULT_PORT = 8420
DEVMODE = bool(os.getenv("MEDIAHIVE_FRONTEND_URL"))


def resolve_media_root(path: str | None = None) -> Path:
    """Resolve the media root folder from a path, MEDIAHIVE_PATH env, or cwd."""
    match Path(path or os.environ.get("MEDIAHIVE_PATH") or Path.cwd()).parts:
        case (*rest, ".mediahive", "index.json"):
            ...
        case (*rest, ".mediahive"):
            ...
        case rest:
            ...
    mediaroot = Path(*rest).resolve()
    if not mediaroot.exists() or not mediaroot.is_dir():
        sys.stderr.write(f"Error: Folder does not exist: {mediaroot}\n")
        exit(1)
    return mediaroot


def main():
    parser = argparse.ArgumentParser(
        description="MediaHive - Media scanning, indexing, and streaming"
    )
    parser.add_argument(
        "media_folder",
        nargs="?",
        help="Path to the media folder (default: MEDIAHIVE_PATH or current directory)",
    )
    parser.add_argument(
        "-l",
        "--listen",
        action="append",
        help=(f"Endpoint (default: localhost:{DEFAULT_PORT})."),
    )

    args = parser.parse_args()

    mediaroot = resolve_media_root(args.media_folder)
    os.environ["MEDIAHIVE_PATH"] = mediaroot.as_posix()

    dev = {"reload": True, "reload_dirs": ["mediahive"]}
    server.run(
        "mediahive.server:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        **(dev if DEVMODE else {}),
    )


if __name__ == "__main__":
    main()
