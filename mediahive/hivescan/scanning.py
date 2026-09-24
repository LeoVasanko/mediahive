"""File system scanning functions."""

import asyncio
import glob
import operator
import os
import threading
from collections import defaultdict
from pathlib import Path

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

# External subtitle file extensions
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}

# Non-language tokens that may follow the language in a sidecar filename
_SUBTITLE_FLAG_TOKENS = {"forced", "sdh", "cc", "hi", "dhi", "commentary", "signs"}

# ISO 639-1 -> ISO 639-2/B for common sidecar language tags, so they merge
# with the codes ffmpeg reports for embedded tracks.
_ISO_639_1_TO_639_2 = {
    "ar": "ara",
    "cs": "ces",
    "da": "dan",
    "de": "deu",
    "el": "ell",
    "en": "eng",
    "es": "esp",
    "fi": "fin",
    "fr": "fra",
    "he": "heb",
    "hi": "hin",
    "hu": "hun",
    "id": "ind",
    "it": "ita",
    "ja": "jpn",
    "ko": "kor",
    "nl": "nld",
    "no": "nor",
    "pl": "pol",
    "pt": "por",
    "ru": "rus",
    "sv": "swe",
    "th": "tha",
    "tr": "tur",
    "uk": "ukr",
    "vi": "vie",
    "zh": "zho",
}

# Caches for expensive operations.  These are per-scan only: the scanner
# clears them at the start of every scan.  Caching across scans is wrong —
# an empty result recorded before a download finished (or during a transient
# network-mount error) would stick for the process lifetime and report
# "no episodes found" for series that do have episodes.
_episode_files_cache: dict[str, dict[tuple[int, int], list[tuple[str, int]]]] = {}
_playable_file_cache: dict[str, str | None] = {}
_bluray_probe_file_cache: dict[str, str | None] = {}


def clear_scan_caches() -> None:
    """Drop all per-scan filesystem caches; called at the start of each scan."""
    _episode_files_cache.clear()
    _playable_file_cache.clear()
    _bluray_probe_file_cache.clear()


def _scandir_split(
    directory: Path,
    stop_event: threading.Event,
) -> tuple[list[Path], list[Path]]:
    """Return child directories and files, checking stop_event each iteration."""
    child_dirs: list[Path] = []
    child_files: list[Path] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if stop_event.is_set():
                return child_dirs, child_files
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            p = Path(entry.path)
            if is_dir:
                child_dirs.append(p)
            else:
                child_files.append(p)
    return child_dirs, child_files


def _scandir_files_with_suffix(
    directory: Path,
    suffixes: set[str],
    stop_event: threading.Event,
) -> list[Path]:
    """Return files in directory with a matching suffix, cancellable via stop_event."""
    files: list[Path] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if stop_event.is_set():
                return files
            try:
                if entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            p = Path(entry.path)
            if p.suffix.lower() in suffixes:
                files.append(p)
    return files


async def scan_downloads(base_pattern: str) -> list[ParsedContent]:
    """Scan download directories matching the pattern.

    Args:
        base_pattern: Glob pattern for finding download directories

    Returns:
        List of ParsedContent objects for each found download

    """
    exclude_patterns = [".torrents", "incomplete", ".incomplete"]
    results: list[ParsedContent] = []

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


async def find_episode_files(
    path: Path,
) -> dict[tuple[int, int], list[tuple[str, int]]]:
    """Find all episode video files in a directory.

    Args:
        path: Path to search (can be a season pack directory or single file)

    Returns:
        Dict mapping (season_num, episode_num) to list of (file_path, file_size) tuples

    """
    cache_key = path.as_posix()
    if cache_key in _episode_files_cache:
        return _episode_files_cache[cache_key]

    episodes: dict[tuple[int, int], list[tuple[str, int]]] = {}
    ap = AsyncPath(path)

    if await ap.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            ep_info = parse_episode_from_filename(path.name)
            if ep_info:
                episodes[ep_info] = [(path.as_posix(), (await ap.stat()).st_size)]
        _episode_files_cache[cache_key] = episodes
        return episodes

    stack = [path]
    while stack:
        current = stack.pop()
        stop_event = threading.Event()
        try:
            child_dirs, child_files = await asyncio.to_thread(
                _scandir_split,
                current,
                stop_event,
            )
        except asyncio.CancelledError:
            stop_event.set()
            raise
        except OSError, PermissionError:
            continue

        stack.extend(child_dirs)
        for f in child_files:
            if f.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if "sample" in f.name.lower():
                continue
            af = AsyncPath(f)
            try:
                ep_info = parse_episode_from_filename(f.name)
                if ep_info:
                    if ep_info not in episodes:
                        episodes[ep_info] = []
                    episodes[ep_info].append((f.as_posix(), (await af.stat()).st_size))
            except OSError, PermissionError:
                continue

    _episode_files_cache[cache_key] = episodes
    return episodes


async def find_playable_file(path: Path) -> str | None:
    """Find the main playable media file in a directory.

    For Blu-ray discs: Returns BDMV/MovieObject.bdmv (fallback: BDMV/index.bdmv)
    For other content: Returns the largest video file
    """
    cache_key = path.as_posix()
    if cache_key in _playable_file_cache:
        return _playable_file_cache[cache_key]

    ap = AsyncPath(path)

    if await ap.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            result = path.as_posix()
            _playable_file_cache[cache_key] = result
            return result
        _playable_file_cache[cache_key] = None
        return None

    # Check for Blu-ray disc structure
    bdmv_dir = path / "BDMV"
    bdmv_movieobject = bdmv_dir / "MovieObject.bdmv"
    bdmv_index = bdmv_dir / "index.bdmv"

    if await AsyncPath(bdmv_movieobject).exists():
        result = bdmv_movieobject.as_posix()
        _playable_file_cache[cache_key] = result
        return result

    if await AsyncPath(bdmv_index).exists():
        result = bdmv_index.as_posix()
        _playable_file_cache[cache_key] = result
        return result

    # Check for DVD disc structure
    video_ts_dir = path / "VIDEO_TS"
    video_ts_ifo = video_ts_dir / "VIDEO_TS.IFO"

    if await AsyncPath(video_ts_ifo).exists():
        result = video_ts_ifo.as_posix()
        _playable_file_cache[cache_key] = result
        return result

    # Check nested Blu-ray structure (e.g., MovieName/DISC1/BDMV/)
    try:
        stop_event = threading.Event()
        child_dirs, _ = await asyncio.to_thread(
            _scandir_split,
            path,
            stop_event,
        )
    except asyncio.CancelledError:
        stop_event.set()
        raise
    except OSError, PermissionError:
        child_dirs = []

    for subdir in child_dirs:
        nested_bdmv_dir = subdir / "BDMV"
        nested_movieobject = nested_bdmv_dir / "MovieObject.bdmv"
        nested_index = nested_bdmv_dir / "index.bdmv"

        if await AsyncPath(nested_movieobject).exists():
            result = nested_movieobject.as_posix()
            _playable_file_cache[cache_key] = result
            return result

        if await AsyncPath(nested_index).exists():
            result = nested_index.as_posix()
            _playable_file_cache[cache_key] = result
            return result

        nested_video_ts_dir = subdir / "VIDEO_TS"
        nested_video_ts_ifo = nested_video_ts_dir / "VIDEO_TS.IFO"

        if await AsyncPath(nested_video_ts_ifo).exists():
            result = nested_video_ts_ifo.as_posix()
            _playable_file_cache[cache_key] = result
            return result

    # Find largest video file
    video_files = []
    stack = [path]
    while stack:
        current = stack.pop()
        stop_event = threading.Event()
        try:
            child_dirs, child_files = await asyncio.to_thread(
                _scandir_split,
                current,
                stop_event,
            )
        except asyncio.CancelledError:
            stop_event.set()
            raise
        except OSError, PermissionError:
            continue

        stack.extend(child_dirs)
        for f in child_files:
            if f.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if "sample" in f.name.lower():
                continue
            af = AsyncPath(f)
            try:
                video_files.append((f.as_posix(), (await af.stat()).st_size))
            except OSError, PermissionError:
                continue

    if not video_files:
        _playable_file_cache[cache_key] = None
        return None

    video_files.sort(key=operator.itemgetter(1), reverse=True)
    result = video_files[0][0]
    _playable_file_cache[cache_key] = result
    return result


def _sidecar_subtitle_language(video_stem: str, filename: str) -> str | None:
    """Language tag from a sidecar subtitle name like `<stem>.esp.srt`, if any."""
    if not filename.startswith(video_stem + "."):
        return None
    suffix = Path(filename).suffix.lower()
    if suffix not in SUBTITLE_EXTENSIONS:
        return None
    middle = filename[len(video_stem) + 1 : -len(suffix)]
    tokens = [t for t in middle.split(".") if t]
    while tokens and tokens[-1].lower() in _SUBTITLE_FLAG_TOKENS:
        tokens.pop()
    if not tokens:
        return None
    code = tokens[-1].lower()
    if not code.isalpha() or not 2 <= len(code) <= 3:
        return None
    code = _ISO_639_1_TO_639_2.get(code, code)
    return None if code == "und" else code


def _scan_external_subtitle_languages(video_path: Path) -> list[str]:
    languages: list[str] = []
    with os.scandir(video_path.parent) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            lang = _sidecar_subtitle_language(video_path.stem, entry.name)
            if lang and lang not in languages:
                languages.append(lang)
    return languages


async def find_external_subtitle_languages(video_path: str | None) -> list[str]:
    """Languages of external subtitle files sitting next to a video file.

    Matches sidecars named `<stem>.<lang>.<ext>` (e.g. `Movie.esp.srt` ->
    ``esp``), optionally with flags like ``forced``/``sdh`` after the language.
    Bare `<stem>.<ext>` files carry no language tag and are ignored.
    """
    if not video_path or "://" in video_path or video_path.startswith("concat:"):
        return []
    path = Path(video_path)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return []
    try:
        return await asyncio.to_thread(_scan_external_subtitle_languages, path)
    except OSError, PermissionError:
        return []


async def find_metadata_probe_file(playable_path: str | None) -> str | None:
    """Resolve a path suitable for ffmpeg stream metadata probing.

    For regular files, returns ``playable_path`` unchanged.
    For Blu-ray control files (``*.bdmv``), returns the largest
    ``BDMV/STREAM/*.m2ts`` file, which ffmpeg can usually inspect even
    when direct BDMV probing is unsupported.
    For DVD control files (``*.ifo``), returns a ``concat:`` URI that
    covers all VOBs of the largest title set, giving ffmpeg the full
    main feature to probe.
    """
    if not playable_path:
        return None

    if not playable_path.lower().endswith(".bdmv"):
        # For DVD control files, build a concat URI covering the main title set
        if playable_path.lower().endswith(".ifo"):
            cache_key = playable_path
            if cache_key in _bluray_probe_file_cache:
                return _bluray_probe_file_cache[cache_key]

            playable = Path(playable_path)
            video_ts_dir = (
                playable.parent
                if playable.parent.name.upper() == "VIDEO_TS"
                else playable.parent
            )

            # Group VOBs by title set (VTS_XX_Y.VOB)
            title_sets: dict[str, list[tuple[str, int]]] = defaultdict(list)
            try:
                stop_event = threading.Event()
                vob_files = await asyncio.to_thread(
                    _scandir_files_with_suffix,
                    video_ts_dir,
                    {".vob"},
                    stop_event,
                )
            except asyncio.CancelledError:
                stop_event.set()
                raise
            except OSError, PermissionError:
                _bluray_probe_file_cache[cache_key] = None
                return None

            for f in vob_files:
                af = AsyncPath(f)
                name = f.name.upper()
                if not name.startswith("VTS_") or len(name) < 10:
                    continue
                try:
                    size = (await af.stat()).st_size
                except OSError, PermissionError:
                    continue
                ts_num = name[4:6]
                title_sets[ts_num].append((f.as_posix(), size))

            if not title_sets:
                _bluray_probe_file_cache[cache_key] = None
                return None

            # Pick the title set with the largest total size (main feature)
            best_ts = max(
                title_sets.keys(),
                key=lambda ts: sum(size for _, size in title_sets[ts]),
            )
            best_vobs = sorted(title_sets[best_ts], key=lambda x: x[0].upper())
            concat_uri = "concat:" + "|".join(path for path, _ in best_vobs)
            _bluray_probe_file_cache[cache_key] = concat_uri
            return concat_uri

        return playable_path

    cache_key = playable_path
    if cache_key in _bluray_probe_file_cache:
        return _bluray_probe_file_cache[cache_key]

    playable = Path(playable_path)
    bdmv_dir = (
        playable.parent if playable.parent.name.upper() == "BDMV" else playable.parent
    )
    stream_dir = bdmv_dir / "STREAM"

    ap_stream = AsyncPath(stream_dir)
    if not await ap_stream.exists() or not await ap_stream.is_dir():
        _bluray_probe_file_cache[cache_key] = None
        return None

    candidates: list[tuple[str, int]] = []
    stack = [stream_dir]
    while stack:
        current = stack.pop()
        stop_event = threading.Event()
        try:
            child_dirs, child_files = await asyncio.to_thread(
                _scandir_split,
                current,
                stop_event,
            )
        except asyncio.CancelledError:
            stop_event.set()
            raise
        except OSError, PermissionError:
            continue

        stack.extend(child_dirs)
        for f in child_files:
            if f.suffix.lower() != ".m2ts":
                continue
            af = AsyncPath(f)
            try:
                candidates.append((f.as_posix(), (await af.stat()).st_size))
            except OSError, PermissionError:
                continue

    if not candidates:
        _bluray_probe_file_cache[cache_key] = None
        return None

    candidates.sort(key=operator.itemgetter(1), reverse=True)
    result = candidates[0][0]
    _bluray_probe_file_cache[cache_key] = result
    return result


async def find_cover_image(
    title: str, year: int | None, media_type: str, cover_dir: Path
) -> str | None:
    """Find a cover image for the given media item."""
    media_folder = get_media_folder_path(title, year, media_type, cover_dir)
    cover_path = media_folder / "cover.jpg"
    if await AsyncPath(cover_path).exists():
        return cover_path.as_posix()

    # Legacy structure fallback
    subdir = "movies" if media_type == "movie" else "series"
    if media_type == "movie" and year:
        legacy_path = cover_dir / subdir / f"{sanitize_filename(title)} ({year}).jpg"
        if await AsyncPath(legacy_path).exists():
            return legacy_path.as_posix()

    legacy_path = cover_dir / subdir / f"{sanitize_filename(title)}.jpg"
    if await AsyncPath(legacy_path).exists():
        return legacy_path.as_posix()

    return None
