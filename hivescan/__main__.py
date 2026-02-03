"""CLI entry point for hivescan."""

import argparse
import glob
import sys
from pathlib import Path

from hivescan.scanning import scan_downloads, categorize_downloads
from hivescan.indexer import generate_media_index
from hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root
from hivescan.tmdb_client import set_cache_dir


def main():
    parser = argparse.ArgumentParser(
        description="Scan downloaded torrents and generate a media index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/torrents/*          # Scan paths, auto-detect common root
  %(prog)s /mnt/disk1/* /mnt/disk2/*    # Scan multiple locations
  %(prog)s /torrents/* -o /srv/media    # Override output directory
  %(prog)s /torrents/* --no-showreels   # Skip showreel generation
  %(prog)s /torrents/* --no-covers      # Skip cover/backdrop downloads

Output:
  By default, creates a .mediahive folder at the common root of scanned paths.
  All paths in the index are stored relative to the .mediahive parent folder.
  Use -o/--output-dir to override the output location.
        """,
    )

    parser.add_argument(
        "paths",
        nargs="+",
        help="Folders or glob patterns to scan for downloads",
    )
    parser.add_argument(
        "-o", "--output-dir",
        metavar="DIR",
        help=f"Output directory for index and covers (default: {DEFAULT_OUTPUT_FOLDER} at common root)",
    )
    parser.add_argument(
        "--no-showreels",
        action="store_true",
        help="Skip generating showreel images",
    )
    parser.add_argument(
        "--no-covers",
        action="store_true",
        help="Skip downloading cover and backdrop images from TMDb",
    )

    args = parser.parse_args()

    # Expand glob patterns and collect all paths
    all_paths = []
    for pattern in args.paths:
        expanded = glob.glob(pattern)
        if expanded:
            all_paths.extend(Path(p) for p in expanded)
        else:
            # Treat as literal path if no glob match
            all_paths.append(Path(pattern))

    if not all_paths:
        print("Error: No paths found to scan", file=sys.stderr)
        sys.exit(1)

    # Determine output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
        media_root = output_dir.parent
    else:
        # Find common root of all scan paths
        media_root = find_common_root(all_paths)
        if media_root is None:
            print("Error: Cannot determine common root for paths (different drives?)", file=sys.stderr)
            print("       Use -o/--output-dir to specify output location", file=sys.stderr)
            sys.exit(1)
        output_dir = media_root / DEFAULT_OUTPUT_FOLDER

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "index.json"

    # Set TMDb cache directory within output dir
    set_cache_dir(output_dir / ".tmdb-cache")

    print(f"Media root: {media_root}")
    print(f"Output dir: {output_dir}")
    print(f"Scanning {len(all_paths)} paths...")

    # Scan all paths
    downloads = []
    for path in all_paths:
        if path.is_dir():
            # Scan directory contents
            for item in path.iterdir():
                if not item.name.startswith("."):
                    from hivescan.parsing import parse_download
                    downloads.append(parse_download(item))
        elif path.exists():
            from hivescan.parsing import parse_download
            downloads.append(parse_download(path))

    print(f"Found {len(downloads)} items")

    categories = categorize_downloads(downloads)

    generate_media_index(
        categories,
        index_path,
        output_dir,
        media_root=media_root,
        fetch_covers=not args.no_covers,
        generate_showreels=not args.no_showreels,
    )


if __name__ == "__main__":
    main()
