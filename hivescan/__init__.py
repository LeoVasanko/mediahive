"""
Hivescan - Continuous media scanning server with live WebSocket updates.

Usage:
    hivescan /path/to/torrents/*        # Start scanning server
    hivescan /path/* --port 9000        # Custom port

Or as a library:
    from hivescan.index_store import IndexStore
    from hivescan.structs import Movie, Series, TaskInfo
    from hivescan.server import app
"""

from hivescan.models import ContentType, ContentHash, ParsedContent
from hivescan.scanning import (
    scan_downloads,
    categorize_downloads,
    find_playable_file,
    find_episode_files,
)
from hivescan.index_store import IndexStore
from hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root
from hivescan.showreel import generate_showreel_images, generate_episode_reel
from hivescan.structs import (
    CastMember,
    Episode,
    EpisodeRelease,
    IndexSnapshot,
    MediaStats,
    Movie,
    MovieVersion,
    Season,
    Series,
    SimilarMedia,
    TaskInfo,
    TMDbEpisodeInfo,
    TMDbInfo,
    TMDbSeasonInfo,
)
from hivescan.tmdb_client import (
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
    # Index store
    "IndexStore",
    # Struct types
    "CastMember",
    "Episode",
    "EpisodeRelease",
    "IndexSnapshot",
    "MediaStats",
    "Movie",
    "MovieVersion",
    "Season",
    "Series",
    "SimilarMedia",
    "TaskInfo",
    "TMDbEpisodeInfo",
    "TMDbInfo",
    "TMDbSeasonInfo",
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
