"""
Hivescan - Continuous media scanning with live WebSocket updates.

Usage as a module:
    python -m mediahive.hivescan /path/to/torrents/*
    python -m mediahive.hivescan /path/* --port 9000

Or as a library:
    from mediahive.hivescan.scanning import scan_downloads, categorize_downloads
    from mediahive.hivescan.indexer import _process_movies, _process_series
"""

from mediahive.hivescan.models import ContentType, ContentHash, ParsedContent
from mediahive.hivescan.scanning import (
    scan_downloads,
    categorize_downloads,
    find_playable_file,
    find_episode_files,
)
from mediahive.hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root
from mediahive.hivescan.showreel import generate_showreel_images, generate_episode_reel
from mediahive.hivescan import scanner
from mediahive.models.data import (
    Episode,
    IndexSnapshot,
    MediaStats,
    Movie,
    Season,
    Series,
    TaskInfo,
    Torrent,
)
from mediahive.hivescan.tmdb_client import (
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
    # Struct types
    "CastMember",
    "Episode",
    "IndexSnapshot",
    "MediaStats",
    "Movie",
    "Season",
    "Series",
    "SimilarMedia",
    "TaskInfo",
    "Torrent",
    "EpisodeInfo",
    "Info",
    "SeasonInfo",
    # Showreel generation
    "generate_showreel_images",
    "generate_episode_reel",
    # TMDb client
    "fetch_movie_info",
    "fetch_series_info",
    "fetch_season_details",
    "set_cache_dir",
    # Utilities
    "DEFAULT_OUTPUT_FOLDER",
    "find_common_root",
]
