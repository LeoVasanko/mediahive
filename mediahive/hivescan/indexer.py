"""Media index generation — async generators for continuous scanning."""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple

from mediahive.hivescan.showreel import (
    get_expected_episode_reel_path,
    get_expected_showreel_paths,
)
from mediahive.models.data import (
    Episode,
    Movie,
    Season,
    Series,
    Torrent,
)
from mediahive.hivescan.tmdb_client import (
    fetch_movie_info,
    fetch_series_info,
    fetch_season_details,
)

from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.scanning import find_cover_image, find_episode_files, find_playable_file
from mediahive.hivescan.images import (
    download_cover_image,
    download_backdrop_image,
    download_season_poster,
)
from mediahive.hivescan.utils import (
    get_added_timestamp,
    get_directory_size,
    get_media_folder_path,
    make_relative_path,
    sort_by_quality,
    RESOLUTION_PRIORITY,
)

logger = logging.getLogger("hivescan.indexer")


async def _build_torrent_info(
    item: ParsedContent, media_root: Optional[str] = None
) -> Torrent:
    """Build torrent info for a single torrent."""
    playable_file = await find_playable_file(item.path)
    if item.content_hash and item.content_hash.size == 0:
        item.content_hash.size = await get_directory_size(item.content_hash.path)
    size = item.content_hash.size if item.content_hash else None
    added_at = await get_added_timestamp(item.path)

    return Torrent(
        title=item.title,
        playable_file=make_relative_path(playable_file, media_root),
        resolution=item.resolution,
        quality=item.quality,
        codec=item.codec,
        audio=item.audio,
        encoder=item.encoder,
        size=size,
        added_at=added_at,
    )


async def _collect_episode_files(
    items: List[ParsedContent],
) -> Dict[Tuple[int, int], List[Dict]]:
    """
    Collect all episode files from a list of torrent items.

    Returns dict mapping (season, episode) to list of file info dicts.
    """
    all_episode_files: Dict[Tuple[int, int], List[Dict]] = {}

    for item in items:
        episode_files = await find_episode_files(item.path)

        for (season_num, episode_num), files in episode_files.items():
            key = (season_num, episode_num)
            if key not in all_episode_files:
                all_episode_files[key] = []
            for file_path, file_size in files:
                all_episode_files[key].append(
                    {
                        "path": file_path,
                        "size": file_size,
                        "resolution": item.resolution,
                        "quality": item.quality,
                        "codec": item.codec,
                        "audio": item.audio,
                        "encoder": item.encoder,
                        "torrent_path": str(item.path),
                        "torrent_title": item.title,
                    }
                )

        # Handle individual episodes from PTN parsing
        if item.episode is not None and item.season is not None:
            season_nums = (
                item.season if isinstance(item.season, list) else [item.season]
            )
            episode_nums = (
                item.episode if isinstance(item.episode, list) else [item.episode]
            )

            playable = await find_playable_file(item.path)
            if playable:
                for sn in season_nums:
                    for ep in episode_nums:
                        key = (sn, ep)
                        if key not in all_episode_files:
                            all_episode_files[key] = []
                        already_added = any(
                            f["path"] == playable
                            for f in all_episode_files.get(key, [])
                        )
                        if not already_added:
                            if item.content_hash and item.content_hash.size == 0:
                                item.content_hash.size = await get_directory_size(item.content_hash.path)
                            size = item.content_hash.size if item.content_hash else 0
                            all_episode_files[key].append(
                                {
                                    "path": playable,
                                    "size": size,
                                    "resolution": item.resolution,
                                    "quality": item.quality,
                                    "codec": item.codec,
                                    "audio": item.audio,
                                    "encoder": item.encoder,
                                    "torrent_path": str(item.path),
                                    "torrent_title": item.title,
                                }
                            )

    return all_episode_files


def _build_episodes_data(
    episodes_in_season: Dict[int, List[Dict]],
    tmdb_episodes: Dict[int, EpisodeInfo],
    series_folder: Path,
    season_num: int,
    generate_showreels: bool,
    episode_reel_tasks: List,
    series_title: str,
    media_root: Optional[str] = None,
) -> List[Episode]:
    """Build episode data list for a season."""
    episodes_data = []

    for episode_num in sorted(episodes_in_season.keys()):
        episode_files = episodes_in_season[episode_num]
        sort_by_quality(episode_files)

        tmdb_ep = tmdb_episodes.get(episode_num)

        reel_path = None
        if generate_showreels and episode_files:
            best_file = episode_files[0]["path"]
            if best_file and not best_file.endswith(".bdmv"):
                reel_path = get_expected_episode_reel_path(
                    series_folder,
                    season_num,
                    episode_num,
                    media_root=Path(media_root) if media_root else None,
                )
                episode_reel_tasks.append(
                    (best_file, series_folder, season_num, episode_num, series_title)
                )

        torrents = {}
        for f in episode_files:
            relpath = make_relative_path(f["torrent_path"], media_root)
            torrents[relpath] = Torrent(
                title=f["torrent_title"],
                playable_file=make_relative_path(f["path"], media_root),
                resolution=f.get("resolution"),
                quality=f.get("quality"),
                codec=f.get("codec"),
                audio=f.get("audio"),
                encoder=f.get("encoder"),
                size=f.get("size"),
            )

        episode_data = Episode(
            episode_number=episode_num,
            name=tmdb_ep.name if tmdb_ep else None,
            overview=tmdb_ep.overview if tmdb_ep else None,
            air_date=tmdb_ep.air_date if tmdb_ep else None,
            runtime=tmdb_ep.runtime if tmdb_ep else None,
            still_path=tmdb_ep.still_path if tmdb_ep else None,
            rating=tmdb_ep.vote_average if tmdb_ep else None,
            director=tmdb_ep.director if tmdb_ep else None,
            reel_image=reel_path,
            torrents=torrents,
        )
        episodes_data.append(episode_data)

    return episodes_data


async def _build_seasons_data(
    all_episode_files: Dict[Tuple[int, int], List[Dict]],
    tmdb_id: Optional[int],
    series_folder: Path,
    display_title: str,
    fetch_covers: bool,
    generate_showreels: bool,
    season_cache: Dict,
    episode_reel_tasks: List,
    media_root: Optional[str] = None,
) -> List[Season]:
    """Build seasons data structure for a series."""
    # Group episodes by season
    seasons_map: Dict[int, Dict[int, List[Dict]]] = {}
    for (season_num, episode_num), files in all_episode_files.items():
        if season_num not in seasons_map:
            seasons_map[season_num] = {}
        seasons_map[season_num][episode_num] = files

    seasons_data = []
    for season_num in sorted(seasons_map.keys()):
        episodes_in_season = seasons_map[season_num]

        # Fetch TMDb season details if we have a TMDb ID
        tmdb_season = None
        tmdb_episodes: Dict[int, EpisodeInfo] = {}

        if tmdb_id:
            cache_key = (tmdb_id, season_num)
            if cache_key not in season_cache:
                logger.debug(
                    "    Fetching season %d details for %s", season_num, display_title
                )
                season_cache[cache_key] = await fetch_season_details(
                    tmdb_id, season_num
                )
            tmdb_season = season_cache[cache_key]

            if tmdb_season and tmdb_season.episodes:
                for ep in tmdb_season.episodes:
                    tmdb_episodes[ep.episode_number] = ep

        # Download season poster
        season_poster_path = None
        if fetch_covers and tmdb_season and tmdb_season.poster_path:
            season_poster_path = await download_season_poster(
                tmdb_season.poster_path, series_folder, season_num
            )

        episodes_data = _build_episodes_data(
            episodes_in_season,
            tmdb_episodes,
            series_folder,
            season_num,
            generate_showreels,
            episode_reel_tasks,
            display_title,
            media_root,
        )

        season_data = Season(
            season_number=season_num,
            name=tmdb_season.name if tmdb_season else None,
            overview=tmdb_season.overview if tmdb_season else None,
            air_date=tmdb_season.air_date if tmdb_season else None,
            poster_path=make_relative_path(season_poster_path, media_root)
            if season_poster_path
            else None,
            episode_count=len(episodes_data),
            episodes=episodes_data,
        )
        seasons_data.append(season_data)

    return seasons_data


async def _process_movies(
    categories: dict,
    cover_dir: Path,
    fetch_covers: bool,
    generate_showreels: bool,
    media_root: Optional[str] = None,
) -> AsyncIterator[Tuple[Movie, Optional[Tuple[str, Path, str]]]]:
    """
    Async generator that processes all movies.

    Yields (Movie, showreel_task_or_None) for each movie as it is processed.
    """
    # In-memory cache for TMDb lookups
    movie_tmdb_cache: Dict[str, Optional[TMDbInfo]] = {}

    async def get_movie_tmdb(title: str, year: Optional[int]) -> Optional[TMDbInfo]:
        cache_key = f"{title.lower()}:{year}"
        if cache_key in movie_tmdb_cache:
            return movie_tmdb_cache[cache_key]
        tmdb_info = await fetch_movie_info(title, year)
        movie_tmdb_cache[cache_key] = tmdb_info
        return tmdb_info

    async def has_playable(item: ParsedContent) -> bool:
        return await find_playable_file(item.path) is not None

    # Filter movies with playable files
    valid_movies = []
    for item in categories[ContentType.MOVIE]:
        if await has_playable(item):
            valid_movies.append(item)
    skipped = len(categories[ContentType.MOVIE]) - len(valid_movies)
    if skipped > 0:
        logger.debug("  Skipped %d movie torrents with no playable video files", skipped)

    # Group by title+year
    movie_groups: Dict[str, List[ParsedContent]] = {}
    for item in valid_movies:
        key = f"{item.title.lower()}:{item.year or 0}"
        if key not in movie_groups:
            movie_groups[key] = []
        movie_groups[key].append(item)

    # Re-group by TMDb ID
    tmdb_movie_groups: Dict[int, Dict] = {}
    no_tmdb_movie_groups: Dict[str, Dict] = {}

    if movie_groups:
        logger.info(
            "  Processing %d unique movies (%d total versions)...",
            len(movie_groups),
            len(categories[ContentType.MOVIE]),
        ) if movie_groups else None

    for idx, (movie_key, items) in enumerate(movie_groups.items(), 1):
        first_item = items[0]
        logger.debug(
            "    [%d/%d] %s (%s)",
            idx,
            len(movie_groups),
            first_item.title,
            first_item.year,
        )

        tmdb_info = await get_movie_tmdb(first_item.title, first_item.year)
        # Yield to event loop so HTTP requests stay responsive
        if idx % 20 == 0:
            await asyncio.sleep(0)

        if tmdb_info and tmdb_info.tmdb_id:
            if tmdb_info.tmdb_id not in tmdb_movie_groups:
                tmdb_movie_groups[tmdb_info.tmdb_id] = {
                    "tmdb_info": tmdb_info,
                    "items": [],
                    "torrent_titles": set(),
                    "year": first_item.year,
                }
            tmdb_movie_groups[tmdb_info.tmdb_id]["items"].extend(items)
            tmdb_movie_groups[tmdb_info.tmdb_id]["torrent_titles"].add(first_item.title)
        else:
            key = f"{first_item.title.lower()}:{first_item.year or 0}"
            if key not in no_tmdb_movie_groups:
                no_tmdb_movie_groups[key] = {
                    "items": [],
                    "title": first_item.title,
                    "year": first_item.year,
                }
            no_tmdb_movie_groups[key]["items"].extend(items)

    # Process movies with TMDb info — yield each as ready
    for tmdb_id, group_data in tmdb_movie_groups.items():
        tmdb_info = group_data["tmdb_info"]
        items = group_data["items"]
        torrent_titles = group_data["torrent_titles"]
        year = group_data["year"]

        display_title = tmdb_info.title
        item_id = hashlib.md5(f"movie:{tmdb_id}".encode()).hexdigest()[:12]

        # Find/download cover
        cover_path = None
        if fetch_covers:
            cover_path = await find_cover_image(display_title, year, "movie", cover_dir)
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = await find_cover_image(tt, year, "movie", cover_dir)
                    if cover_path:
                        break
            if not cover_path and tmdb_info.poster_path:
                cover_path = await download_cover_image(
                    tmdb_info.poster_path, display_title, year, "movie", cover_dir
                )

        torrents = {}
        for item in items:
            relpath = make_relative_path(str(item.path), media_root)
            torrent = await _build_torrent_info(item, media_root)
            torrents[relpath] = torrent

        sort_by_quality(list(torrents.values()))

        # Queue showreel generation
        showreel_paths = []
        showreel_task = None
        if generate_showreels and versions:
            # Find the best version for showreel (highest quality)
            best_relpath = max(versions.keys(), key=lambda k: (
                RESOLUTION_PRIORITY.get(versions[k].resolution or "", 0),
                versions[k].size or 0,
                k
            ))
            best_version = torrents[best_relpath]
            if best_version.playable_file:
                abs_playable = (
                    str(Path(media_root) / best_version.playable_file)
                    if media_root
                    else best_version.playable_file
                )
                media_folder = get_media_folder_path(
                    display_title, year, "movie", cover_dir
                )
                showreel_paths = get_expected_showreel_paths(
                    media_folder, media_root=Path(media_root) if media_root else None
                )
                showreel_task = (abs_playable, media_folder, display_title)

        # Download backdrop
        backdrop_path = None
        if fetch_covers and tmdb_info.backdrop_path:
            backdrop_path = await download_backdrop_image(
                tmdb_info.backdrop_path, display_title, year, "movie", cover_dir
            )

        version_timestamps = [v.added_at for v in torrents.values() if v.added_at]
        newest = max(version_timestamps) if version_timestamps else None

        movie = Movie(
            id=item_id,
            title=display_title,
            info=tmdb_info,
            year=year,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            backdrop_path=make_relative_path(backdrop_path, media_root),
            showreel_images=showreel_paths if showreel_paths else None,
            torrents=torrents,
        )
        yield movie, showreel_task

    # Process movies without TMDb info
    for key, group_data in no_tmdb_movie_groups.items():
        items = group_data["items"]
        title = group_data["title"]
        year = group_data["year"]
        item_id = hashlib.md5(f"movie:{title}:{year}".encode()).hexdigest()[:12]

        cover_path = (
            await find_cover_image(title, year, "movie", cover_dir) if fetch_covers else None
        )

        torrents = {}
        for item in items:
            relpath = make_relative_path(str(item.path), media_root)
            torrent = await _build_torrent_info(item, media_root)
            torrents[relpath] = torrent

        sort_by_quality(list(torrents.values()))

        showreel_paths = []
        showreel_task = None
        if generate_showreels and torrents:
            # Find the best version for showreel (highest quality)
            best_relpath = max(torrents.keys(), key=lambda k: (
                RESOLUTION_PRIORITY.get(torrents[k].resolution or "", 0),
                torrents[k].size or 0,
                k
            ))
            best_version = torrents[best_relpath]
            if best_version.playable_file and not best_version.playable_file.endswith(".bdmv"):
                abs_playable = (
                    str(Path(media_root) / best_version.playable_file)
                    if media_root
                    else best_version.playable_file
                )
                media_folder = get_media_folder_path(
                    title, year, "movie", cover_dir
                )
                showreel_paths = get_expected_showreel_paths(
                    media_folder,
                    media_root=Path(media_root) if media_root else None,
                )
                showreel_task = (abs_playable, media_folder, title)

        version_timestamps = [v.added_at for v in torrents.values() if v.added_at]
        newest = max(version_timestamps) if version_timestamps else None

        movie = Movie(
            id=item_id,
            title=title,
            year=year,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            showreel_images=showreel_paths if showreel_paths else None,
            torrents=torrents,
        )
        yield movie, showreel_task


async def _process_series(
    categories: dict,
    cover_dir: Path,
    fetch_covers: bool,
    generate_showreels: bool,
    media_root: Optional[str] = None,
) -> AsyncIterator[Tuple[Series, List[Tuple[str, Path, int, int, str]]]]:
    """
    Async generator that processes all series.

    Yields (Series, episode_reel_tasks) for each series as it is processed.
    """
    # In-memory cache for TMDb lookups
    series_tmdb_cache: Dict[str, Optional[TMDbInfo]] = {}
    season_cache: Dict[Tuple[int, int], Optional[TMDbSeasonInfo]] = {}

    async def get_series_tmdb(title: str) -> Optional[TMDbInfo]:
        cache_key = title.lower()
        if cache_key in series_tmdb_cache:
            return series_tmdb_cache[cache_key]
        tmdb_info = await fetch_series_info(title)
        series_tmdb_cache[cache_key] = tmdb_info
        return tmdb_info

    async def has_video_content(item: ParsedContent) -> bool:
        if await find_playable_file(item.path):
            return True
        return len(await find_episode_files(item.path)) > 0

    # Filter series with video content
    valid_series = []
    for item in categories[ContentType.SERIES]:
        if await has_video_content(item):
            valid_series.append(item)
    skipped = len(categories[ContentType.SERIES]) - len(valid_series)
    if skipped > 0:
        logger.info(
            "  Skipped %d series torrents with no playable video files", skipped
        )

    # Group by title
    series_groups: Dict[str, List[ParsedContent]] = {}
    for item in valid_series:
        key = item.title.lower()
        if key not in series_groups:
            series_groups[key] = []
        series_groups[key].append(item)

    # Re-group by TMDb ID
    tmdb_groups: Dict[int, Dict] = {}
    no_tmdb_groups: Dict[str, Dict] = {}

    if series_groups:
        logger.info(
            "  Processing %d unique series (%d total entries)...",
            len(series_groups),
            len(categories[ContentType.SERIES]),
        )

    for idx, (series_key, items) in enumerate(series_groups.items(), 1):
        first_item = items[0]
        logger.debug("    [%d/%d] %s", idx, len(series_groups), first_item.title)

        tmdb_info = await get_series_tmdb(first_item.title)
        # Yield to event loop so HTTP requests stay responsive
        if idx % 20 == 0:
            await asyncio.sleep(0)

        if tmdb_info and tmdb_info.tmdb_id:
            if tmdb_info.tmdb_id not in tmdb_groups:
                tmdb_groups[tmdb_info.tmdb_id] = {
                    "tmdb_info": tmdb_info,
                    "items": [],
                    "torrent_titles": set(),
                }
            tmdb_groups[tmdb_info.tmdb_id]["items"].extend(items)
            tmdb_groups[tmdb_info.tmdb_id]["torrent_titles"].add(first_item.title)
        else:
            key = first_item.title.lower()
            if key not in no_tmdb_groups:
                no_tmdb_groups[key] = {"items": [], "title": first_item.title}
            no_tmdb_groups[key]["items"].extend(items)

    # Process series with TMDb info — yield each as ready
    for series_idx, (tmdb_id, group_data) in enumerate(tmdb_groups.items(), 1):
        tmdb_info = group_data["tmdb_info"]
        items = group_data["items"]
        torrent_titles = group_data["torrent_titles"]

        display_title = tmdb_info.title
        series_id = hashlib.md5(f"series:{tmdb_id}".encode()).hexdigest()[:12]

        logger.debug("  [%d/%d] %s", series_idx, len(tmdb_groups), display_title)

        series_folder = get_media_folder_path(display_title, None, "series", cover_dir)

        # Find/download cover
        cover_path = None
        if fetch_covers:
            cover_path = await find_cover_image(display_title, None, "series", cover_dir)
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = await find_cover_image(tt, None, "series", cover_dir)
                    if cover_path:
                        break
            if not cover_path and tmdb_info.poster_path:
                cover_path = await download_cover_image(
                    tmdb_info.poster_path, display_title, None, "series", cover_dir
                )

        # Download backdrop
        backdrop_path = None
        if fetch_covers and tmdb_info.backdrop_path:
            backdrop_path = await download_backdrop_image(
                tmdb_info.backdrop_path, display_title, None, "series", cover_dir
            )

        # Collect and build episode data
        all_episode_files = await _collect_episode_files(items)
        ep_reel_tasks: List[Tuple[str, Path, int, int, str]] = []
        seasons_data = await _build_seasons_data(
            all_episode_files,
            tmdb_id,
            series_folder,
            display_title,
            fetch_covers,
            generate_showreels,
            season_cache,
            ep_reel_tasks,
            media_root,
        )

        if not seasons_data:
            logger.info("    Skipping %s - no episodes found", display_title)
            continue

        different_titles = sorted(
            t for t in torrent_titles if t.lower() != display_title.lower()
        )
        item_timestamps = [await get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series = Series(
            id=series_id,
            title=display_title,
            info=tmdb_info,
            alternative_titles=different_titles if different_titles else None,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            backdrop_path=make_relative_path(backdrop_path, media_root),
            seasons=seasons_data,
        )
        yield series, ep_reel_tasks

    # Process series without TMDb info
    for key, group_data in no_tmdb_groups.items():
        items = group_data["items"]
        title = group_data["title"]
        series_id = hashlib.md5(f"series:{title}".encode()).hexdigest()[:12]

        cover_path = (
            await find_cover_image(title, None, "series", cover_dir) if fetch_covers else None
        )
        series_folder = get_media_folder_path(title, None, "series", cover_dir)

        all_episode_files = await _collect_episode_files(items)
        ep_reel_tasks: List[Tuple[str, Path, int, int, str]] = []
        seasons_data = await _build_seasons_data(
            all_episode_files,
            None,
            series_folder,
            title,
            fetch_covers,
            generate_showreels,
            season_cache,
            ep_reel_tasks,
            media_root,
        )

        if not seasons_data:
            logger.info("    Skipping %s - no episodes found", title)
            continue

        item_timestamps = [await get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series = Series(
            id=series_id,
            title=title,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            seasons=seasons_data,
        )
        yield series, ep_reel_tasks
