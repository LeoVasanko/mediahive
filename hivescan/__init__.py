"""
Hivescan - Scans downloaded torrent directories and generates a media index.

Usage:
    hivescan [path] [options]

Or as a library:
    from hivescan import scan_downloads, generate_media_index
"""

from hivescan.models import ContentType, ContentHash, ParsedContent
from hivescan.scanning import scan_downloads, categorize_downloads, find_playable_file, find_episode_files
from hivescan.indexer import generate_media_index
from hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root
from hivescan.showreel import generate_showreel_images, generate_episode_reel
from hivescan.tmdb_client import (
    TMDbInfo,
    TMDbSeasonInfo,
    TMDbEpisodeInfo,
    fetch_movie_info,
    fetch_series_info,
    fetch_season_details,
    set_cache_dir,
)

__all__ = [
    # Models
    "ContentType",
    "ContentHash",
    "ParsedContent",
    # Scanning
    "scan_downloads",
    "categorize_downloads",
    "find_playable_file",
    "find_episode_files",
    # Index generation
    "generate_media_index",
    # Showreel generation
    "generate_showreel_images",
    "generate_episode_reel",
    # TMDb client
    "TMDbInfo",
    "TMDbSeasonInfo",
    "TMDbEpisodeInfo",
    "fetch_movie_info",
    "fetch_series_info",
    "fetch_season_details",
    "set_cache_dir",
    # Utilities
    "DEFAULT_OUTPUT_FOLDER",
    "find_common_root",
]
