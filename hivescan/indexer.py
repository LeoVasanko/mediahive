"""Media index generation."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from hivescan.showreel import (
    episode_reel_exists,
    generate_episode_reel,
    generate_showreel_images,
    get_expected_episode_reel_path,
    get_expected_showreel_paths,
    movie_showreels_exist,
)
from hivescan.tmdb_client import (
    TMDbInfo,
    TMDbSeasonInfo,
    TMDbEpisodeInfo,
    fetch_movie_info,
    fetch_series_info,
    fetch_season_details,
)

from hivescan.models import ContentType, ParsedContent
from hivescan.scanning import find_cover_image, find_episode_files, find_playable_file
from hivescan.images import download_cover_image, download_backdrop_image, download_season_poster
from hivescan.utils import (
    get_added_timestamp,
    get_media_folder_path,
    make_relative_path,
    sort_by_quality,
)


def _build_version_info(item: ParsedContent, media_root: Optional[str] = None) -> dict:
    """Build version/release info dict for a single torrent."""
    playable_file = find_playable_file(item.path)
    size = item.content_hash.size if item.content_hash else None
    newest = get_added_timestamp(item.path)

    return {
        "path": make_relative_path(str(item.path), media_root),
        "playable_file": make_relative_path(playable_file, media_root),
        "resolution": item.resolution,
        "quality": item.quality,
        "codec": item.codec,
        "audio": item.audio,
        "encoder": item.encoder,
        "size": size,
        "newest": newest,
    }


def _collect_episode_files(items: List[ParsedContent]) -> Dict[Tuple[int, int], List[Dict]]:
    """
    Collect all episode files from a list of torrent items.

    Returns dict mapping (season, episode) to list of file info dicts.
    """
    all_episode_files: Dict[Tuple[int, int], List[Dict]] = {}

    for item in items:
        episode_files = find_episode_files(item.path)

        for (season_num, episode_num), files in episode_files.items():
            key = (season_num, episode_num)
            if key not in all_episode_files:
                all_episode_files[key] = []
            for file_path, file_size in files:
                all_episode_files[key].append({
                    "path": file_path,
                    "size": file_size,
                    "resolution": item.resolution,
                    "quality": item.quality,
                    "codec": item.codec,
                    "audio": item.audio,
                    "encoder": item.encoder,
                    "torrent_path": str(item.path),
                })

        # Handle individual episodes from PTN parsing
        if item.episode is not None and item.season is not None:
            season_nums = item.season if isinstance(item.season, list) else [item.season]
            episode_nums = item.episode if isinstance(item.episode, list) else [item.episode]

            playable = find_playable_file(item.path)
            if playable:
                for sn in season_nums:
                    for ep in episode_nums:
                        key = (sn, ep)
                        if key not in all_episode_files:
                            all_episode_files[key] = []
                        already_added = any(f["path"] == playable for f in all_episode_files.get(key, []))
                        if not already_added:
                            size = item.content_hash.size if item.content_hash else 0
                            all_episode_files[key].append({
                                "path": playable,
                                "size": size,
                                "resolution": item.resolution,
                                "quality": item.quality,
                                "codec": item.codec,
                                "audio": item.audio,
                                "encoder": item.encoder,
                                "torrent_path": str(item.path),
                            })

    return all_episode_files


def _build_episodes_data(
    episodes_in_season: Dict[int, List[Dict]],
    tmdb_episodes: Dict[int, TMDbEpisodeInfo],
    series_folder: Path,
    season_num: int,
    generate_showreels: bool,
    episode_reel_tasks: List,
    series_title: str,
    media_root: Optional[str] = None,
) -> List[dict]:
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
                reel_path = get_expected_episode_reel_path(series_folder, season_num, episode_num, media_root=Path(media_root) if media_root else None)
                episode_reel_tasks.append((best_file, series_folder, season_num, episode_num, series_title))

        releases = []
        for f in episode_files:
            releases.append({
                "path": make_relative_path(f["torrent_path"], media_root),
                "playable_file": make_relative_path(f["path"], media_root),
                "resolution": f.get("resolution"),
                "quality": f.get("quality"),
                "codec": f.get("codec"),
                "audio": f.get("audio"),
                "encoder": f.get("encoder"),
                "size": f.get("size"),
            })

        episode_data = {
            "episode_number": episode_num,
            "name": tmdb_ep.name if tmdb_ep else None,
            "overview": tmdb_ep.overview if tmdb_ep else None,
            "air_date": tmdb_ep.air_date if tmdb_ep else None,
            "runtime": tmdb_ep.runtime if tmdb_ep else None,
            "still_path": tmdb_ep.still_path if tmdb_ep else None,
            "rating": tmdb_ep.vote_average if tmdb_ep else None,
            "director": tmdb_ep.director if tmdb_ep else None,
            "reel_image": reel_path,
            "releases": releases,
        }
        episodes_data.append(episode_data)

    return episodes_data


def _build_seasons_data(
    all_episode_files: Dict[Tuple[int, int], List[Dict]],
    tmdb_id: Optional[int],
    series_folder: Path,
    display_title: str,
    fetch_covers: bool,
    generate_showreels: bool,
    season_cache: Dict,
    episode_reel_tasks: List,
    media_root: Optional[str] = None,
) -> List[dict]:
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
        tmdb_episodes: Dict[int, TMDbEpisodeInfo] = {}

        if tmdb_id:
            cache_key = (tmdb_id, season_num)
            if cache_key not in season_cache:
                print(f"    Fetching season {season_num} details for {display_title}")
                season_cache[cache_key] = fetch_season_details(tmdb_id, season_num)
            tmdb_season = season_cache[cache_key]

            if tmdb_season and tmdb_season.episodes:
                for ep in tmdb_season.episodes:
                    tmdb_episodes[ep.episode_number] = ep

        # Download season poster
        season_poster_path = None
        if fetch_covers and tmdb_season and tmdb_season.poster_path:
            season_poster_path = download_season_poster(tmdb_season.poster_path, series_folder, season_num)

        episodes_data = _build_episodes_data(
            episodes_in_season, tmdb_episodes, series_folder, season_num,
            generate_showreels, episode_reel_tasks, display_title, media_root
        )

        season_data = {
            "season_number": season_num,
            "name": tmdb_season.name if tmdb_season else None,
            "overview": tmdb_season.overview if tmdb_season else None,
            "air_date": tmdb_season.air_date if tmdb_season else None,
            "poster_path": make_relative_path(season_poster_path, media_root) if season_poster_path else None,
            "episode_count": len(episodes_data),
            "episodes": episodes_data,
        }
        seasons_data.append(season_data)

    return seasons_data


def _process_movies(
    categories: dict,
    cover_dir: Path,
    fetch_covers: bool,
    generate_showreels: bool,
    media_root: Optional[str] = None,
) -> Tuple[List[dict], List[Tuple[str, Path, str]]]:
    """Process all movies and return (movies_list, showreel_tasks)."""
    # In-memory cache for TMDb lookups
    movie_tmdb_cache: Dict[str, Optional[TMDbInfo]] = {}

    def get_movie_tmdb(title: str, year: Optional[int]) -> Optional[TMDbInfo]:
        cache_key = f"{title.lower()}:{year}"
        if cache_key in movie_tmdb_cache:
            return movie_tmdb_cache[cache_key]
        tmdb_info = fetch_movie_info(title, year)
        movie_tmdb_cache[cache_key] = tmdb_info
        return tmdb_info

    def has_playable(item: ParsedContent) -> bool:
        return find_playable_file(item.path) is not None

    # Filter movies with playable files
    valid_movies = [item for item in categories[ContentType.MOVIE] if has_playable(item)]
    skipped = len(categories[ContentType.MOVIE]) - len(valid_movies)
    if skipped > 0:
        print(f"  Skipped {skipped} movie torrents with no playable video files")

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

    print(f"  Processing {len(movie_groups)} unique movies ({len(categories[ContentType.MOVIE])} total versions)...")

    for idx, (movie_key, items) in enumerate(movie_groups.items(), 1):
        first_item = items[0]
        print(f"    [{idx}/{len(movie_groups)}] {first_item.title} ({first_item.year})\x1b[K", end="\r")

        tmdb_info = get_movie_tmdb(first_item.title, first_item.year)

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
                no_tmdb_movie_groups[key] = {"items": [], "title": first_item.title, "year": first_item.year}
            no_tmdb_movie_groups[key]["items"].extend(items)

    print()

    movies = []
    movie_showreel_tasks: List[Tuple[str, Path, str]] = []

    # Process movies with TMDb info
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
            cover_path = find_cover_image(display_title, year, "movie", cover_dir)
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = find_cover_image(tt, year, "movie", cover_dir)
                    if cover_path:
                        break
            if not cover_path and tmdb_info.poster_path:
                cover_path = download_cover_image(tmdb_info.poster_path, display_title, year, "movie", cover_dir)

        versions = [_build_version_info(item, media_root) for item in items]
        sort_by_quality(versions)

        # Queue showreel generation
        showreel_paths = []
        if generate_showreels and versions:
            best_playable = versions[0].get("playable_file")
            if best_playable:
                # Reconstruct absolute path from relative path
                abs_playable = str(Path(media_root) / best_playable) if media_root else best_playable
                media_folder = get_media_folder_path(display_title, year, "movie", cover_dir)
                showreel_paths = get_expected_showreel_paths(media_folder, media_root=Path(media_root) if media_root else None)
                movie_showreel_tasks.append((abs_playable, media_folder, display_title))

        # Download backdrop
        backdrop_path = None
        if fetch_covers and tmdb_info.backdrop_path:
            backdrop_path = download_backdrop_image(tmdb_info.backdrop_path, display_title, year, "movie", cover_dir)

        different_titles = [t for t in torrent_titles if t.lower() != display_title.lower()]
        version_timestamps = [v["newest"] for v in versions if v.get("newest")]
        newest = max(version_timestamps) if version_timestamps else None

        movies.append({
            "id": item_id,
            "title": display_title,
            "original_title": tmdb_info.original_title,
            "alternative_titles": tmdb_info.alternative_titles,
            "torrent_titles": different_titles if different_titles else None,
            "year": year,
            "newest": newest,
            "cover_path": make_relative_path(cover_path, media_root),
            "backdrop_path": make_relative_path(backdrop_path, media_root),
            "showreel_images": showreel_paths if showreel_paths else None,
            "versions": versions,
            "tmdb_id": tmdb_info.tmdb_id,
            "tmdb_title": tmdb_info.title,
            "rating": tmdb_info.rating,
            "vote_count": tmdb_info.vote_count,
            "overview": tmdb_info.overview,
            "genres": tmdb_info.genres,
            "release_date": tmdb_info.release_date,
            "runtime": tmdb_info.runtime,
            "status": tmdb_info.status,
            "tagline": tmdb_info.tagline,
            "poster_path": tmdb_info.poster_path,
            "similar": tmdb_info.similar,
            "keywords": tmdb_info.keywords,
            "cast": tmdb_info.cast,
            "director": tmdb_info.director,
        })

    # Process movies without TMDb info
    for key, group_data in no_tmdb_movie_groups.items():
        items = group_data["items"]
        title = group_data["title"]
        year = group_data["year"]
        item_id = hashlib.md5(f"movie:{title}:{year}".encode()).hexdigest()[:12]

        cover_path = find_cover_image(title, year, "movie", cover_dir) if fetch_covers else None

        versions = [_build_version_info(item, media_root) for item in items]
        sort_by_quality(versions)

        showreel_paths = []
        if generate_showreels and versions:
            best_playable = versions[0].get("playable_file")
            if best_playable:
                # Reconstruct absolute path from relative path
                abs_playable = str(Path(media_root) / best_playable) if media_root else best_playable
                if not abs_playable.endswith(".bdmv"):
                    media_folder = get_media_folder_path(title, year, "movie", cover_dir)
                    showreel_paths = get_expected_showreel_paths(media_folder, media_root=Path(media_root) if media_root else None)
                    movie_showreel_tasks.append((abs_playable, media_folder, title))

        version_timestamps = [v["newest"] for v in versions if v.get("newest")]
        newest = max(version_timestamps) if version_timestamps else None

        movies.append({
            "id": item_id,
            "title": title,
            "original_title": None,
            "torrent_titles": None,
            "year": year,
            "newest": newest,
            "cover_path": make_relative_path(cover_path, media_root),
            "showreel_images": showreel_paths if showreel_paths else None,
            "versions": versions,
        })

    return movies, movie_showreel_tasks


def _process_series(
    categories: dict,
    cover_dir: Path,
    fetch_covers: bool,
    generate_showreels: bool,
    media_root: Optional[str] = None,
) -> Tuple[List[dict], List[Tuple[str, Path, int, int, str]]]:
    """Process all series and return (series_list, episode_reel_tasks)."""
    # In-memory cache for TMDb lookups
    series_tmdb_cache: Dict[str, Optional[TMDbInfo]] = {}
    season_cache: Dict[Tuple[int, int], Optional[TMDbSeasonInfo]] = {}

    def get_series_tmdb(title: str) -> Optional[TMDbInfo]:
        cache_key = title.lower()
        if cache_key in series_tmdb_cache:
            return series_tmdb_cache[cache_key]
        tmdb_info = fetch_series_info(title)
        series_tmdb_cache[cache_key] = tmdb_info
        return tmdb_info

    def has_video_content(item: ParsedContent) -> bool:
        if find_playable_file(item.path):
            return True
        return len(find_episode_files(item.path)) > 0

    # Filter series with video content
    valid_series = [item for item in categories[ContentType.SERIES] if has_video_content(item)]
    skipped = len(categories[ContentType.SERIES]) - len(valid_series)
    if skipped > 0:
        print(f"  Skipped {skipped} series torrents with no playable video files")

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

    print(f"  Processing {len(series_groups)} unique series ({len(categories[ContentType.SERIES])} total entries)...")

    for idx, (series_key, items) in enumerate(series_groups.items(), 1):
        first_item = items[0]
        print(f"    [{idx}/{len(series_groups)}] {first_item.title}\x1b[K", end="\r")

        tmdb_info = get_series_tmdb(first_item.title)

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

    print()

    series = []
    episode_reel_tasks: List[Tuple[str, Path, int, int, str]] = []

    # Process series with TMDb info
    for series_idx, (tmdb_id, group_data) in enumerate(tmdb_groups.items(), 1):
        tmdb_info = group_data["tmdb_info"]
        items = group_data["items"]
        torrent_titles = group_data["torrent_titles"]

        display_title = tmdb_info.title
        series_id = hashlib.md5(f"series:{tmdb_id}".encode()).hexdigest()[:12]

        print(f"  [{series_idx}/{len(tmdb_groups)}] {display_title}\x1b[K")

        series_folder = get_media_folder_path(display_title, None, "series", cover_dir)

        # Find/download cover
        cover_path = None
        if fetch_covers:
            cover_path = find_cover_image(display_title, None, "series", cover_dir)
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = find_cover_image(tt, None, "series", cover_dir)
                    if cover_path:
                        break
            if not cover_path and tmdb_info.poster_path:
                cover_path = download_cover_image(tmdb_info.poster_path, display_title, None, "series", cover_dir)

        # Download backdrop
        backdrop_path = None
        if fetch_covers and tmdb_info.backdrop_path:
            backdrop_path = download_backdrop_image(tmdb_info.backdrop_path, display_title, None, "series", cover_dir)

        # Collect and build episode data
        all_episode_files = _collect_episode_files(items)
        seasons_data = _build_seasons_data(
            all_episode_files, tmdb_id, series_folder, display_title,
            fetch_covers, generate_showreels, season_cache, episode_reel_tasks, media_root
        )

        if not seasons_data:
            print(f"    Skipping {display_title} - no episodes found")
            continue

        different_titles = [t for t in torrent_titles if t.lower() != display_title.lower()]
        item_timestamps = [get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series.append({
            "id": series_id,
            "title": display_title,
            "original_title": tmdb_info.original_title,
            "torrent_titles": different_titles if different_titles else None,
            "newest": newest,
            "cover_path": make_relative_path(cover_path, media_root),
            "backdrop_path": make_relative_path(backdrop_path, media_root),
            "seasons": seasons_data,
            "tmdb_id": tmdb_info.tmdb_id,
            "tmdb_title": tmdb_info.title,
            "rating": tmdb_info.rating,
            "vote_count": tmdb_info.vote_count,
            "overview": tmdb_info.overview,
            "genres": tmdb_info.genres,
            "release_date": tmdb_info.release_date,
            "status": tmdb_info.status,
            "tagline": tmdb_info.tagline,
            "poster_path": tmdb_info.poster_path,
            "similar": tmdb_info.similar,
            "keywords": tmdb_info.keywords,
            "cast": tmdb_info.cast,
            "creators": tmdb_info.creators,
            "number_of_seasons": tmdb_info.number_of_seasons,
            "number_of_episodes": tmdb_info.number_of_episodes,
            "networks": tmdb_info.networks,
        })

    # Process series without TMDb info
    for key, group_data in no_tmdb_groups.items():
        items = group_data["items"]
        title = group_data["title"]
        series_id = hashlib.md5(f"series:{title}".encode()).hexdigest()[:12]

        cover_path = find_cover_image(title, None, "series", cover_dir) if fetch_covers else None
        series_folder = get_media_folder_path(title, None, "series", cover_dir)

        all_episode_files = _collect_episode_files(items)
        seasons_data = _build_seasons_data(
            all_episode_files, None, series_folder, title,
            fetch_covers, generate_showreels, season_cache, episode_reel_tasks, media_root
        )

        if not seasons_data:
            print(f"    Skipping {title} - no episodes found")
            continue

        item_timestamps = [get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series.append({
            "id": series_id,
            "title": title,
            "original_title": None,
            "torrent_titles": None,
            "newest": newest,
            "cover_path": make_relative_path(cover_path, media_root),
            "seasons": seasons_data,
        })

    return series, episode_reel_tasks


def _run_showreel_generation(
    movie_tasks: List[Tuple[str, Path, str]],
    episode_tasks: List[Tuple[str, Path, int, int, str]],
) -> None:
    """Run showreel generation for movies and episodes."""
    pending_movie_tasks = [
        (vp, mf, t) for vp, mf, t in movie_tasks
        if not movie_showreels_exist(mf)
    ]
    pending_episode_tasks = [
        (vp, mf, s, e, t) for vp, mf, s, e, t in episode_tasks
        if not episode_reel_exists(mf, s, e)
    ]

    total_units = len(pending_movie_tasks) * 5 + len(pending_episode_tasks)
    skipped_movies = len(movie_tasks) - len(pending_movie_tasks)
    skipped_episodes = len(episode_tasks) - len(pending_episode_tasks)

    if total_units > 0:
        print(f"\nGenerating showreels: {len(pending_movie_tasks)} movies, {len(pending_episode_tasks)} episodes")
        if skipped_movies > 0 or skipped_episodes > 0:
            print(f"  (skipping {skipped_movies} movies, {skipped_episodes} episodes already done)")

        with tqdm(total=total_units, unit="clip", dynamic_ncols=True) as pbar:
            for video_path, media_folder, title in pending_movie_tasks:
                pbar.set_description(f"{title[:40]}")
                generate_showreel_images(video_path, media_folder, title=title, pbar=pbar)

            for video_path, media_folder, season_num, episode_num, series_title in pending_episode_tasks:
                episode_code = f"S{season_num:02d}E{episode_num:02d}"
                pbar.set_description(f"{series_title[:30]} {episode_code}")
                generate_episode_reel(video_path, media_folder, season_num, episode_num, pbar=pbar)

        print("Showreel generation complete.")
    elif movie_tasks or episode_tasks:
        print(f"\nAll showreels already exist ({skipped_movies} movies, {skipped_episodes} episodes).")


def generate_media_index(
    categories: dict[ContentType, list[ParsedContent]],
    output_path: Path,
    cover_dir: Path,
    media_root: Optional[Path] = None,
    fetch_covers: bool = True,
    generate_showreels: bool = True,
) -> None:
    """
    Generate a comprehensive metadata index for the media browser app.

    The index includes:
    - Media metadata with versions bundled together
    - Cover image paths (relative to media_root)
    - Playable file paths
    - TMDb data: ratings, cast, similar items, keywords, etc.
    - Showreel images for movies and episodes
    """
    print(f"Generating media index: {output_path}")

    # Convert media_root to string for relative path calculations
    media_root_str = str(media_root) if media_root else None

    # Process movies and series
    movies, movie_showreel_tasks = _process_movies(categories, cover_dir, fetch_covers, generate_showreels, media_root_str)
    series, episode_reel_tasks = _process_series(categories, cover_dir, fetch_covers, generate_showreels, media_root_str)

    # Sort results
    movies.sort(key=lambda x: (x["title"].lower(), x.get("year") or 0))
    series.sort(key=lambda x: x["title"].lower())

    # Calculate totals
    total_movie_versions = sum(len(m["versions"]) for m in movies)
    total_series_episodes = sum(
        sum(len(season.get("episodes", [])) for season in s["seasons"])
        for s in series
    )

    # Build and write the index
    index = {
        "version": 5,
        "generated_at": datetime.now().isoformat(),
        "media_root": media_root_str,
        "stats": {
            "total_movies": len(movies),
            "total_movie_versions": total_movie_versions,
            "total_series": len(series),
            "total_series_episodes": total_series_episodes,
        },
        "movies": movies,
        "series": series,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f"  Movies: {len(movies)} ({total_movie_versions} versions)")
    print(f"  Series: {len(series)} ({total_series_episodes} episodes)")
    print(f"  Output: {output_path}")

    # Generate showreels
    if generate_showreels:
        _run_showreel_generation(movie_showreel_tasks, episode_reel_tasks)
