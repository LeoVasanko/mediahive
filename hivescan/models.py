"""Data models for the download scanner."""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


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
    _size: Optional[int] = None

    @property
    def size(self) -> int:
        """Get the size, computing it lazily if needed."""
        if self._size is None:
            from hivescan.utils import get_directory_size

            self._size = get_directory_size(self.path)
        return self._size

    @size.setter
    def size(self, value: int) -> None:
        self._size = value

    @classmethod
    def from_path(cls, path: Path) -> "ContentHash":
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
    year: Optional[int] = None
    resolution: Optional[str] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    audio: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_name: Optional[str] = None
    encoder: Optional[str] = None
    language: Optional[str] = None
    is_directory: bool = False
    raw_parsed: dict = field(default_factory=dict)
    content_hash: Optional[ContentHash] = None
