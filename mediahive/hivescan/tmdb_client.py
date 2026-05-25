#!/usr/bin/env python3
"""TMDb Client - Fetch movie and TV series metadata from The Movie Database (TMDb)."""

import asyncio
import hashlib
import json
import os
import sys
import urllib.parse
from pathlib import Path

import httpx
from aiopathlib import AsyncPath

from mediahive.models.tmdb import (
    CastMember,
    EpisodeInfo,
    Info,
    SeasonInfo,
    SimilarMedia,
)

# TMDb API configuration
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "6bd914e6a5df1c6d1ddf622cf2dbc232")
TMDB_API_BASE = "https://api.themoviedb.org/3"

# API response cache directory (can be overridden via set_cache_dir)
_tmdb_cache_dir: Path | None = None

# Persistent async HTTP client for connection reuse
_http_client: httpx.AsyncClient | None = None


def set_cache_dir(cache_dir: Path) -> None:
    """Set the directory for TMDb API response cache."""
    global _tmdb_cache_dir
    _tmdb_cache_dir = cache_dir


def _get_cache_dir() -> Path:
    """Get the TMDb cache directory, defaulting to current directory if not set."""
    if _tmdb_cache_dir is not None:
        return _tmdb_cache_dir
    # Fallback to .tmdb-cache in current working directory
    return Path.cwd() / ".tmdb-cache"


def _get_http_client() -> httpx.AsyncClient:
    """Get or create a persistent async HTTP client for connection reuse."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=TMDB_API_BASE,
            headers={"Accept": "application/json", "User-Agent": "TorrentManager/1.0"},
            timeout=10.0,
            http2=True,  # Enable HTTP/2 for better performance
        )
    return _http_client


# Sentinel value to distinguish "cached None" from "not in cache"
_NOT_FOUND = object()


def _get_cache_path(endpoint: str, params: dict[str, str]) -> Path:
    """Generate a cache file path for an API request."""
    # Create a stable cache key from endpoint and sorted params
    cache_key = endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
    cache_hash = hashlib.sha256(cache_key.encode()).hexdigest()
    return _get_cache_dir() / f"{cache_hash}.json"


async def _load_from_cache(cache_path: Path):
    """Load cached response. Returns _NOT_FOUND if not cached."""
    if not await AsyncPath(cache_path).exists():
        return _NOT_FOUND
    try:
        text = await AsyncPath(cache_path).read_text(encoding="utf-8")
        data = json.loads(text)
        # Handle cached "no results" / errors
        if data.get("_cached_none"):
            return None
        return data
    except OSError, TypeError, json.JSONDecodeError:
        return _NOT_FOUND


async def _save_to_cache(cache_path: Path, data: dict | None) -> None:
    """Save response to cache."""
    try:
        await AsyncPath(_get_cache_dir()).mkdir(parents=True, exist_ok=True)
        text = json.dumps({"_cached_none": True}) if data is None else json.dumps(data)
        await AsyncPath(cache_path).write_text(text, encoding="utf-8")
    except OSError, TypeError, ValueError:
        pass  # Cache write failures are not critical


# EpisodeInfo, SeasonInfo, Info imported from mediahive.models.tmdb


async def tmdb_api_request(
    endpoint: str, params: dict[str, str] | None = None
) -> dict[str, str] | None:
    """Make a request to the TMDb API with disk caching and connection reuse."""
    params = params or {}

    # Check cache first (before adding API key to params for cache key)
    cache_path = _get_cache_path(endpoint, params)
    cached = await _load_from_cache(cache_path)
    if cached is not _NOT_FOUND:
        return cached

    params["api_key"] = TMDB_API_KEY

    try:
        client = _get_http_client()
        response = await client.get(endpoint, params=params)

        if response.status_code == 429:
            # Rate limited - wait and retry
            print("  Rate limited, waiting...", file=sys.stderr)
            await asyncio.sleep(1)
            return await tmdb_api_request(
                endpoint, {k: v for k, v in params.items() if k != "api_key"}
            )

        response.raise_for_status()
        data = response.json()
        # Cache immediately after receiving response
        await _save_to_cache(cache_path, data)
        return data
    except httpx.HTTPStatusError:
        # Cache the failure (None) to avoid retrying
        await _save_to_cache(cache_path, None)
        return None
    except httpx.HTTPError:
        # Don't cache network errors - they may be transient
        return None


async def fetch_movie_details(movie_id: int) -> dict | None:
    """Fetch movie info including credits, similar, keywords, and alt titles."""
    # Use append_to_response to get multiple data in one request
    return await tmdb_api_request(
        f"/movie/{movie_id}",
        {"append_to_response": "credits,similar,keywords,alternative_titles"},
    )


async def fetch_series_details(series_id: int) -> dict | None:
    """Fetch detailed TV series info including credits, similar, and keywords."""
    # Use append_to_response to get multiple data in one request
    return await tmdb_api_request(
        f"/tv/{series_id}", {"append_to_response": "credits,similar,keywords"}
    )


async def fetch_season_details(series_id: int, season_number: int) -> SeasonInfo | None:
    """Fetch detailed season info including all episodes.

    Returns season metadata with episode list including:
    - Episode names, overviews, air dates
    - Episode still images
    - Runtime, ratings
    - Directors for each episode
    """
    data = await tmdb_api_request(
        f"/tv/{series_id}/season/{season_number}", {"append_to_response": "images"}
    )

    if not data:
        return None

    # Parse episodes
    episodes = []
    for ep_data in data.get("episodes", []):
        # Get director from crew
        director = None
        for crew_member in ep_data.get("crew", []):
            if crew_member.get("job") == "Director":
                director = crew_member.get("name")
                break

        episode = EpisodeInfo(
            episode_number=ep_data.get("episode_number", 0),
            season_number=ep_data.get("season_number", season_number),
            name=ep_data.get("name"),
            overview=ep_data.get("overview"),
            air_date=ep_data.get("air_date"),
            runtime=ep_data.get("runtime"),
            still_path=ep_data.get("still_path"),
            vote_average=ep_data.get("vote_average"),
            vote_count=ep_data.get("vote_count"),
            director=director,
        )
        episodes.append(episode)

    return SeasonInfo(
        season_number=data.get("season_number", season_number),
        name=data.get("name"),
        overview=data.get("overview"),
        air_date=data.get("air_date"),
        poster_path=data.get("poster_path"),
        episode_count=len(episodes),
        episodes=episodes,
    )


def _map_person_gender(value: object) -> str | None:
    """Map TMDb person gender codes to stable string values."""
    if value == 1:
        return "female"
    if value == 2:
        return "male"
    if value == 3:
        return "non_binary"
    if value == 0:
        return "unknown"
    return None


def _generate_title_variants(words: list[str], min_words: int = 2) -> list[str]:
    """Generate title variants by progressively removing words from both ends.

    Order: full title, then shorter from end, then shorter from start.
    """
    if len(words) < min_words:
        return [" ".join(words)] if words else []

    variants = []

    # First try full title
    variants.append(" ".join(words))

    # Then try removing from end (most common: edition names at end)
    variants.extend(
        " ".join(words[:num_words])
        for num_words in range(len(words) - 1, min_words - 1, -1)
    )

    # Then try removing from start (garbage at beginning)
    variants.extend(
        " ".join(words[start:]) for start in range(1, len(words) - min_words + 1)
    )

    # Finally try middle portions (remove from both ends)
    for start in range(1, len(words) - min_words):
        for end in range(len(words) - 1, start + min_words - 1, -1):
            variant = " ".join(words[start:end])
            if variant not in variants:
                variants.append(variant)

    return variants


def _normalize_for_match(text: str) -> set[str]:
    """Normalize text into a set of lowercase words for matching."""
    # Remove common punctuation and split
    normalized = text.lower()
    for char in ".:;,!?-_'\"()[]{}":
        normalized = normalized.replace(char, " ")
    return {w for w in normalized.split() if len(w) > 1}


def _titles_match(original_title: str, tmdb_title: str, search_query: str) -> bool:
    """Check if TMDb result title reasonably matches our original title.

    Uses word overlap to verify the result is relevant, preventing
    false matches from short queries like "The" or just a year.
    """
    original_words = _normalize_for_match(original_title)
    tmdb_words = _normalize_for_match(tmdb_title)
    query_words = _normalize_for_match(search_query)

    # Remove common stop words that don't help matching
    stop_words = {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "is",
        "it",
    }
    original_significant = original_words - stop_words
    tmdb_significant = tmdb_words - stop_words
    query_significant = query_words - stop_words

    # The query words should be a subset of both original and tmdb titles
    # (the search query came from the original, and should match the result)
    if not query_significant:
        # If query has no significant words, require direct word overlap
        return bool(original_words & tmdb_words)

    # Check if significant query words appear in the TMDb title
    query_in_tmdb = query_significant & tmdb_significant
    if not query_in_tmdb:
        return False

    # Also require some overlap between original and TMDb
    # This catches cases where query matches but it's the wrong movie
    overlap = original_significant & tmdb_significant

    # Either good overlap, or the TMDb title is contained in original (or vice versa)
    return (
        bool(overlap)
        or tmdb_significant <= original_significant
        or original_significant <= tmdb_significant
    )


async def _search_movie_with_fallbacks(title: str, year: int | None) -> dict | None:
    """Search for a movie with progressive title shortening fallbacks.

    PTN often includes edition names (THEATRICAL CUT, DIRECTOR'S CUT, etc.)
    or garbage at the beginning/end of the title.
    Year is always included when available as it's more reliable.
    Results are validated with fuzzy matching to prevent false positives.
    """
    words = title.split()
    variants = _generate_title_variants(words, min_words=2)

    def _result_matches(
        top_result: dict, original_title: str, search_query: str
    ) -> bool:
        """Check if result matches against either title or original_title."""
        tmdb_title = top_result.get("title", "")
        tmdb_original = top_result.get("original_title", "")
        return _titles_match(original_title, tmdb_title, search_query) or _titles_match(
            original_title, tmdb_original, search_query
        )

    # Try all variants with year first
    if year:
        for search_title in variants:
            params = {
                "query": search_title,
                "include_adult": "false",
                "year": str(year),
            }
            data = await tmdb_api_request("/search/movie", params)
            if data and data.get("results"):
                # Validate the top result matches our title (check both title and original_title)
                top_result = data["results"][0]
                if _result_matches(top_result, title, search_title):
                    return data

    # Then try without year
    for search_title in variants:
        params = {"query": search_title, "include_adult": "false"}
        data = await tmdb_api_request("/search/movie", params)
        if data and data.get("results"):
            top_result = data["results"][0]
            if _result_matches(top_result, title, search_title):
                return data

    return None


async def fetch_movie_info(title: str, year: int | None = None) -> Info | None:
    """Fetch comprehensive movie info from TMDb."""
    data = await _search_movie_with_fallbacks(title, year)

    if not data or not data.get("results"):
        return None

    result = data["results"][0]
    movie_id = result["id"]

    # Fetch full details with credits, similar movies, and keywords
    details = await fetch_movie_details(movie_id)
    if not details:
        # Fall back to basic info from search
        return Info(
            tmdb_id=movie_id,
            title=result.get("title"),
            original_title=result.get("original_title"),
            rating=result.get("vote_average"),
            vote_count=result.get("vote_count"),
            overview=result.get("overview"),
            poster_path=result.get("poster_path"),
            backdrop_path=result.get("backdrop_path"),
            release_date=result.get("release_date"),
        )

    # Extract genres
    genres = [g["name"] for g in details.get("genres", [])]

    # Extract keywords
    keywords_data = details.get("keywords", {}).get("keywords", [])
    keywords = [k["name"] for k in keywords_data]

    # Extract alternative titles (deduplicated)
    alt_titles_data = details.get("alternative_titles", {}).get("titles", [])
    alt_titles_set = set()
    for t in alt_titles_data:
        title_str = t.get("title", "").strip()
        if title_str:
            alt_titles_set.add(title_str)
    # Remove the main title and original title to avoid duplicates
    main_title = details.get("title", "")
    orig_title = details.get("original_title", "")
    alt_titles_set.discard(main_title)
    alt_titles_set.discard(orig_title)
    alternative_titles = sorted(alt_titles_set) if alt_titles_set else None

    # Extract full cast
    credits = details.get("credits", {})
    cast_data = credits.get("cast", [])
    cast = [
        CastMember(
            name=c["name"],
            character=c.get("character", ""),
            profile_path=c.get("profile_path"),
            gender=_map_person_gender(c.get("gender")),
        )
        for c in cast_data
    ]

    # Extract director from crew
    crew = credits.get("crew", [])
    directors = [c["name"] for c in crew if c.get("job") == "Director"]
    director = directors[0] if directors else None

    # Extract similar movies (limit to 10)
    similar_data = details.get("similar", {}).get("results", [])[:10]
    similar = [
        SimilarMedia(id=s["id"], title=s["title"], poster_path=s.get("poster_path"))
        for s in similar_data
    ]

    return Info(
        tmdb_id=movie_id,
        title=details.get("title"),
        original_title=details.get("original_title"),
        alternative_titles=alternative_titles,
        rating=details.get("vote_average"),
        vote_count=details.get("vote_count"),
        overview=details.get("overview"),
        genres=genres or None,
        release_date=details.get("release_date"),
        runtime=details.get("runtime"),
        status=details.get("status"),
        tagline=details.get("tagline"),
        poster_path=details.get("poster_path"),
        backdrop_path=details.get("backdrop_path"),
        similar=similar or None,
        keywords=keywords or None,
        cast=cast or None,
        director=director,
    )


async def _search_series_with_fallbacks(title: str) -> dict | None:
    """Search for a TV series with progressive title shortening fallbacks.

    PTN often includes extra text in the title at beginning or end.
    Results are validated with fuzzy matching to prevent false positives.
    """
    words = title.split()
    variants = _generate_title_variants(words, min_words=1)

    for search_title in variants:
        params = {"query": search_title, "include_adult": "false"}
        data = await tmdb_api_request("/search/tv", params)
        if data and data.get("results"):
            # Validate the top result matches our title
            top_result = data["results"][0]
            tmdb_title = top_result.get("name", "")
            if _titles_match(title, tmdb_title, search_title):
                return data

    return None


async def fetch_series_info(title: str) -> Info | None:
    """Fetch comprehensive TV series info from TMDb."""
    data = await _search_series_with_fallbacks(title)

    if not data or not data.get("results"):
        return None

    result = data["results"][0]
    series_id = result["id"]

    # Fetch full details with credits, similar shows, and keywords
    details = await fetch_series_details(series_id)
    if not details:
        # Fall back to basic info from search
        return Info(
            tmdb_id=series_id,
            title=result.get("name"),
            original_title=result.get("original_name"),
            rating=result.get("vote_average"),
            vote_count=result.get("vote_count"),
            overview=result.get("overview"),
            poster_path=result.get("poster_path"),
            backdrop_path=result.get("backdrop_path"),
        )

    # Extract genres
    genres = [g["name"] for g in details.get("genres", [])]

    # Extract keywords (TV uses "results" instead of "keywords")
    keywords_data = details.get("keywords", {}).get("results", [])
    keywords = [k["name"] for k in keywords_data]

    # Extract full cast
    credits = details.get("credits", {})
    cast_data = credits.get("cast", [])
    cast = [
        CastMember(
            name=c["name"],
            character=c.get("character", ""),
            profile_path=c.get("profile_path"),
            gender=_map_person_gender(c.get("gender")),
        )
        for c in cast_data
    ]

    # Extract creators
    creators = [c["name"] for c in details.get("created_by", [])]

    # Extract networks
    networks = [n["name"] for n in details.get("networks", [])]

    # Extract similar series (limit to 10)
    similar_data = details.get("similar", {}).get("results", [])[:10]
    similar = [
        SimilarMedia(id=s["id"], title=s["name"], poster_path=s.get("poster_path"))
        for s in similar_data
    ]

    # Get first air date
    first_air_date = details.get("first_air_date")

    return Info(
        tmdb_id=series_id,
        title=details.get("name"),
        original_title=details.get("original_name"),
        rating=details.get("vote_average"),
        vote_count=details.get("vote_count"),
        overview=details.get("overview"),
        genres=genres or None,
        release_date=first_air_date,
        status=details.get("status"),
        tagline=details.get("tagline"),
        poster_path=details.get("poster_path"),
        backdrop_path=details.get("backdrop_path"),
        similar=similar or None,
        keywords=keywords or None,
        cast=cast or None,
        creators=creators or None,
        number_of_seasons=details.get("number_of_seasons"),
        number_of_episodes=details.get("number_of_episodes"),
        networks=networks or None,
    )
