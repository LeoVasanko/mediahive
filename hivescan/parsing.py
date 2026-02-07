"""Torrent name parsing functions."""

import re
from pathlib import Path
from typing import Optional, Tuple

import PTN

from hivescan.models import ContentHash, ContentType, ParsedContent


def determine_content_type(parsed: dict) -> ContentType:
    """Determine content type based on parsed torrent name info."""
    has_season = "season" in parsed and parsed["season"] is not None
    has_episode = "episode" in parsed and parsed["episode"] is not None
    has_year = "year" in parsed and parsed["year"] is not None

    if has_season or has_episode:
        return ContentType.SERIES
    if has_year:
        return ContentType.MOVIE
    return ContentType.OTHER


def parse_download(path: Path) -> ParsedContent:
    """Parse a downloaded torrent directory/file name."""
    name = path.name
    parsed = PTN.parse(name)
    content_type = determine_content_type(parsed)
    content_hash = ContentHash.from_path(path)

    return ParsedContent(
        path=path,
        name=name,
        content_type=content_type,
        title=parsed.get("title", name),
        year=parsed.get("year"),
        resolution=parsed.get("resolution"),
        quality=parsed.get("quality"),
        codec=parsed.get("codec"),
        audio=parsed.get("audio"),
        season=parsed.get("season"),
        episode=parsed.get("episode"),
        episode_name=parsed.get("episodeName"),
        encoder=parsed.get("encoder"),
        language=parsed.get("language"),
        is_directory=path.is_dir(),
        raw_parsed=parsed,
        content_hash=content_hash,
    )


def parse_episode_from_filename(filename: str) -> Optional[Tuple[int, int]]:
    """
    Parse season and episode numbers from a filename.

    Handles formats: S01E05, 1x05, Season 1 Episode 5

    Returns:
        Tuple of (season_number, episode_number) or None if not found
    """
    name = filename.lower()

    # S01E05 format
    match = re.search(r"s(\d{1,2})e(\d{1,3})", name)
    if match:
        return int(match.group(1)), int(match.group(2))

    # 1x05 format
    match = re.search(r"(\d{1,2})x(\d{1,3})", name)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Season 1 Episode 5 format
    match = re.search(r"season\s*(\d{1,2}).*episode\s*(\d{1,3})", name)
    if match:
        return int(match.group(1)), int(match.group(2))

    return None
