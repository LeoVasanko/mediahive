import argparse
import asyncio
import glob
import logging
import os
from pathlib import Path

from mediahive.hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root


def main():
    parser = argparse.ArgumentParser(
        description="Hivescan server — continuous media scanning with live WS updates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m mediahive.hivescan /path/to/torrents/*              # Scan paths, auto-detect common root
  python -m mediahive.hivescan /mnt/disk1/* /mnt/disk2/*        # Scan multiple locations
  python -m mediahive.hivescan /torrents/* -o /srv/media        # Override output directory
  python -m mediahive.hivescan /torrents/* --port 9000          # Custom port

The server exposes:
  WS   /ws          Live index updates & task progress
  POST /api/scan    Trigger a new scan
  GET  /api/status  Current server status
  GET  /api/index   Full index as JSON (HTTP fallback)
        """
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Folders or glob patterns to scan for downloads",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        metavar="DIR",
        help="Output directory for index and covers (default: .mediahive at common root)",
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

    # Derive the media root so we can set MEDIAHIVE_PATH
    all_paths: list[Path] = []
    for pattern in args.paths:
        expanded = glob.glob(pattern)
        if expanded:
            all_paths.extend(Path(p) for p in expanded)
        else:
            all_paths.append(Path(pattern))

    if args.output_dir:
        media_root = Path(args.output_dir).parent
    else:
        media_root = asyncio.run(find_common_root(all_paths))
        if media_root is None:
            print("Error: Cannot determine common root; use -o to set output directory")
            exit(1)

    # Configure environment for the mediahive server + scanner
    os.environ["MEDIAHIVE_PATH"] = str(media_root.resolve())
    os.environ["HIVESCAN_PATHS"] = os.pathsep.join(args.paths)
    if args.output_dir:
        os.environ["HIVESCAN_OUTPUT"] = args.output_dir

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    import uvicorn
    uvicorn.run("mediahive.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
