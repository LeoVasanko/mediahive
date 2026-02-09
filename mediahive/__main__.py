import argparse
import os
from pathlib import Path

from fastapi_vue import server

DEFAULT_PORT = 8420
DEVMODE = bool(os.getenv("MEDIAHIVE_FRONTEND_URL"))


def main():
    parser = argparse.ArgumentParser(description="MediaHive - Media scanning, indexing, and streaming")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    # Server subcommand
    server_parser = subparsers.add_parser('server', help='Run the MediaHive streaming server')
    server_parser.add_argument(
        "media_folder",
        nargs="?",
        help="Path to the media folder (default: MEDIAHIVE_PATH or current directory)",
    )
    server_parser.add_argument(
        "-l",
        "--listen",
        action="append",
        help=(f"Endpoint (default: localhost:{DEFAULT_PORT})."),
    )

    # Scan subcommand
    scan_parser = subparsers.add_parser('scan', help='Run the Hivescan media scanning server',
                                        description="Hivescan server — continuous media scanning with live WS updates.",
                                        formatter_class=argparse.RawDescriptionHelpFormatter,
                                        epilog="""
Examples:
  mediahive scan /path/to/torrents/*              # Scan paths, auto-detect common root
  mediahive scan /mnt/disk1/* /mnt/disk2/*        # Scan multiple locations
  mediahive scan /torrents/* -o /srv/media        # Override output directory
  mediahive scan /torrents/* --port 9000          # Custom port

The server exposes:
  WS   /ws          Live index updates & task progress
  POST /api/scan    Trigger a new scan
  GET  /api/status  Current server status
  GET  /api/index   Full index as JSON (HTTP fallback)
        """)
    scan_parser.add_argument(
        "paths",
        nargs="+",
        help="Folders or glob patterns to scan for downloads",
    )
    scan_parser.add_argument(
        "-o",
        "--output-dir",
        metavar="DIR",
        help="Output directory for index and covers (default: .mediahive at common root)",
    )
    scan_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    scan_parser.add_argument(
        "--port",
        type=int,
        default=8421,
        help="Port to listen on (default: 8421)",
    )

    args = parser.parse_args()

    if args.command == 'server':
        # Determine media folder
        match Path(
            args.media_folder or os.environ.get("MEDIAHIVE_PATH") or Path.cwd()
        ).parts:
            case (*rest, ".mediahive", "index.json"):
                ...
            case (*rest, ".mediahive"):
                ...
            case rest:
                ...
        mediaroot = Path(*rest).resolve()
        if not mediaroot.exists() or not mediaroot.is_dir():
            print(f"Error: Folder does not exist: {mediaroot}")
            exit(1)
        os.environ["MEDIAHIVE_PATH"] = mediaroot.as_posix()
        dev = {"reload": True, "reload_dirs": ["mediahive"]}
        server.run(
            "mediahive.server:app",
            listen=args.listen,
            default_port=DEFAULT_PORT,
            **(dev if DEVMODE else {}),
        )
    elif args.command == 'scan':
        # Pass configuration via environment variables (read by server.py lifespan)
        os.environ["HIVESCAN_PATHS"] = os.pathsep.join(args.paths)
        if args.output_dir:
            os.environ["HIVESCAN_OUTPUT"] = args.output_dir

        from mediahive.hivescan.server import run
        run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
