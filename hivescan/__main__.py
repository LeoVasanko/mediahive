"""Entry point for hivescan — launches the scanning FastAPI server."""

import argparse
import os


def main():
    parser = argparse.ArgumentParser(
        description="Hivescan server — continuous media scanning with live WS updates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/torrents/*              # Scan paths, auto-detect common root
  %(prog)s /mnt/disk1/* /mnt/disk2/*        # Scan multiple locations
  %(prog)s /torrents/* -o /srv/media        # Override output directory
  %(prog)s /torrents/* --port 9000          # Custom port

The server exposes:
  WS   /ws          Live index updates & task progress
  POST /api/scan    Trigger a new scan
  GET  /api/status  Current server status
  GET  /api/index   Full index as JSON (HTTP fallback)
        """,
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

    # Pass configuration via environment variables (read by server.py lifespan)
    os.environ["HIVESCAN_PATHS"] = os.pathsep.join(args.paths)
    if args.output_dir:
        os.environ["HIVESCAN_OUTPUT"] = args.output_dir

    from hivescan.server import run

    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
