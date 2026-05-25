"""Utility functions for paths, sizes, and timestamps."""

import time
from pathlib import Path

from aiopathlib import AsyncPath

# Default output folder name (created at common root of scanned paths)
DEFAULT_OUTPUT_FOLDER = ".mediahive"

# Threshold for considering atime "too close" to current time (1 hour)
_ATIME_FRESHNESS_THRESHOLD = 3600

# Resolution priority for quality sorting (higher = better)
RESOLUTION_PRIORITY = {
    "8K": 5,
    "4K": 4,
    "FHD": 3,
    "HD": 2,
    "SD": 1,
    # Backward compatibility for existing snapshot data
    "4320p": 5,
    "2160p": 4,
    "UHD": 4,
    "1080p": 3,
    "1080i": 3,
    "720p": 2,
    "576p": 1,
    "480p": 1,
}


def classify_resolution_from_dimensions(
    width: int | None, height: int | None
) -> str | None:
    """Map raw frame dimensions to SD/HD/FHD/4K/8K buckets.

    Uses the smallest standard frame bucket that can contain the source frame,
    which keeps cropped cinematic encodes in their expected class.
    """
    if not width or not height or width <= 0 or height <= 0:
        return None

    long_edge = max(width, height)
    short_edge = min(width, height)

    buckets = [
        (1024, 576, "SD"),
        (1280, 720, "HD"),
        (1920, 1080, "FHD"),
        (4096, 2160, "4K"),
        (8192, 4320, "8K"),
    ]

    for max_w, max_h, label in buckets:
        if long_edge <= max_w and short_edge <= max_h:
            return label

    return "8K"


def normalize_resolution_label(value: str | None) -> str | None:
    """Normalize PTN/legacy resolution text into SD/HD/FHD/4K/8K labels."""
    if not value:
        return None

    normalized = str(value).strip().upper()
    mapping = {
        "SD": "SD",
        "HD": "HD",
        "FHD": "FHD",
        "4K": "4K",
        "8K": "8K",
        "4320P": "8K",
        "2160P": "4K",
        "UHD": "4K",
        "1080P": "FHD",
        "1080I": "FHD",
        "720P": "HD",
        "576P": "SD",
        "480P": "SD",
    }
    return mapping.get(normalized)


async def get_added_timestamp(path: Path) -> int | None:
    """Get the timestamp when a torrent was added to the collection.

    Heuristic:
    - For directories: use ctime (most accurate for torrent folder creation)
    - For files: use atime unless it's too close to current time (suggesting
      the filesystem updates atime on reads), otherwise use max(mtime, ctime)

    Returns:
        Unix timestamp as int, or None if path doesn't exist

    """
    ap = AsyncPath(path)
    try:
        stat_info = await ap.stat()
    except OSError, PermissionError:
        return None

    if await ap.is_dir():
        return int(stat_info.st_ctime)

    now = time.time()
    atime = stat_info.st_atime

    if now - atime < _ATIME_FRESHNESS_THRESHOLD:
        return int(max(stat_info.st_mtime, stat_info.st_ctime))

    return int(atime)


async def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory recursively."""
    ap = AsyncPath(path)
    total = 0
    try:
        if await ap.is_file():
            return (await ap.stat()).st_size
        for item in ap.rglob("*"):
            if await AsyncPath(item).is_file():
                total += (await AsyncPath(item).stat()).st_size
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


async def find_common_root(paths: list[Path]) -> Path | None:
    """Find the common root directory for a list of paths.

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
            while (
                not await AsyncPath(check_path).exists()
                and check_path.parent != check_path
            ):
                check_path = check_path.parent
            if await AsyncPath(check_path).exists():
                devices.add((await AsyncPath(check_path).stat()).st_dev)

        if len(devices) > 1:
            # Paths are on different devices/drives
            return None
    except OSError:
        pass

    # Find common path prefix
    if len(resolved) == 1:
        # Single path - use its parent as root
        return (
            resolved[0].parent
            if await AsyncPath(resolved[0]).is_file()
            else resolved[0]
        )

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


def make_relative_path(path: str | None, root: str | None = None) -> str | None:
    """Convert an absolute path to a posix-style path relative to the given root.

    If root is None, returns the path as a posix string unchanged.
    """
    if path is None:
        return None
    p = Path(path)
    if root is None:
        return p.as_posix()
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
        name = name.replace(char, "_")
    name = name.strip(". ")
    return name


def get_media_folder_name(title: str, year: int | None, media_type: str) -> str:
    """Get the folder name for a media item."""
    sanitized_title = sanitize_filename(title)
    if media_type == "movie" and year:
        return f"{sanitized_title} ({year})"
    return sanitized_title


def get_media_folder_path(
    title: str, year: int | None, media_type: str, cover_dir: Path
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
            _val(v, "path", "") or "",
        ),
        reverse=reverse,
    )
