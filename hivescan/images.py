"""TMDb image downloading functions."""

import urllib.request
from pathlib import Path
from typing import Optional

from hivescan.utils import get_media_folder_path


# TMDb image configuration
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
DEFAULT_POSTER_SIZE = "w500"
DEFAULT_BACKDROP_SIZE = "w1280"


def _download_image(url: str, output_path: Path, description: str) -> Optional[str]:
    """Download an image from URL to output path."""
    if output_path.exists():
        return str(output_path)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TorrentManager/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(response.read())
        return str(output_path)
    except Exception as e:
        print(f"    Failed to download {description}: {e}")
        return None


def download_cover_image(
    poster_path: str,
    title: str,
    year: Optional[int],
    media_type: str,
    cover_dir: Path,
    size: str = DEFAULT_POSTER_SIZE,
) -> Optional[str]:
    """Download a cover image from TMDb."""
    if not poster_path:
        return None

    media_folder = get_media_folder_path(title, year, media_type, cover_dir)
    cover_path = media_folder / "cover.jpg"

    if cover_path.exists():
        return str(cover_path)

    url = f"{TMDB_IMAGE_BASE}/{size}{poster_path}"
    print(f"    Downloading cover: {title}")
    return _download_image(url, cover_path, f"cover for {title}")


def download_backdrop_image(
    backdrop_path: str,
    title: str,
    year: Optional[int],
    media_type: str,
    cover_dir: Path,
    size: str = DEFAULT_BACKDROP_SIZE,
) -> Optional[str]:
    """Download a backdrop image from TMDb."""
    if not backdrop_path:
        return None

    media_folder = get_media_folder_path(title, year, media_type, cover_dir)
    local_path = media_folder / "backdrop.jpg"

    if local_path.exists():
        return str(local_path)

    url = f"{TMDB_IMAGE_BASE}/{size}{backdrop_path}"
    print(f"    Downloading backdrop: {title}")
    return _download_image(url, local_path, f"backdrop for {title}")


def download_season_poster(
    poster_path: str,
    media_folder: Path,
    season_num: int,
) -> Optional[str]:
    """Download a season poster image from TMDb."""
    if not poster_path:
        return None

    output_path = media_folder / f"season{season_num:02d}.jpg"

    if output_path.exists():
        return str(output_path)

    media_folder.mkdir(parents=True, exist_ok=True)
    url = f"{TMDB_IMAGE_BASE}/{DEFAULT_POSTER_SIZE}{poster_path}"
    return _download_image(url, output_path, f"season {season_num} poster")
