"""Data models for the download scanner."""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ContentType(Enum):
    """Types of content that can be identified."""

    MOVIE = "movie"
    SERIES = "series"
    OTHER = "other"


@dataclass
class ContentHash:
    """Hash representing a file or directory's content based on torrent name."""

    path: Path
    hash: str
    size: int = 0

    @classmethod
    def from_path(cls, path: Path) -> ContentHash:
        """Generate a content hash based on torrent name."""
        hash_val = hashlib.md5(path.name.encode()).hexdigest()[:16]
        return cls(path=path, hash=hash_val)


@dataclass
class ParsedContent:
    """Information parsed from a torrent name."""

    path: Path
    name: str
    content_type: ContentType
    title: str
    year: int | None = None
    resolution: str | None = None
    quality: str | None = None
    network: str | None = None
    codec: str | None = None
    audio: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_name: str | None = None
    encoder: str | None = None
    language: str | None = None
    is_directory: bool = False
    raw_parsed: dict = field(default_factory=dict)
    content_hash: ContentHash | None = None
