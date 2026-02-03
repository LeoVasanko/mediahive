#!/usr/bin/env python3
"""
Torrent Scanner - Scans for .torrent files and analyzes their trackers.
"""

import argparse
import glob
import hashlib
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator

import bencodepy
from rtorrent_client import RTorrentClient


@dataclass
class TorrentInfo:
    """Information extracted from a torrent file."""
    path: Path
    name: str
    trackers: list[str]
    size: int | None = None
    files: list[str] | None = None
    info_hash: str | None = None
    is_multi_file: bool = False

    def has_tracker(self, domain: str) -> bool:
        """Check if any tracker URL contains the given domain."""
        return any(domain.lower() in tracker.lower() for tracker in self.trackers)

    def get_download_directory(self) -> Path:
        """Get the download directory (parent of .torrents folder).

        Assumes .torrent files are in <download_dir>/.torrents/
        so the actual downloads are one level up.
        """
        return self.path.parent.parent

    def get_expected_data_path(self) -> Path:
        """
        Get the expected path where downloaded data should exist.

        For multi-file torrents: download_dir/torrent_name/ (directory)
        For single-file torrents: download_dir/torrent_name (file)
        """
        return self.get_download_directory() / self.name

    def verify_download_exists(self) -> tuple[bool, str]:
        """
        Verify that the downloaded data exists on disk.

        Returns:
            Tuple of (exists: bool, message: str)
        """
        expected_path = self.get_expected_data_path()

        if self.is_multi_file:
            # Multi-file torrent: expect a directory
            if not expected_path.exists():
                return False, f"Directory not found: {expected_path}"
            if not expected_path.is_dir():
                return False, f"Expected directory but found file: {expected_path}"
            # Optionally check if at least some files exist
            existing_files = list(expected_path.rglob("*"))
            file_count = sum(1 for f in existing_files if f.is_file())
            if file_count == 0:
                return False, f"Directory exists but is empty: {expected_path}"
            return True, f"Directory exists with {file_count} files"
        else:
            # Single-file torrent: expect a file
            if not expected_path.exists():
                return False, f"File not found: {expected_path}"
            if expected_path.is_dir():
                return False, f"Expected file but found directory: {expected_path}"
            return True, f"File exists: {expected_path}"


def parse_torrent(filepath: Path) -> TorrentInfo | None:
    """
    Parse a .torrent file and extract relevant information.

    Args:
        filepath: Path to the .torrent file

    Returns:
        TorrentInfo object or None if parsing fails
    """
    try:
        with open(filepath, 'rb') as f:
            data = bencodepy.decode(f.read())
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return None

    # Extract trackers
    trackers = []

    # Main announce URL
    if b'announce' in data:
        announce = data[b'announce']
        if isinstance(announce, bytes):
            trackers.append(announce.decode('utf-8', errors='replace'))

    # Announce list (multiple trackers)
    if b'announce-list' in data:
        for tier in data[b'announce-list']:
            for tracker in tier:
                if isinstance(tracker, bytes):
                    url = tracker.decode('utf-8', errors='replace')
                    if url not in trackers:
                        trackers.append(url)

    # Extract name
    info = data.get(b'info', {})
    name = info.get(b'name', b'Unknown').decode('utf-8', errors='replace')

    # Calculate info hash
    info_hash = hashlib.sha1(bencodepy.encode(info)).hexdigest().upper()

    # Extract size and files
    size = None
    files = None
    is_multi_file = False

    if b'length' in info:
        # Single file torrent
        size = info[b'length']
        files = [name]
        is_multi_file = False
    elif b'files' in info:
        # Multi-file torrent
        files = []
        size = 0
        is_multi_file = True
        for file_info in info[b'files']:
            file_path = '/'.join(
                p.decode('utf-8', errors='replace')
                for p in file_info.get(b'path', [])
            )
            files.append(file_path)
            size += file_info.get(b'length', 0)

    return TorrentInfo(
        path=filepath,
        name=name,
        trackers=trackers,
        size=size,
        files=files,
        info_hash=info_hash,
        is_multi_file=is_multi_file,
    )


def scan_torrent_directories(paths: list[str]) -> Iterator[Path]:
    """
    Scan directories for .torrent files.

    Args:
        paths: List of directory paths or glob patterns to scan

    Yields:
        Path objects for each .torrent file found
    """
    for pattern in paths:
        for dir_path in glob.glob(pattern):
            torrent_dir = Path(dir_path)
            if torrent_dir.is_dir():
                for torrent_file in torrent_dir.glob("*.torrent"):
                    yield torrent_file


def find_torrents_with_tracker(tracker_domain: str,
                                paths: list[str]) -> list[TorrentInfo]:
    """
    Find all torrents that have a specific tracker domain.

    Args:
        tracker_domain: Domain to search for in tracker URLs (e.g., "hdbits.org")
        paths: List of directory paths or glob patterns to scan

    Returns:
        List of TorrentInfo objects for matching torrents
    """
    matching_torrents = []

    for torrent_path in scan_torrent_directories(paths):
        info = parse_torrent(torrent_path)
        if info and info.has_tracker(tracker_domain):
            matching_torrents.append(info)

    return matching_torrents


def format_size(size_bytes: int | None) -> str:
    """Format bytes as human-readable size."""
    if size_bytes is None:
        return "Unknown"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def main():
    """Main entry point for the torrent scanner."""
    parser = argparse.ArgumentParser(
        description="Scan and manage torrent files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/torrents*/.torrents/
  %(prog)s /mnt/disk1/torrents/.torrents/ /mnt/disk2/torrents/.torrents/
  %(prog)s /torrents*/.torrents/ --tracker hdbits.org
  %(prog)s /torrents*/.torrents/ --dry
        """,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Directories or glob patterns containing .torrent files",
    )
    parser.add_argument("--dry", action="store_true", help="Dry run - show what would be done without making changes")
    parser.add_argument("--tracker", default="hdbits.org",
                        help="Tracker domain to filter by (default: hdbits.org)")
    args = parser.parse_args()

    dry_run = args.dry
    tracker_domain = args.tracker

    # Expand glob patterns
    expanded_paths = []
    for pattern in args.paths:
        matches = glob.glob(pattern)
        if matches:
            expanded_paths.extend(matches)
        else:
            expanded_paths.append(pattern)

    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE - No changes will be made")
        print("=" * 60)

    print(f"Scanning for torrents...")
    print(f"Search paths: {expanded_paths}")
    print("-" * 60)

    # Parse all torrents
    all_torrents: list[TorrentInfo] = []
    for torrent_path in scan_torrent_directories(expanded_paths):
        info = parse_torrent(torrent_path)
        if info:
            all_torrents.append(info)

    # Separate by tracker
    with_hdbits = [t for t in all_torrents if t.has_tracker(tracker_domain)]
    without_hdbits = [t for t in all_torrents if not t.has_tracker(tracker_domain)]

    # Print stats
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total torrents scanned: {len(all_torrents)}")
    print(f"With {tracker_domain}: {len(with_hdbits)}")
    print(f"Without {tracker_domain}: {len(without_hdbits)}")

    # Add hdbits torrents to rtorrent
    if with_hdbits:
        print(f"\n{'='*60}")
        print(f"VERIFYING DOWNLOADS & ADDING TO RTORRENT")
        print(f"{'='*60}")

        # First, verify which torrents have their data
        verified = []
        missing_data = []

        for torrent in with_hdbits:
            exists, message = torrent.verify_download_exists()
            if exists:
                verified.append(torrent)
            else:
                missing_data.append((torrent, message))

        print(f"\nVerification results:")
        print(f"  Downloads found: {len(verified)}")
        print(f"  Downloads missing: {len(missing_data)}")

        # Report missing downloads
        if missing_data:
            print(f"\n{'='*60}")
            print(f"TORRENTS WITH MISSING DATA (will not add)")
            print(f"{'='*60}")
            for torrent, message in missing_data:
                print(f"\n  Name: {torrent.name}")
                print(f"  Torrent: {torrent.path}")
                print(f"  Reason: {message}")

        # Now add verified torrents to rtorrent
        if verified:
            print(f"\n{'='*60}")
            print(f"ADDING {len(verified)} VERIFIED TORRENTS TO RTORRENT")
            print(f"{'='*60}")

            client = RTorrentClient()
            loaded_hashes = client.get_loaded_hashes()
            print(f"Currently loaded in rtorrent: {len(loaded_hashes)} torrents")

            added = 0
            skipped = 0
            failed = 0

            for torrent in verified:
                if torrent.info_hash and torrent.info_hash in loaded_hashes:
                    print(f"Skipping (already loaded): {torrent.name}")
                    skipped += 1
                else:
                    download_dir = torrent.get_download_directory()
                    if dry_run:
                        print(f"Would add: {torrent.name}")
                        print(f"  Download dir: {download_dir}")
                        added += 1
                    else:
                        print(f"Adding: {torrent.name}")
                        print(f"  Download dir: {download_dir}")
                        if client.load_torrent(torrent.path, download_dir):
                            added += 1
                        else:
                            failed += 1

            if dry_run:
                print(f"\nDry run: {added} would be added, {skipped} already loaded")
            else:
                print(f"\nRtorrent results: {added} added, {skipped} skipped, {failed} failed")

    # Clean up unregistered torrents from rtorrent
    print(f"\n{'='*60}")
    print(f"CHECKING FOR UNREGISTERED TORRENTS")
    print(f"{'='*60}")

    client = RTorrentClient()
    unregistered = client.get_unregistered_torrents()

    if unregistered:
        print(f"Found {len(unregistered)} unregistered torrent(s):\n")

        removed_from_rtorrent = 0
        removed_torrent_files = 0
        removed_downloads = 0

        for torrent_info in unregistered:
            # Determine the download path (base_path is the actual file/folder)
            download_path = Path(torrent_info['base_path']) if torrent_info['base_path'] else None

            if dry_run:
                status = "[DRY]"
                if download_path:
                    print(f"  {status} {download_path}")
                else:
                    print(f"  {status} {torrent_info['name']} (no data path)")
            else:
                # Remove from rtorrent (keeps downloaded files)
                if client.remove_torrent(torrent_info['hash']):
                    removed_from_rtorrent += 1

                    # Delete the .torrent file if it exists
                    tied_file = torrent_info['tied_file']
                    if tied_file:
                        torrent_file = Path(tied_file)
                        if torrent_file.exists():
                            try:
                                torrent_file.unlink()
                                removed_torrent_files += 1
                            except Exception:
                                pass

                    # Delete the downloaded files
                    if download_path and download_path.exists():
                        try:
                            if download_path.is_dir():
                                shutil.rmtree(download_path)
                            else:
                                download_path.unlink()
                            removed_downloads += 1
                            print(f"  [DEL] {download_path}")
                        except Exception as e:
                            print(f"  [ERR] {download_path}: {e}")
                    else:
                        print(f"  [DEL] {torrent_info['name']} (no data)")
                else:
                    print(f"  [ERR] {torrent_info['name']}: failed to remove from rtorrent")

        print()
        if dry_run:
            print(f"Dry run: {len(unregistered)} would be removed (rtorrent + .torrent + downloads)")
        else:
            print(f"Cleanup: {removed_from_rtorrent} from rtorrent, {removed_torrent_files} .torrents, {removed_downloads} downloads")
    else:
        print("No unregistered torrents found.")

    # List torrents without hdbits.org
    if without_hdbits:
        print(f"\n{'='*60}")
        print(f"TORRENTS WITHOUT {tracker_domain.upper()}")
        print(f"{'='*60}")
        for torrent in without_hdbits:
            print(f"\nName: {torrent.name}")
            print(f"Path: {torrent.path}")
            print(f"Size: {format_size(torrent.size)}")
            if torrent.trackers:
                print(f"Trackers:")
                for tracker in torrent.trackers:
                    print(f"  - {tracker}")
            else:
                print("Trackers: (none)")

        # Remove the non-hdbits torrent files
        print(f"\n{'='*60}")
        if dry_run:
            print(f"WOULD REMOVE {len(without_hdbits)} TORRENT FILE(S)")
            print(f"{'='*60}")
            for torrent in without_hdbits:
                print(f"Would remove: {torrent.path}")
        else:
            print(f"REMOVING {len(without_hdbits)} TORRENT FILE(S)")
            print(f"{'='*60}")
            for torrent in without_hdbits:
                try:
                    torrent.path.unlink()
                    print(f"Removed: {torrent.path}")
                except Exception as e:
                    print(f"Failed to remove {torrent.path}: {e}")


if __name__ == "__main__":
    main()
