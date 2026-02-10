import argparse
import logging
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Hivescan server — continuous media scanning with live WS updates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m mediahive.hivescan /srv/media              # Scan a media root
  python -m mediahive.hivescan Z:\\                     # Windows drive
  python -m mediahive.hivescan /srv/media --port 9000  # Custom port

Exclude paths by creating .mediahive/scanignore (gitignore syntax).

The server exposes:
  WS   /ws          Live index updates & task progress
  POST /api/scan    Trigger a new scan
  GET  /api/status  Current server status
  GET  /api/index   Full index as JSON (HTTP fallback)
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

    media_root = Path(args.media_folder).resolve()
    if not media_root.exists() or not media_root.is_dir():
        print(f"Error: Folder does not exist: {media_root}")
        exit(1)

    os.environ["MEDIAHIVE_PATH"] = str(media_root)

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
