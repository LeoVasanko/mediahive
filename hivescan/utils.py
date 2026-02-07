"""Utility functions for paths, sizes, and timestamps."""

import os
import time
from pathlib import Path
from typing import Optional, List


# Default output folder name (created at common root of scanned paths)
DEFAULT_OUTPUT_FOLDER = ".mediahive"

# Threshold for considering atime "too close" to current time (1 hour)
_ATIME_FRESHNESS_THRESHOLD = 3600

# Resolution priority for quality sorting (higher = better)
RESOLUTION_PRIORITY = {
    "2160p": 4,
    "4K": 4,
    "1080p": 3,
    "1080i": 3,
    "720p": 2,
    "480p": 1,
}


def get_added_timestamp(path: Path) -> Optional[int]:
    """
    Get the timestamp when a torrent was added to the collection.

    Heuristic:
    - For directories: use ctime (most accurate for torrent folder creation)
    - For files: use atime unless it's too close to current time (suggesting
      the filesystem updates atime on reads), otherwise use max(mtime, ctime)

    Returns:
        Unix timestamp as int, or None if path doesn't exist
    """
    try:
        stat_info = path.stat()
    except OSError, PermissionError:
        return None

    if path.is_dir():
        return int(stat_info.st_ctime)

    now = time.time()
    atime = stat_info.st_atime

    if now - atime < _ATIME_FRESHNESS_THRESHOLD:
        return int(max(stat_info.st_mtime, stat_info.st_ctime))

    return int(atime)


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory recursively."""
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except OSError, PermissionError:
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Format size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def find_common_root(paths: List[Path]) -> Optional[Path]:
    """
    Find the common root directory for a list of paths.

    Returns None if paths are on different drives/mounts or have no common ancestor.
    """
    if not paths:
        return None

    # Resolve all paths to absolute
    resolved = [p.resolve() for p in paths]

    # Check if all paths are on the same drive (relevant for Windows, but also
    # catches cases where paths have completely different roots)
    try:
        # Get the device for each path
        devices = set()
        for p in resolved:
            # Find the first existing parent to get device info
            check_path = p
            while not check_path.exists() and check_path.parent != check_path:
                check_path = check_path.parent
            if check_path.exists():
                devices.add(os.stat(check_path).st_dev)

        if len(devices) > 1:
            # Paths are on different devices/drives
            return None
    except OSError:
        pass

    # Find common path prefix
    if len(resolved) == 1:
        # Single path - use its parent as root
        return resolved[0].parent if resolved[0].is_file() else resolved[0]

    # Get parts of each path
    all_parts = [p.parts for p in resolved]

    # Find common prefix
    common_parts = []
    for parts in zip(*all_parts):
        if len(set(parts)) == 1:
            common_parts.append(parts[0])
        else:
            break

    if not common_parts:
        return None

    return Path(*common_parts)


def make_relative_path(
    path: Optional[str], root: Optional[str] = None
) -> Optional[str]:
    """
    Convert an absolute path to a path relative to the given root.

    If root is None, returns the path unchanged.
    """
    if path is None:
        return None
    if root is None:
        return path
    root_str = str(root).rstrip("/")
    if path.startswith(root_str):
        rel = path[len(root_str) :]
        return rel.lstrip("/")
    return path


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
        name = name.replace(char, "_")
    name = name.strip(". ")
    return name


def get_media_folder_name(title: str, year: Optional[int], media_type: str) -> str:
    """Get the folder name for a media item."""
    sanitized_title = sanitize_filename(title)
    if media_type == "movie" and year:
        return f"{sanitized_title} ({year})"
    return sanitized_title


def get_media_folder_path(
    title: str, year: Optional[int], media_type: str, cover_dir: Path
) -> Path:
    """Get the full path to a media item's folder."""
    subdir = "movies" if media_type == "movie" else "series"
    folder_name = get_media_folder_name(title, year, media_type)
    return cover_dir / subdir / folder_name


def sort_by_quality(items: list, reverse: bool = True) -> None:
    """Sort items in-place by resolution quality and size.

    Works with both plain dicts (intermediate episode files) and
    msgspec.Struct instances (MovieVersion, EpisodeRelease).
    """

    def _val(v, key, default=None):
        return v.get(key, default) if isinstance(v, dict) else getattr(v, key, default)

    items.sort(
        key=lambda v: (
            RESOLUTION_PRIORITY.get(_val(v, "resolution", "") or "", 0),
            _val(v, "size", 0) or 0,
        ),
        reverse=reverse,
    )
