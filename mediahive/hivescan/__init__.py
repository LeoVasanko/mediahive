"""Hivescan - Continuous media scanning with live WebSocket updates.

Import from submodules directly:
    from mediahive.hivescan.scanner import start, stop
    from mediahive.hivescan.scanning import scan_downloads, categorize_downloads
    from mediahive.hivescan.models import ContentType, ParsedContent
    from mediahive.hivescan.tmdb_client import fetch_movie_info, fetch_series_info
"""

# Minimal public API - prefer importing from submodules directly
from mediahive.hivescan.models import ContentHash, ContentType, ParsedContent
from mediahive.hivescan.utils import DEFAULT_OUTPUT_FOLDER, find_common_root

__all__ = [
    "DEFAULT_OUTPUT_FOLDER",
    "ContentHash",
    "ContentType",
    "ParsedContent",
    "find_common_root",
]
