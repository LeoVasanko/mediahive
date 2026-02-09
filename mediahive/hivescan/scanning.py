"""File system scanning functions."""

import asyncio
import glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiopathlib import AsyncPath

from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.parsing import parse_download, parse_episode_from_filename
from mediahive.hivescan.utils import get_media_folder_path, sanitize_filename


# Video file extensions
VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".ts",
    ".m2ts",
}

# Caches for expensive operations
_episode_files_cache: Dict[str, Dict[Tuple[int, int], List[Tuple[str, int]]]] = {}
_playable_file_cache: Dict[str, Optional[str]] = {}


async def scan_downloads(base_pattern: str) -> List[ParsedContent]:
    """
    Scan download directories matching the pattern.

    Args:
        base_pattern: Glob pattern for finding download directories

    Returns:
        List of ParsedContent objects for each found download
    """
    exclude_patterns = [".torrents", "incomplete", ".incomplete"]
    results: List[ParsedContent] = []

    paths = await asyncio.to_thread(glob.glob, base_pattern)
    for path_str in paths:
        path = Path(path_str)
        ap = AsyncPath(path)

        if path.name.startswith("."):
            continue

        if any(excl.lower() in path.name.lower() for excl in exclude_patterns):
            continue

        if not await ap.exists():
            continue

        results.append(await parse_download(path))

    return results


def categorize_downloads(
    downloads: list[ParsedContent],
) -> dict[ContentType, list[ParsedContent]]:
    """Categorize downloads by content type."""
    categories: dict[ContentType, list[ParsedContent]] = {
        ContentType.MOVIE: [],
        ContentType.SERIES: [],
        ContentType.OTHER: [],
    }

    for download in downloads:
        categories[download.content_type].append(download)

    return categories


async def find_episode_files(path: Path) -> Dict[Tuple[int, int], List[Tuple[str, int]]]:
    """
    Find all episode video files in a directory.

    Args:
        path: Path to search (can be a season pack directory or single file)

    Returns:
        Dict mapping (season_num, episode_num) to list of (file_path, file_size) tuples
    """
    cache_key = str(path)
    if cache_key in _episode_files_cache:
        return _episode_files_cache[cache_key]

    episodes: Dict[Tuple[int, int], List[Tuple[str, int]]] = {}
    ap = AsyncPath(path)

    if await ap.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            ep_info = parse_episode_from_filename(path.name)
            if ep_info:
                episodes[ep_info] = [(str(path), (await ap.stat()).st_size)]
        _episode_files_cache[cache_key] = episodes
        return episodes

    try:
        for f in ap.rglob("*"):
            af = AsyncPath(f)
            if await af.is_file() and Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                if "sample" in Path(f).name.lower():
                    continue
                ep_info = parse_episode_from_filename(Path(f).name)
                if ep_info:
                    if ep_info not in episodes:
                        episodes[ep_info] = []
                    episodes[ep_info].append((str(f), (await af.stat()).st_size))
    except (OSError, PermissionError):
        pass

    _episode_files_cache[cache_key] = episodes
    return episodes


async def find_playable_file(path: Path) -> Optional[str]:
    """
    Find the main playable media file in a directory.

    For Blu-ray discs: Returns BDMV/index.bdmv
    For other content: Returns the largest video file
    """
    cache_key = str(path)
    if cache_key in _playable_file_cache:
        return _playable_file_cache[cache_key]

    ap = AsyncPath(path)

    if await ap.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            result = str(path)
            _playable_file_cache[cache_key] = result
            return result
        _playable_file_cache[cache_key] = None
        return None

    # Check for Blu-ray disc structure
    bdmv_index = path / "BDMV" / "index.bdmv"
    if await AsyncPath(bdmv_index).exists():
        result = str(bdmv_index)
        _playable_file_cache[cache_key] = result
        return result

    # Check nested Blu-ray structure (e.g., MovieName/DISC1/BDMV/)
    try:
        for subdir in ap.iterdir():
            if await AsyncPath(subdir).is_dir():
                nested_bdmv = Path(subdir) / "BDMV" / "index.bdmv"
                if await AsyncPath(nested_bdmv).exists():
                    result = str(nested_bdmv)
                    _playable_file_cache[cache_key] = result
                    return result
    except (OSError, PermissionError):
        pass

    # Find largest video file
    video_files = []
    try:
        for f in ap.rglob("*"):
            af = AsyncPath(f)
            if await af.is_file() and Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                if "sample" in Path(f).name.lower():
                    continue
                video_files.append((str(f), (await af.stat()).st_size))
    except (OSError, PermissionError):
        pass

    if not video_files:
        _playable_file_cache[cache_key] = None
        return None

    video_files.sort(key=lambda x: x[1], reverse=True)
    result = video_files[0][0]
    _playable_file_cache[cache_key] = result
    return result


async def find_cover_image(
    title: str, year: Optional[int], media_type: str, cover_dir: Path
) -> Optional[str]:
    """Find a cover image for the given media item."""
    media_folder = get_media_folder_path(title, year, media_type, cover_dir)
    cover_path = media_folder / "cover.jpg"
    if await AsyncPath(cover_path).exists():
        return str(cover_path)

    # Legacy structure fallback
    subdir = "movies" if media_type == "movie" else "series"
    if media_type == "movie" and year:
        legacy_path = cover_dir / subdir / f"{sanitize_filename(title)} ({year}).jpg"
        if await AsyncPath(legacy_path).exists():
            return str(legacy_path)

    legacy_path = cover_dir / subdir / f"{sanitize_filename(title)}.jpg"
    if await AsyncPath(legacy_path).exists():
        return str(legacy_path)

    return None

