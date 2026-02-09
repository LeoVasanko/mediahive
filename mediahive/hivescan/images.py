"""TMDb image downloading functions."""

import httpx
from pathlib import Path
from typing import Optional

from aiopathlib import AsyncPath

from mediahive.hivescan.utils import get_media_folder_path


# TMDb image configuration
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
DEFAULT_POSTER_SIZE = "w500"
DEFAULT_BACKDROP_SIZE = "w1280"

# Shared async HTTP client (created lazily)
_image_client: Optional[httpx.AsyncClient] = None


def _get_image_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client for image downloads."""
    global _image_client
    if _image_client is None:
        _image_client = httpx.AsyncClient(
            headers={"User-Agent": "TorrentManager/1.0"},
            timeout=30.0,
            follow_redirects=True,
        )
    return _image_client


async def _download_image(
    url: str, output_path: Path, description: str
) -> Optional[str]:
    """Download an image from URL to output path."""
    ap = AsyncPath(output_path)
    if await ap.exists():
        return str(output_path)

    try:
        client = _get_image_client()
        response = await client.get(url)
        response.raise_for_status()
        await AsyncPath(output_path.parent).mkdir(parents=True, exist_ok=True)
        await ap.write_bytes(response.content)
        return str(output_path)
    except Exception as e:
        print(f"    Failed to download {description}: {e}")
        return None


async def download_cover_image(
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

    if await AsyncPath(cover_path).exists():
        return str(cover_path)

    url = f"{TMDB_IMAGE_BASE}/{size}{poster_path}"
    print(f"    Downloading cover: {title}")
    return await _download_image(url, cover_path, f"cover for {title}")


async def download_backdrop_image(
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

    if await AsyncPath(local_path).exists():
        return str(local_path)

    url = f"{TMDB_IMAGE_BASE}/{size}{backdrop_path}"
    print(f"    Downloading backdrop: {title}")
    return await _download_image(url, local_path, f"backdrop for {title}")


async def download_season_poster(
    poster_path: str,
    media_folder: Path,
    season_num: int,
) -> Optional[str]:
    """Download a season poster image from TMDb."""
    if not poster_path:
        return None

    output_path = media_folder / f"season{season_num:02d}.jpg"

    if await AsyncPath(output_path).exists():
        return str(output_path)

    await AsyncPath(media_folder).mkdir(parents=True, exist_ok=True)
    url = f"{TMDB_IMAGE_BASE}/{DEFAULT_POSTER_SIZE}{poster_path}"
    return await _download_image(url, output_path, f"season {season_num} poster")

