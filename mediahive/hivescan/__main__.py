import argparse
import json
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

The server exposes per-root endpoints:
  WS   /api/roots/{root_id}/ws  Live index updates & task progress
  POST /api/roots/{root_id}/scan    Trigger a new scan
  GET  /api/roots/{root_id}/status  Current root status
  GET  /api/roots/{root_id}/index   Full index as JSON (HTTP fallback)
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

    # Defer filesystem validation to the server; pass raw path via env.
    media_root = Path(args.media_folder).expanduser()
    os.environ["MEDIAHIVE_ROOTS"] = json.dumps({
        media_root.name or "media": media_root.as_posix()
    })

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
