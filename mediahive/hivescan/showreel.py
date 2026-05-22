"""
Showreel generation module for media preview clips.

Generates short video clips (reels) from movies and TV episodes using ffmpeg.
Supports automatic black bar detection and removal, hardware-accelerated encoding,
and HDR passthrough.
"""

import asyncio
import logging
import re
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aiopathlib import AsyncPath

from mediahive.hivescan.utils import classify_resolution_from_dimensions

logger = logging.getLogger("hivescan.showreel")


# Suppress console windows when spawning subprocesses on Windows
async def _subprocess_exec(*args, **kwargs):
    """Wrap asyncio.create_subprocess_exec to hide console windows on Windows."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return await asyncio.create_subprocess_exec(*args, **kwargs)


# Showreel timestamp positions in seconds (5, 10, 15, 20, 25 minutes)
SHOWREEL_TIMESTAMPS = [5 * 60, 10 * 60, 15 * 60, 20 * 60, 25 * 60]
REEL_SOURCE_EXTENSIONS = [".webm", ".mp4"]
# Temporary rollout switch: keep platform-native MP4/H.265 path available but disabled.
ENABLE_PLATFORM_NATIVE_REELS = False


def get_reel_source_extensions() -> list[str]:
    """Return reel source extensions in client preference order."""
    return REEL_SOURCE_EXTENSIONS.copy()


def _to_media_path(path: Path, media_root: Optional[Path] = None) -> str:
    """Convert an absolute reel file path to a media-root-relative path when possible."""
    if media_root:
        try:
            return str(path.relative_to(media_root))
        except ValueError:
            return str(path)
    return str(path)


def get_reel_extension() -> str:
    """Return the platform-native reel file extension."""
    if not ENABLE_PLATFORM_NATIVE_REELS:
        return ".webm"
    return ".mp4" if sys.platform == "darwin" else ".webm"


async def get_reel_video_encoder() -> str:
    """Return the platform-native reel video encoder."""
    if not ENABLE_PLATFORM_NATIVE_REELS:
        return await get_av1_encoder()
    if sys.platform == "darwin":
        return "libx265"
    return await get_av1_encoder()


def get_reel_video_options(encoder: str) -> list[str]:
    """Return ffmpeg video encoder options for the chosen reel encoder."""
    if encoder == "libx265":
        return ["-crf", "28", "-preset", "medium", "-tag:v", "hvc1"]
    if encoder == "av1_nvenc":
        return ["-cq", "35", "-preset", "p4"]
    return ["-crf", "38", "-preset", "6"]


def get_reel_audio_options() -> list[str]:
    """Return ffmpeg audio and container options for the current platform."""
    if not ENABLE_PLATFORM_NATIVE_REELS:
        return ["-c:a", "libopus", "-ac", "2", "-b:a", "128k"]
    if sys.platform == "darwin":
        return ["-c:a", "aac", "-ac", "2", "-b:a", "128k", "-movflags", "+faststart"]
    return ["-c:a", "libopus", "-ac", "2", "-b:a", "128k"]


def get_expected_showreel_paths(
    media_folder: Path,
    timestamps: list[int] = SHOWREEL_TIMESTAMPS,
    media_root: Optional[Path] = None,
) -> list[str]:
    """
    Compute the expected showreel paths without generating them.

    Args:
        media_folder: Folder for this specific media item
        timestamps: List of timestamps (determines number of reels)
        media_root: Root path for computing relative paths (optional)

    Returns:
        List of relative paths where showreels will be created for this platform
    """
    paths = []
    extension = get_reel_extension()
    for reel_num in range(1, len(timestamps) + 1):
        output_path = media_folder / f"reel{reel_num}{extension}"
        if media_root:
            try:
                paths.append(str(output_path.relative_to(media_root)))
            except ValueError:
                paths.append(str(output_path))
        else:
            paths.append(str(output_path))
    return paths


def get_expected_episode_reel_path(
    media_folder: Path,
    season_num: int,
    episode_num: int,
    media_root: Optional[Path] = None,
) -> str:
    """
    Compute the expected episode reel path without generating it.

    Args:
        media_folder: Folder for this series
        season_num: Season number
        episode_num: Episode number
        media_root: Root path for computing relative paths (optional)

    Returns:
        Relative path where the reel will be created for this platform
    """
    output_path = (
        media_folder / f"S{season_num:02d}E{episode_num:02d}{get_reel_extension()}"
    )
    if media_root:
        try:
            return str(output_path.relative_to(media_root))
        except ValueError:
            return str(output_path)
    return str(output_path)


def get_existing_showreel_paths(
    media_folder: Path,
    timestamps: list[int] = SHOWREEL_TIMESTAMPS,
    media_root: Optional[Path] = None,
) -> list[str]:
    """Return preferred existing showreel paths, one per reel slot, in AV1-first order."""
    source_sets = get_existing_showreel_source_sets(
        media_folder,
        timestamps=timestamps,
        media_root=media_root,
    )
    return [sources[0] for sources in source_sets if sources]


def get_existing_showreel_source_sets(
    media_folder: Path,
    timestamps: list[int] = SHOWREEL_TIMESTAMPS,
    media_root: Optional[Path] = None,
) -> list[list[str]]:
    """Return all existing showreel source files for each reel slot in AV1-first order."""
    source_sets: list[list[str]] = []
    for reel_num in range(1, len(timestamps) + 1):
        sources = [
            _to_media_path(media_folder / f"reel{reel_num}{extension}", media_root)
            for extension in get_reel_source_extensions()
            if (media_folder / f"reel{reel_num}{extension}").exists()
        ]
        if sources:
            source_sets.append(sources)
    return source_sets


def get_existing_episode_reel_path(
    media_folder: Path,
    season_num: int,
    episode_num: int,
    media_root: Optional[Path] = None,
) -> str | None:
    """Return the preferred existing episode reel path in AV1-first order."""
    sources = get_existing_episode_reel_sources(
        media_folder,
        season_num,
        episode_num,
        media_root=media_root,
    )
    return sources[0] if sources else None


def get_existing_episode_reel_sources(
    media_folder: Path,
    season_num: int,
    episode_num: int,
    media_root: Optional[Path] = None,
) -> list[str]:
    """Return all existing episode reel source files in AV1-first order."""
    ep_code = f"S{season_num:02d}E{episode_num:02d}"
    return [
        _to_media_path(media_folder / f"{ep_code}{extension}", media_root)
        for extension in get_reel_source_extensions()
        if (media_folder / f"{ep_code}{extension}").exists()
    ]


async def movie_showreels_exist(
    media_folder: Path, timestamps: list[int] = SHOWREEL_TIMESTAMPS
) -> bool:
    """Check if all platform-native showreel files for a movie already exist."""
    extension = get_reel_extension()
    for reel_num in range(1, len(timestamps) + 1):
        if not await AsyncPath(media_folder / f"reel{reel_num}{extension}").exists():
            return False
    return True


async def episode_reel_exists(
    media_folder: Path, season_num: int, episode_num: int
) -> bool:
    """Check if a platform-native episode reel file already exists."""
    return await AsyncPath(
        media_folder / f"S{season_num:02d}E{episode_num:02d}{get_reel_extension()}"
    ).exists()


def get_bluray_uri(video_path: str) -> Optional[str]:
    """
    Convert a Blu-ray index.bdmv path to an ffmpeg-compatible bluray: URI.

    Args:
        video_path: Path that may be a Blu-ray index.bdmv file

    Returns:
        bluray: URI if this is a Blu-ray disc, None otherwise
    """
    if not video_path.endswith(".bdmv"):
        return None

    path = Path(video_path)
    # index.bdmv is in BDMV folder, so parent's parent is the disc root
    # e.g., /path/to/disc/BDMV/index.bdmv -> /path/to/disc
    if path.parent.name == "BDMV":
        disc_root = path.parent.parent
        return f"bluray:{disc_root}"

    return None


# Cache for AV1 encoder availability
_av1_encoder_cache: Optional[str] = None


async def get_av1_encoder() -> str:
    """
    Detect the best available AV1 encoder.

    Prefers hardware encoders (NVIDIA av1_nvenc) over software (libsvtav1).
    Falls back to libsvtav1 if no hardware encoder is available.

    Returns:
        Encoder name to use with ffmpeg -c:v
    """
    global _av1_encoder_cache
    if _av1_encoder_cache is not None:
        return _av1_encoder_cache

    # Check for NVIDIA AV1 encoder
    try:
        proc = await _subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if b"av1_nvenc" in stdout:
            # Verify it actually works (driver support)
            test_proc = await _subprocess_exec(
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "nullsrc=s=64x64:d=1",
                "-c:v",
                "av1_nvenc",
                "-f",
                "null",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(test_proc.communicate(), timeout=10)
            if test_proc.returncode == 0:
                _av1_encoder_cache = "av1_nvenc"
                return _av1_encoder_cache
    except Exception:
        pass

    # Default to libsvtav1
    _av1_encoder_cache = "libsvtav1"
    return _av1_encoder_cache


def get_encoder_options(encoder: str) -> list[str]:
    """
    Get encoder-specific options for the given AV1 encoder.

    Args:
        encoder: The encoder name (av1_nvenc, libsvtav1)

    Returns:
        List of ffmpeg arguments for encoder settings
    """
    if encoder == "av1_nvenc":
        # NVIDIA hardware encoder - use constant quality mode
        return ["-cq", "35", "-preset", "p4"]
    else:
        # libsvtav1 software encoder
        return ["-crf", "38", "-preset", "6"]


@dataclass
class MediaProbeInfo:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    is_hdr: bool = False
    dovi_profile: int | None = None
    has_dolby_vision: bool = False
    has_dolby_atmos: bool = False
    resolution: str | None = None
    audio_languages: list[str] | None = None
    subtitle_languages: list[str] | None = None


_media_probe_cache: dict[str, MediaProbeInfo] = {}
_duration_re = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_dimension_re = re.compile(r"(\d{2,5})x(\d{2,5})")
_dovi_profile_re = re.compile(r"DOVI configuration record:.*?profile:\s*(\d+)", re.I)
_audio_stream_re = re.compile(r"Stream #\d+:\d+(?:\(([^)]+)\))?:\s+Audio:")
_subtitle_stream_re = re.compile(r"Stream #\d+:\d+(?:\(([^)]+)\))?:\s+Subtitle:")


def _lang_code(raw: str | None) -> str | None:
    if not raw:
        return None
    code = raw.strip().split(",", 1)[0].lower()
    return code if code and code != "und" else None


async def probe_media_info(video_path: str) -> MediaProbeInfo:
    """Parse key media metadata from `ffmpeg -i` output."""
    cached = _media_probe_cache.get(video_path)
    if cached is not None:
        return cached

    info = MediaProbeInfo()
    try:
        cmd = ["ffmpeg", "-hide_banner", "-i", video_path]
        logger.debug("    $ %s", shlex.join(cmd))
        proc = await _subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = (stderr + stdout).decode("utf-8", errors="replace")
        lower_text = text.lower()

        duration_match = _duration_re.search(text)
        if duration_match:
            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            seconds = float(duration_match.group(3))
            info.duration = hours * 3600 + minutes * 60 + seconds

        video_line = None
        for line in text.splitlines():
            if "Stream #" in line and "Video:" in line:
                video_line = line
                break
        if video_line:
            dim_match = _dimension_re.search(video_line)
            if dim_match:
                info.width = int(dim_match.group(1))
                info.height = int(dim_match.group(2))
                info.resolution = classify_resolution_from_dimensions(
                    info.width, info.height
                )

        info.is_hdr = (
            "smpte2084" in lower_text
            or "arib-std-b67" in lower_text
            or "bt2020" in lower_text
        )

        dovi_match = _dovi_profile_re.search(text)
        if dovi_match:
            info.dovi_profile = int(dovi_match.group(1))
        elif "dvhe" in lower_text or "dvh1" in lower_text or "dav1" in lower_text:
            info.dovi_profile = 7
        info.has_dolby_vision = info.dovi_profile is not None

        audio_languages: list[str] = []
        for line in text.splitlines():
            if "Stream #" not in line or "Audio:" not in line:
                continue
            match = _audio_stream_re.search(line)
            if not match:
                continue
            lang = _lang_code(match.group(1))
            if lang and lang not in audio_languages:
                audio_languages.append(lang)
            if "atmos" in line.lower():
                info.has_dolby_atmos = True
        info.audio_languages = audio_languages or None

        subtitle_languages: list[str] = []
        for match in _subtitle_stream_re.finditer(text):
            lang = _lang_code(match.group(1))
            if lang and lang not in subtitle_languages:
                subtitle_languages.append(lang)
        info.subtitle_languages = subtitle_languages or None
    except Exception as e:
        logger.warning("    ffmpeg probe error: %s", e)

    _media_probe_cache[video_path] = info
    return info


async def detect_dovi_profile(video_path: str) -> Optional[int]:
    """
    Detect Dolby Vision profile from a video file.

    Returns the DoVi profile number (5, 7, 8, etc.) or None if not DoVi.
    Profile 5: Dual-layer, no HDR10 base (needs conversion)
    Profile 7: Dual-layer with HDR10 base, but may have EL issues
    Profile 8: Single-layer HDR10 compatible (usually OK)
    """
    try:
        return (await probe_media_info(video_path)).dovi_profile
    except Exception as e:
        logger.warning("    DoVi detection error: %s", e)
        return None


def get_dovi_to_hdr10_filter() -> str:
    """
    Get the video filter string for converting DoVi to HDR10.

    Uses libplacebo to strip DoVi metadata while preserving HDR10 colorspace.
    No tonemapping is applied - this just converts the container format.
    """
    # libplacebo converts DoVi to clean HDR10 without tonemapping
    # Preserves bt2020 primaries and SMPTE ST 2084 (PQ) transfer
    return (
        "libplacebo=colorspace=bt2020nc:color_primaries=bt2020:"
        "color_trc=smpte2084:range=tv"
    )


async def is_hdr_video(video_path: str) -> bool:
    """
    Check if a video file is HDR using ffmpeg probe output.

    Returns True if the video has HDR metadata (bt2020, SMPTE ST 2084, etc.)
    """
    try:
        return (await probe_media_info(video_path)).is_hdr
    except Exception:
        return False


async def detect_crop(video_path: str) -> Optional[str]:
    """
    Detect black bars in a video and return the crop filter string.

    Only runs on 16:9 (1.78:1) source videos, since other aspect ratios like
    2.35:1 or 4:3 are already correctly framed. Trusts cropping results only
    when symmetric (same top/bottom OR same left/right). Final coordinates
    are aligned to 8 pixels.

    Uses ffmpeg to analyze just 2 seconds of video at the 5-minute mark for speed.

    Args:
        video_path: Path to the video file (or bluray: URI)

    Returns:
        Crop filter string like "crop=1920:800:0:140" if black bars detected,
        or None if no cropping needed or detection failed.
    """
    try:
        # First, get source video dimensions to check if it's 16:9
        probe_info = await probe_media_info(video_path)
        if not probe_info.width or not probe_info.height:
            return None
        src_width, src_height = probe_info.width, probe_info.height

        # Check if source is 16:9 (allow small tolerance for weird resolutions)
        # 16:9 = 1.777..., typical: 1920x1080, 3840x2160, 1280x720
        aspect_ratio = src_width / src_height
        if not (1.7 <= aspect_ratio <= 1.85):
            # Not 16:9, skip crop detection (already correctly framed)
            return None

        # Use ffmpeg to run cropdetect on just 2 seconds at 5-minute mark
        # This is much faster than scanning a long window with lavfi analysis.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            "300",
            "-i",
            video_path,
            "-t",
            "2",
            "-vf",
            "cropdetect=limit=24:round=2:reset=0",
            "-f",
            "null",
            "-",
        ]
        logger.debug("    $ %s", shlex.join(cmd))
        proc = await _subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        # cropdetect outputs to stderr like: [Parsed_cropdetect_0 @ ...] x1:0 x2:1919 y1:138 y2:941 w:1920 h:800 ...
        # We need to parse the crop values from stderr
        crop_pattern = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
        crop_values = []
        for line in stderr_text.split("\n"):
            match = crop_pattern.search(line)
            if match:
                w, h, x, y = (
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                )
                if w > 0 and h > 0 and x >= 0 and y >= 0:
                    crop_values.append((w, h, x, y))

        if not crop_values:
            return None

        # Use the most common crop values (mode) for stability
        most_common = Counter(crop_values).most_common(1)
        if not most_common:
            return None

        w, h, x, y = most_common[0][0]

        # Only crop if there's meaningful black bar removal (at least 8 pixels offset)
        if x < 8 and y < 8:
            return None

        # Validate symmetry: trust only if cropping is symmetric in one direction
        # (same top/bottom for letterbox, OR same left/right for pillarbox)
        # Allow up to 4 pixels of rounding error
        left_crop = x
        right_crop = src_width - (x + w)
        top_crop = y
        bottom_crop = src_height - (y + h)

        horizontal_symmetric = abs(left_crop - right_crop) <= 4
        vertical_symmetric = abs(top_crop - bottom_crop) <= 4

        # Must be symmetric in at least one direction, but not require both
        # (letterbox = vertical symmetric, pillarbox = horizontal symmetric)
        if not (horizontal_symmetric or vertical_symmetric):
            return None

        # If cropping in both directions, both must be symmetric
        if x >= 8 and y >= 8:
            if not (horizontal_symmetric and vertical_symmetric):
                return None

        # Align all coordinates to 8 pixels (shrink content area if needed)
        # x and y: round UP to next multiple of 8
        x_aligned = ((x + 7) // 8) * 8
        y_aligned = ((y + 7) // 8) * 8
        # w and h: round DOWN to multiple of 8, accounting for adjusted x/y
        w_aligned = ((w - (x_aligned - x)) // 8) * 8
        h_aligned = ((h - (y_aligned - y)) // 8) * 8

        # Ensure we still have valid dimensions
        if w_aligned <= 0 or h_aligned <= 0:
            return None

        crop_result = f"crop={w_aligned}:{h_aligned}:{x_aligned}:{y_aligned}"
        logger.debug("    Detected crop: %s", crop_result)
        return crop_result

    except Exception as e:
        logger.warning("    Crop detection error: %s", e)
        return None


async def get_video_duration(video_path: str) -> Optional[float]:
    """
    Get the duration of a video file in seconds using ffmpeg probe output.
    """
    try:
        return (await probe_media_info(video_path)).duration
    except Exception:
        return None


async def generate_showreel_images(
    video_path: str,
    media_folder: Path,
    timestamps: list[int] = SHOWREEL_TIMESTAMPS,
    title: str = None,
    on_progress=None,
) -> list[str]:
    """
    Generate showreel video clips from a video file at specified timestamps.

    Saves 10-second clips in a platform-native format, downscaled to max 720px width,
    preserving original color metadata. macOS emits MP4/H.265; other platforms emit WebM/AV1.

    Args:
        video_path: Path to the video file (or index.bdmv for Blu-ray discs)
        media_folder: Folder for this specific media item
        timestamps: List of timestamps in seconds to capture
        title: Title for logging
        on_progress: Optional callback(reel_num) called after each reel completes

    Returns:
        List of relative paths to generated showreel video clips
    """
    if not video_path:
        logger.warning(
            "    Showreel: no video path provided for %s", title or "unknown"
        )
        return []

    # Handle Blu-ray disc structures using bluray: protocol
    bluray_uri = get_bluray_uri(video_path)
    if bluray_uri:
        ffmpeg_input = bluray_uri
    else:
        if not await AsyncPath(video_path).exists():
            logger.warning("    Showreel: video file does not exist: %s", video_path)
            return []
        ffmpeg_input = video_path

    # Fast path: check if all showreel clips already exist before any probe calls
    existing_paths = []
    all_exist = True
    extension = get_reel_extension()
    for reel_num in range(1, len(timestamps) + 1):
        output_filename = f"reel{reel_num}{extension}"
        output_path = media_folder / output_filename
        if await AsyncPath(output_path).exists():
            existing_paths.append(str(output_path))
        else:
            all_exist = False
            break

    if all_exist and existing_paths:
        return existing_paths

    await AsyncPath(media_folder).mkdir(parents=True, exist_ok=True)

    # Check video duration to avoid seeking past the end
    duration = await get_video_duration(ffmpeg_input)
    if duration is None:
        logger.warning("    Could not get duration for: %s", video_path)
        return []

    # Filter timestamps that are within the video duration (with 40s margin for 10s clips)
    valid_timestamps = [t for t in timestamps if t < (duration - 40)]
    if not valid_timestamps:
        # If video is too short, try to get at least one clip from middle
        if duration > 60:
            valid_timestamps = [int(duration / 2) - 5]  # Center the 10s clip
        else:
            logger.warning(
                "    Showreel: video too short (%.0fs) for %s: %s",
                duration,
                title or "unknown",
                video_path,
            )
            return []

    encoder = await get_reel_video_encoder()
    encoder_opts = get_reel_video_options(encoder)
    audio_opts = get_reel_audio_options()

    # Detect Dolby Vision profile for tonemapping (profiles 5/7 need conversion)
    dovi_profile = await detect_dovi_profile(ffmpeg_input)
    needs_tonemap = dovi_profile is not None and dovi_profile in (5, 7)
    if needs_tonemap:
        logger.info("    DoVi profile %d detected, will convert to HDR10", dovi_profile)

    # Detect black bars once for all clips (uses same video source)
    crop_filter = await detect_crop(ffmpeg_input)

    generated_paths = []

    for reel_num, timestamp in enumerate(valid_timestamps, 1):
        output_filename = f"reel{reel_num}{extension}"
        output_path = media_folder / output_filename

        # Skip if already exists
        if await AsyncPath(output_path).exists():
            generated_paths.append(str(output_path))
            if on_progress:
                on_progress(reel_num)
            continue

        # Build video filter chain:
        # 1. DoVi to HDR10 conversion (if needed) - must come first
        # 2. Crop black bars (if detected)
        # 3. Scale to max 720px width
        vf_parts = []
        if needs_tonemap:
            vf_parts.append(get_dovi_to_hdr10_filter())
        if crop_filter:
            vf_parts.append(crop_filter)
        vf_parts.append("scale='min(720,iw)':-2")
        vf_filter = ",".join(vf_parts)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            ffmpeg_input,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",  # First video, first audio (optional)
            "-t",
            "10",
            "-vf",
            vf_filter,
            "-c:v",
            encoder,
            *encoder_opts,
            *audio_opts,
            str(output_path),
        ]

        logger.debug("    $ %s", shlex.join(cmd))
        try:
            proc = await _subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode == 0 and await AsyncPath(output_path).exists():
                generated_paths.append(str(output_path))
                logger.info(
                    "    Showreel reel%d generated for %s",
                    reel_num,
                    title or "unknown",
                )
                if on_progress:
                    on_progress(reel_num)
            else:
                stderr_text = stderr.decode(errors="replace").strip() if stderr else ""
                logger.error(
                    "    Showreel reel%d failed (rc=%s) for %s: %s",
                    reel_num,
                    proc.returncode,
                    title or "unknown",
                    stderr_text[:500] if stderr_text else "(no output)",
                )
                await AsyncPath(output_path).unlink(missing_ok=True)
                # Abort remaining reels - if first one fails, others likely will too
                break
        except BaseException as e:
            await AsyncPath(output_path).unlink(missing_ok=True)
            if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            logger.error(
                "Error generating showreel for %s at %ds: %s",
                title or "unknown",
                timestamp,
                e,
            )
            # Abort remaining reels
            break

    if generated_paths:
        logger.info(
            "    Showreel complete for %s: %d/%d reels",
            title or "unknown",
            len(generated_paths),
            len(valid_timestamps),
        )
    else:
        logger.warning("    Showreel: no reels generated for %s", title or "unknown")

    return generated_paths


async def generate_episode_reel(
    video_path: str,
    media_folder: Path,
    season_num: int,
    episode_num: int,
) -> Optional[str]:
    """
    Generate a single 10-second reel video clip for a TV episode.

    Saves a platform-native clip such as S01E05.mp4 on macOS or S01E05.webm elsewhere.
    The clip is downscaled to max 720px width while preserving original color metadata.

    Args:
        video_path: Path to the episode video file (or index.bdmv for Blu-ray discs)
        media_folder: Folder for this series
        season_num: Season number
        episode_num: Episode number

    Returns:
        Relative path to generated image, or None if failed
    """
    ep_code = f"S{season_num:02d}E{episode_num:02d}"
    if not video_path:
        logger.warning("    Episode reel: no video path for %s", ep_code)
        return None

    # Handle Blu-ray disc structures using bluray: protocol
    bluray_uri = get_bluray_uri(video_path)
    if bluray_uri:
        ffmpeg_input = bluray_uri
    else:
        if not await AsyncPath(video_path).exists():
            logger.warning(
                "    Episode reel: file not found for %s: %s", ep_code, video_path
            )
            return None
        ffmpeg_input = video_path

    await AsyncPath(media_folder).mkdir(parents=True, exist_ok=True)

    extension = get_reel_extension()
    output_filename = f"{ep_code}{extension}"
    output_path = media_folder / output_filename

    # Skip if already exists
    if await AsyncPath(output_path).exists():
        return str(output_path)

    # Check video duration
    duration = await get_video_duration(ffmpeg_input)
    if duration is None:
        logger.warning(
            "    Episode reel: could not get duration for %s: %s", ep_code, video_path
        )
        return None

    # Use 40% of total length for the clip start
    actual_timestamp = int(duration * 0.4)
    # Ensure we're at least 10 seconds in and have room for 10s clip
    actual_timestamp = max(10, min(actual_timestamp, duration - 40))

    encoder = await get_reel_video_encoder()
    encoder_opts = get_reel_video_options(encoder)
    audio_opts = get_reel_audio_options()

    # Detect Dolby Vision profile for tonemapping (profiles 5/7 need conversion)
    dovi_profile = await detect_dovi_profile(ffmpeg_input)
    needs_tonemap = dovi_profile is not None and dovi_profile in (5, 7)
    if needs_tonemap:
        logger.info("    DoVi profile %d detected, will convert to HDR10", dovi_profile)

    # Detect black bars for cropping
    crop_filter = await detect_crop(ffmpeg_input)

    # Build video filter chain:
    # 1. DoVi to HDR10 conversion (if needed) - must come first
    # 2. Crop black bars (if detected)
    # 3. Scale to max 720px width
    vf_parts = []
    if needs_tonemap:
        vf_parts.append(get_dovi_to_hdr10_filter())
    if crop_filter:
        vf_parts.append(crop_filter)
    vf_parts.append("scale='min(720,iw)':-2")
    vf_filter = ",".join(vf_parts)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(actual_timestamp),
        "-i",
        ffmpeg_input,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",  # First video, first audio (optional)
        "-t",
        "10",
        "-vf",
        vf_filter,
        "-c:v",
        encoder,
        *encoder_opts,
        *audio_opts,
        str(output_path),
    ]

    logger.debug("    $ %s", shlex.join(cmd))
    try:
        proc = await _subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0 and await AsyncPath(output_path).exists():
            logger.info("    Episode reel generated: %s", ep_code)
            return str(output_path)
        else:
            stderr_text = stderr.decode(errors="replace").strip() if stderr else ""
            logger.error(
                "    Episode reel %s failed (rc=%s): %s",
                ep_code,
                proc.returncode,
                stderr_text[:500] if stderr_text else "(no output)",
            )
            await AsyncPath(output_path).unlink(missing_ok=True)
            return None
    except BaseException as e:
        await AsyncPath(output_path).unlink(missing_ok=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        logger.error("Error generating episode reel for %s: %s", ep_code, e)
        return None
