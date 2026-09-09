"""Media index generation — async generators for continuous scanning."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

from mediahive.hivescan.images import (
    download_backdrop_image,
    download_cast_profile,
    download_cover_image,
    download_season_poster,
)
from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.scanning import (
    find_cover_image,
    find_episode_files,
    find_metadata_probe_file,
    find_playable_file,
)
from mediahive.hivescan.showreel import (
    get_existing_episode_reel_path,
    get_existing_episode_reel_sources,
    get_existing_showreel_paths,
    get_existing_showreel_source_sets,
    probe_media_info,
)
from mediahive.hivescan.tmdb_client import (
    fetch_movie_info,
    fetch_season_details,
    fetch_series_info,
)
from mediahive.hivescan.utils import (
    RESOLUTION_PRIORITY,
    build_movie_id,
    build_series_id,
    get_added_timestamp,
    get_directory_size,
    get_media_folder_path,
    make_relative_path,
    sort_by_quality,
)
from mediahive.models.data import (
    Episode,
    Movie,
    Season,
    Series,
    Torrent,
)
from mediahive.models.tmdb import EpisodeInfo, Info, Person, SeasonInfo

logger = logging.getLogger("hivescan.indexer")

_HDR10PLUS_RE = re.compile(r"hdr10\+|hdr10plus", re.IGNORECASE)


def _infer_hdr10plus(*values: str | None) -> bool:
    """Infer HDR10+ from parsed release strings when probe data is ambiguous."""
    text = " ".join(v for v in values if v)
    return bool(_HDR10PLUS_RE.search(text))


def _compact_playable_file(file_key: str, playable_file: str | None) -> str | None:
    """Store playable paths compactly relative to the file key when possible."""
    if not playable_file:
        return None
    if playable_file == file_key:
        return None
    prefix = f"{file_key}/"
    if playable_file.startswith(prefix):
        rel = playable_file[len(prefix) :]
        return rel or None
    return playable_file


def _expand_playable_file(file_key: str, playable_file: str | None) -> str | None:
    """Expand compact playable paths back to media-root-relative paths."""
    if not playable_file:
        return file_key
    if playable_file.startswith("concat:") or "://" in playable_file:
        return playable_file
    prefix = f"{file_key}/"
    if playable_file.startswith(prefix):
        return playable_file
    if playable_file.startswith("/"):
        return playable_file.lstrip("/")
    return f"{file_key}/{playable_file}"


async def _build_torrent_info(
    item: ParsedContent,
    file_key: str,
    media_root: str | None = None,
) -> Torrent:
    """Build torrent info for a single torrent."""
    playable_file = await find_playable_file(item.path)
    probe_info = None
    probe_target = await find_metadata_probe_file(playable_file)
    if probe_target:
        probe_info = await probe_media_info(str(probe_target))

    if item.content_hash and item.content_hash.size == 0:
        item.content_hash.size = await asyncio.to_thread(
            get_directory_size,
            item.content_hash.path,
        )
    size = item.content_hash.size if item.content_hash else None
    added_at = await get_added_timestamp(item.path)

    playable_rel = make_relative_path(playable_file, media_root)
    hdr10plus_from_text = _infer_hdr10plus(
        item.title,
        item.quality,
        item.codec,
        item.audio,
        playable_rel,
    )
    return Torrent(
        title=item.title,
        playable_file=_compact_playable_file(file_key, playable_rel),
        resolution=(probe_info.resolution if probe_info else None) or item.resolution,
        quality=item.quality,
        network=item.network,
        codec=item.codec,
        audio=item.audio,
        audio_languages=probe_info.audio_languages if probe_info else None,
        subtitle_languages=probe_info.subtitle_languages if probe_info else None,
        hdr=probe_info.hdr if probe_info else False,
        dovi=probe_info.dovi if probe_info else False,
        atmos=probe_info.atmos if probe_info else False,
        hdr10plus=(probe_info.hdr10plus if probe_info else False)
        or hdr10plus_from_text,
        encoder=item.encoder,
        size=size,
        added_at=added_at,
    )


async def _cache_people_profiles(
    info: Info | None,
    people: dict[int, Person],
    media_folder: Path,
    media_root: str | None = None,
) -> tuple[Info | None, dict[int, Person]]:
    """Cache people profile images and keep people payload filename-only."""
    _ = media_root
    if not info or not info.cast:
        return info, people

    semaphore = asyncio.Semaphore(8)

    async def fetch_profile(cast_credit, person):
        async with semaphore:
            return cast_credit.id, await download_cast_profile(
                person.profile_path,
                media_folder,
                person.name,
                cast_credit.id,
            )

    tasks = []
    for cast_credit in info.cast:
        if cast_credit.id is None:
            continue
        person = people.get(cast_credit.id)
        if person is None or not person.profile_path:
            continue
        tasks.append(fetch_profile(cast_credit, person))

    for cast_id, downloaded_path in await asyncio.gather(*tasks):
        if downloaded_path:
            person = people[cast_id]
            people[cast_id] = Person(
                name=person.name,
                profile_path=Path(downloaded_path).name,
                gender=person.gender,
            )

    return info, people


async def _collect_episode_files(
    items: list[ParsedContent],
) -> dict[tuple[int, int], list[dict]]:
    """Collect all episode files from a list of torrent items.

    Returns dict mapping (season, episode) to list of file info dicts.
    """
    all_episode_files: dict[tuple[int, int], list[dict]] = {}
    probe_cache: dict[str, object] = {}

    async def get_probe(path: str):
        cached = probe_cache.get(path)
        if cached is not None:
            return cached
        probe = await probe_media_info(path)
        probe_cache[path] = probe
        return probe

    for item in items:
        episode_files = await find_episode_files(item.path)

        for (season_num, episode_num), files in episode_files.items():
            key = (season_num, episode_num)
            if key not in all_episode_files:
                all_episode_files[key] = []
            for file_path, file_size in files:
                probe = await get_probe(file_path)
                all_episode_files[key].append({
                    "path": file_path,
                    "size": file_size,
                    "probed_resolution": probe.resolution,
                    "audio_languages": probe.audio_languages,
                    "subtitle_languages": probe.subtitle_languages,
                    "hdr": probe.hdr,
                    "dovi": probe.dovi,
                    "atmos": probe.atmos,
                    "hdr10plus": probe.hdr10plus,
                    "resolution": item.resolution,
                    "quality": item.quality,
                    "network": item.network,
                    "codec": item.codec,
                    "audio": item.audio,
                    "encoder": item.encoder,
                    "torrent_path": item.path.as_posix(),
                    "torrent_title": item.title,
                })

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
                probe = await get_probe(playable)
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
                                item.content_hash.size = await asyncio.to_thread(
                                    get_directory_size,
                                    item.content_hash.path,
                                )
                            size = item.content_hash.size if item.content_hash else 0
                            all_episode_files[key].append({
                                "path": playable,
                                "size": size,
                                "probed_resolution": probe.resolution,
                                "audio_languages": probe.audio_languages,
                                "subtitle_languages": probe.subtitle_languages,
                                "hdr": probe.hdr,
                                "dovi": probe.dovi,
                                "atmos": probe.atmos,
                                "hdr10plus": probe.hdr10plus,
                                "resolution": item.resolution,
                                "quality": item.quality,
                                "network": item.network,
                                "codec": item.codec,
                                "audio": item.audio,
                                "encoder": item.encoder,
                                "torrent_path": item.path.as_posix(),
                                "torrent_title": item.title,
                            })

    return all_episode_files


def _build_episodes_data(
    episodes_in_season: dict[int, list[dict]],
    tmdb_episodes: dict[int, EpisodeInfo],
    series_folder: Path,
    season_num: int,
    generate_showreels: bool,
    episode_reel_tasks: list,
    series_title: str,
    media_root: str | None = None,
) -> list[Episode]:
    """Build episode data list for a season."""
    episodes_data = []

    for episode_num in sorted(episodes_in_season.keys()):
        episode_files = episodes_in_season[episode_num]
        sort_by_quality(episode_files)

        tmdb_ep = tmdb_episodes.get(episode_num)

        reel_path = None
        reel_sources = None
        if generate_showreels and episode_files:
            best_file = episode_files[0]["path"]
            if best_file and not best_file.endswith((".bdmv", ".ifo")):
                reel_sources = get_existing_episode_reel_sources(
                    series_folder,
                    season_num,
                    episode_num,
                    media_root=Path(media_root) if media_root else None,
                )
                reel_path = get_existing_episode_reel_path(
                    series_folder,
                    season_num,
                    episode_num,
                    media_root=Path(media_root) if media_root else None,
                )
                episode_reel_tasks.append((
                    best_file,
                    series_folder,
                    season_num,
                    episode_num,
                    series_title,
                ))

        files = {}
        for f in episode_files:
            relpath = make_relative_path(f["torrent_path"], media_root)
            playable_rel = make_relative_path(f["path"], media_root)
            hdr10plus_from_text = _infer_hdr10plus(
                f.get("torrent_title"),
                f.get("quality"),
                f.get("codec"),
                f.get("audio"),
                playable_rel,
            )
            files[relpath] = Torrent(
                title=f["torrent_title"],
                playable_file=_compact_playable_file(relpath, playable_rel),
                resolution=f.get("probed_resolution") or f.get("resolution"),
                quality=f.get("quality"),
                network=f.get("network"),
                codec=f.get("codec"),
                audio=f.get("audio"),
                audio_languages=f.get("audio_languages"),
                subtitle_languages=f.get("subtitle_languages"),
                hdr=bool(f.get("hdr")),
                dovi=bool(f.get("dovi")),
                atmos=bool(f.get("atmos")),
                hdr10plus=bool(f.get("hdr10plus")) or hdr10plus_from_text,
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
            reel_sources=reel_sources or None,
            files=files,
        )
        episodes_data.append(episode_data)

    return episodes_data


async def _build_seasons_data(
    all_episode_files: dict[tuple[int, int], list[dict]],
    tmdb_id: int | None,
    series_folder: Path,
    display_title: str,
    fetch_covers: bool,
    generate_showreels: bool,
    season_cache: dict,
    episode_reel_tasks: list,
    media_root: str | None = None,
) -> list[Season]:
    """Build seasons data structure for a series."""
    # Group episodes by season
    seasons_map: dict[int, dict[int, list[dict]]] = {}
    for (season_num, episode_num), files in all_episode_files.items():
        if season_num not in seasons_map:
            seasons_map[season_num] = {}
        seasons_map[season_num][episode_num] = files

    # Prefetch all missing season details in parallel; the loop below then
    # reads them straight from season_cache.
    if tmdb_id:
        missing = [
            season_num
            for season_num in seasons_map
            if (tmdb_id, season_num) not in season_cache
        ]
        if missing:
            semaphore = asyncio.Semaphore(4)

            async def prefetch(num: int) -> None:
                async with semaphore:
                    season_cache[tmdb_id, num] = await fetch_season_details(
                        tmdb_id, num
                    )

            await asyncio.gather(*(prefetch(num) for num in missing))

    seasons_data = []
    for season_num in sorted(seasons_map.keys()):
        episodes_in_season = seasons_map[season_num]

        # Fetch TMDb season details if we have a TMDb ID
        tmdb_season = None
        tmdb_episodes: dict[int, EpisodeInfo] = {}

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
    media_root: str | None = None,
    root_id: str | None = None,
) -> AsyncIterator[
    tuple[str, Movie, tuple[str, Path, str] | None, dict[int, Person], list[str]]
]:
    """Async generator that processes all movies.

    Yields:
        Tuples of ``(movie_id, Movie, showreel_task_or_None, people, scanned)``
        as each movie is processed.  ``scanned`` lists the media-root-relative
        torrent paths whose content was (re)scanned to build the movie.

    """
    _ = root_id
    # In-memory cache for TMDb lookups
    movie_tmdb_cache: dict[
        str,
        tuple[Info, str | None, str | None, dict[int, Person]] | None,
    ] = {}

    async def get_movie_tmdb(
        title: str,
        year: int | None,
    ) -> tuple[Info, str | None, str | None, dict[int, Person]] | None:
        cache_key = f"{title.lower()}:{year}"
        if cache_key in movie_tmdb_cache:
            return movie_tmdb_cache[cache_key]
        tmdb_info = await fetch_movie_info(title, year)
        movie_tmdb_cache[cache_key] = tmdb_info
        return tmdb_info

    async def has_playable(item: ParsedContent) -> bool:
        return await find_playable_file(item.path) is not None

    # Filter movies with playable files
    valid_movies = [
        item for item in categories[ContentType.MOVIE] if await has_playable(item)
    ]
    skipped = len(categories[ContentType.MOVIE]) - len(valid_movies)
    if skipped > 0:
        logger.debug(
            "  Skipped %d movie torrents with no playable video files", skipped
        )

    # Group by title+year
    movie_groups: dict[str, list[ParsedContent]] = {}
    for item in valid_movies:
        key = f"{item.title.lower()}:{item.year or 0}"
        if key not in movie_groups:
            movie_groups[key] = []
        movie_groups[key].append(item)

    # Re-group by TMDb ID
    tmdb_movie_groups: dict[int, dict] = {}
    no_tmdb_movie_groups: dict[str, dict] = {}

    if movie_groups:
        logger.info(
            "  Processing %d unique movies (%d total versions)...",
            len(movie_groups),
            len(categories[ContentType.MOVIE]),
        ) if movie_groups else None

    # Prefetch TMDb lookups for all unique titles in parallel; the grouping
    # loop below then reads them straight from movie_tmdb_cache.
    if movie_groups:
        semaphore = asyncio.Semaphore(4)
        first_by_key = {
            f"{items[0].title.lower()}:{items[0].year}": items[0]
            for items in movie_groups.values()
        }

        async def prefetch(item: ParsedContent) -> None:
            async with semaphore:
                await get_movie_tmdb(item.title, item.year)

        await asyncio.gather(*(prefetch(item) for item in first_by_key.values()))

    for idx, (_movie_key, items) in enumerate(movie_groups.items(), 1):
        first_item = items[0]
        logger.debug(
            "    [%d/%d] %s (%s)",
            idx,
            len(movie_groups),
            first_item.title,
            first_item.year,
        )

        tmdb_result = await get_movie_tmdb(first_item.title, first_item.year)

        if tmdb_result and tmdb_result[0].tmdb_id:
            tmdb_info, poster_path_ref, backdrop_path_ref, people = tmdb_result
            if tmdb_info.tmdb_id not in tmdb_movie_groups:
                tmdb_movie_groups[tmdb_info.tmdb_id] = {
                    "tmdb_info": tmdb_info,
                    "poster_path_ref": poster_path_ref,
                    "backdrop_path_ref": backdrop_path_ref,
                    "people": people,
                    "items": [],
                    "torrent_titles": set(),
                    "year": first_item.year,
                }
            else:
                tmdb_movie_groups[tmdb_info.tmdb_id]["people"].update(people)
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
    for group_data in tmdb_movie_groups.values():
        tmdb_info = group_data["tmdb_info"]
        poster_path_ref = group_data["poster_path_ref"]
        backdrop_path_ref = group_data["backdrop_path_ref"]
        people = group_data["people"]
        items = group_data["items"]
        torrent_titles = group_data["torrent_titles"]
        year = group_data["year"]

        display_title = tmdb_info.title
        item_id = build_movie_id(display_title, year)
        media_folder = get_media_folder_path(display_title, year, "movie", cover_dir)

        # Find/download cover
        cover_path = None
        if fetch_covers:
            cover_path = await find_cover_image(display_title, year, "movie", cover_dir)
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = await find_cover_image(tt, year, "movie", cover_dir)
                    if cover_path:
                        break
            if not cover_path and poster_path_ref:
                cover_path = await download_cover_image(
                    poster_path_ref,
                    display_title,
                    year,
                    "movie",
                    cover_dir,
                )
            tmdb_info, people = await _cache_people_profiles(
                tmdb_info,
                people,
                media_folder,
                media_root,
            )

        files = {}
        for item in items:
            relpath = make_relative_path(item.path.as_posix(), media_root)
            torrent = await _build_torrent_info(item, relpath, media_root)
            files[relpath] = torrent

        sort_by_quality(list(files.values()))

        # Queue showreel generation
        showreel_paths = []
        showreel_source_sets = []
        showreel_task = None
        if generate_showreels and files:
            # Find the best version for showreel (highest quality)
            best_relpath = max(
                files.keys(),
                key=lambda k: (
                    RESOLUTION_PRIORITY.get(files[k].resolution or "", 0),
                    files[k].size or 0,
                    k,
                ),
            )
            best_version = files[best_relpath]
            best_playable = _expand_playable_file(
                best_relpath, best_version.playable_file
            )
            if best_playable and not best_playable.endswith(".ifo"):
                abs_playable = (
                    (Path(media_root) / best_playable).as_posix()
                    if media_root
                    else best_playable
                )
                showreel_source_sets = get_existing_showreel_source_sets(
                    media_folder, media_root=Path(media_root) if media_root else None
                )
                showreel_paths = get_existing_showreel_paths(
                    media_folder, media_root=Path(media_root) if media_root else None
                )
                showreel_task = (abs_playable, media_folder, display_title)

        # Download backdrop
        backdrop_path = None
        if fetch_covers and backdrop_path_ref:
            backdrop_path = await download_backdrop_image(
                backdrop_path_ref,
                display_title,
                year,
                "movie",
                cover_dir,
            )

        version_timestamps = [v.added_at for v in files.values() if v.added_at]
        newest = max(version_timestamps) if version_timestamps else None

        movie = Movie(
            title=display_title,
            info=tmdb_info,
            year=year,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            backdrop_path=make_relative_path(backdrop_path, media_root),
            showreel_images=showreel_paths or None,
            showreel_source_sets=showreel_source_sets or None,
            files=files,
        )
        scanned = [
            make_relative_path(item.path.as_posix(), media_root) for item in items
        ]
        yield item_id, movie, showreel_task, people, scanned

    # Process movies without TMDb info
    for group_data in no_tmdb_movie_groups.values():
        items = group_data["items"]
        title = group_data["title"]
        year = group_data["year"]
        item_id = build_movie_id(title, year)

        cover_path = (
            await find_cover_image(title, year, "movie", cover_dir)
            if fetch_covers
            else None
        )

        files = {}
        for item in items:
            relpath = make_relative_path(item.path.as_posix(), media_root)
            torrent = await _build_torrent_info(item, relpath, media_root)
            files[relpath] = torrent

        sort_by_quality(list(files.values()))

        showreel_paths = []
        showreel_source_sets = []
        showreel_task = None
        if generate_showreels and files:
            # Find the best version for showreel (highest quality)
            best_relpath = max(
                files.keys(),
                key=lambda k: (
                    RESOLUTION_PRIORITY.get(files[k].resolution or "", 0),
                    files[k].size or 0,
                    k,
                ),
            )
            best_version = files[best_relpath]
            best_playable = _expand_playable_file(
                best_relpath, best_version.playable_file
            )
            if best_playable and not best_playable.endswith((
                ".bdmv",
                ".ifo",
            )):
                abs_playable = (
                    (Path(media_root) / best_playable).as_posix()
                    if media_root
                    else best_playable
                )
                media_folder = get_media_folder_path(title, year, "movie", cover_dir)
                showreel_source_sets = get_existing_showreel_source_sets(
                    media_folder,
                    media_root=Path(media_root) if media_root else None,
                )
                showreel_paths = get_existing_showreel_paths(
                    media_folder,
                    media_root=Path(media_root) if media_root else None,
                )
                showreel_task = (abs_playable, media_folder, title)

        version_timestamps = [v.added_at for v in files.values() if v.added_at]
        newest = max(version_timestamps) if version_timestamps else None

        movie = Movie(
            title=title,
            year=year,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            showreel_images=showreel_paths or None,
            showreel_source_sets=showreel_source_sets or None,
            files=files,
        )
        scanned = [
            make_relative_path(item.path.as_posix(), media_root) for item in items
        ]
        yield item_id, movie, showreel_task, {}, scanned


async def _process_series(
    categories: dict,
    cover_dir: Path,
    fetch_covers: bool,
    generate_showreels: bool,
    media_root: str | None = None,
    root_id: str | None = None,
) -> AsyncIterator[
    tuple[
        str, Series, list[tuple[str, Path, int, int, str]], dict[int, Person], list[str]
    ]
]:
    """Async generator that processes all series.

    Yields:
        Tuples of ``(series_id, Series, episode_reel_tasks, people, scanned)``
        as each series is processed.  ``scanned`` lists the media-root-relative
        torrent paths whose content was (re)scanned to build the series.

    """
    _ = root_id
    # In-memory cache for TMDb lookups
    series_tmdb_cache: dict[
        str,
        tuple[Info, str | None, str | None, dict[int, Person]] | None,
    ] = {}
    season_cache: dict[tuple[int, int], SeasonInfo | None] = {}

    async def get_series_tmdb(
        title: str,
    ) -> tuple[Info, str | None, str | None, dict[int, Person]] | None:
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
    valid_series = [
        item for item in categories[ContentType.SERIES] if await has_video_content(item)
    ]
    skipped = len(categories[ContentType.SERIES]) - len(valid_series)
    if skipped > 0:
        logger.info(
            "  Skipped %d series torrents with no playable video files", skipped
        )

    # Group by title
    series_groups: dict[str, list[ParsedContent]] = {}
    for item in valid_series:
        key = item.title.lower()
        if key not in series_groups:
            series_groups[key] = []
        series_groups[key].append(item)

    # Re-group by TMDb ID
    tmdb_groups: dict[int, dict] = {}
    no_tmdb_groups: dict[str, dict] = {}

    if series_groups:
        logger.info(
            "  Processing %d unique series (%d total entries)...",
            len(series_groups),
            len(categories[ContentType.SERIES]),
        )

    # Prefetch TMDb lookups for all unique titles in parallel; the grouping
    # loop below then reads them straight from series_tmdb_cache.
    if series_groups:
        semaphore = asyncio.Semaphore(4)
        first_by_key = {
            items[0].title.lower(): items[0] for items in series_groups.values()
        }

        async def prefetch(item: ParsedContent) -> None:
            async with semaphore:
                await get_series_tmdb(item.title)

        await asyncio.gather(*(prefetch(item) for item in first_by_key.values()))

    for idx, (_series_key, items) in enumerate(series_groups.items(), 1):
        first_item = items[0]
        logger.debug("    [%d/%d] %s", idx, len(series_groups), first_item.title)

        tmdb_result = await get_series_tmdb(first_item.title)

        if tmdb_result and tmdb_result[0].tmdb_id:
            tmdb_info, poster_path_ref, backdrop_path_ref, people = tmdb_result
            if tmdb_info.tmdb_id not in tmdb_groups:
                tmdb_groups[tmdb_info.tmdb_id] = {
                    "tmdb_info": tmdb_info,
                    "poster_path_ref": poster_path_ref,
                    "backdrop_path_ref": backdrop_path_ref,
                    "people": people,
                    "items": [],
                    "torrent_titles": set(),
                }
            else:
                tmdb_groups[tmdb_info.tmdb_id]["people"].update(people)
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
        poster_path_ref = group_data["poster_path_ref"]
        backdrop_path_ref = group_data["backdrop_path_ref"]
        people = group_data["people"]
        items = group_data["items"]
        torrent_titles = group_data["torrent_titles"]

        display_title = tmdb_info.title
        series_id = build_series_id(display_title)

        logger.debug("  [%d/%d] %s", series_idx, len(tmdb_groups), display_title)

        series_folder = get_media_folder_path(display_title, None, "series", cover_dir)

        # Find/download cover
        cover_path = None
        if fetch_covers:
            cover_path = await find_cover_image(
                display_title, None, "series", cover_dir
            )
            if not cover_path:
                for tt in torrent_titles:
                    cover_path = await find_cover_image(tt, None, "series", cover_dir)
                    if cover_path:
                        break
            if not cover_path and poster_path_ref:
                cover_path = await download_cover_image(
                    poster_path_ref,
                    display_title,
                    None,
                    "series",
                    cover_dir,
                )
            tmdb_info, people = await _cache_people_profiles(
                tmdb_info,
                people,
                series_folder,
                media_root,
            )

        # Download backdrop
        backdrop_path = None
        if fetch_covers and backdrop_path_ref:
            backdrop_path = await download_backdrop_image(
                backdrop_path_ref,
                display_title,
                None,
                "series",
                cover_dir,
            )

        # Collect and build episode data
        all_episode_files = await _collect_episode_files(items)
        ep_reel_tasks: list[tuple[str, Path, int, int, str]] = []
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
            logger.info(
                "    Skipping %s - no episodes in the %d scanned torrent(s)",
                display_title,
                len(items),
            )
            continue

        different_titles = sorted(
            t for t in torrent_titles if t.lower() != display_title.lower()
        )
        item_timestamps = [await get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series = Series(
            title=display_title,
            info=tmdb_info,
            alternative_titles=different_titles or None,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            backdrop_path=make_relative_path(backdrop_path, media_root),
            seasons=seasons_data,
        )
        scanned = [
            make_relative_path(item.path.as_posix(), media_root) for item in items
        ]
        yield series_id, series, ep_reel_tasks, people, scanned

    # Process series without TMDb info
    for group_data in no_tmdb_groups.values():
        items = group_data["items"]
        title = group_data["title"]
        series_id = build_series_id(title)

        cover_path = (
            await find_cover_image(title, None, "series", cover_dir)
            if fetch_covers
            else None
        )
        series_folder = get_media_folder_path(title, None, "series", cover_dir)

        all_episode_files = await _collect_episode_files(items)
        ep_reel_tasks: list[tuple[str, Path, int, int, str]] = []
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
            logger.info(
                "    Skipping %s - no episodes in the %d scanned torrent(s)",
                title,
                len(items),
            )
            continue

        item_timestamps = [await get_added_timestamp(item.path) for item in items]
        item_timestamps = [t for t in item_timestamps if t is not None]
        newest = max(item_timestamps) if item_timestamps else None

        series = Series(
            title=title,
            newest=newest,
            cover_path=make_relative_path(cover_path, media_root),
            seasons=seasons_data,
        )
        scanned = [
            make_relative_path(item.path.as_posix(), media_root) for item in items
        ]
        yield series_id, series, ep_reel_tasks, {}, scanned
