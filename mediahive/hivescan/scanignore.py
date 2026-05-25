"""Gitignore-style path matcher for controlling which directories the scanner visits.

Reads patterns from ``<media_root>/.mediahive/scanignore``.  The file uses the
same syntax as ``.gitignore``:

- Blank lines and lines starting with ``#`` are ignored.
- A pattern without a slash is matched against the **name** of every directory
  entry (e.g. ``incomplete`` matches any directory called *incomplete*).
- A pattern with a slash is matched against the **path relative to media root**
  (e.g. ``Downloads/ISOs/`` skips that specific subtree).
- A leading ``!`` negates the pattern (re-includes a previously excluded path).
- A leading ``/`` anchors the pattern to the media root.
- ``*`` matches anything except ``/``;  ``**`` matches zero or more directories.
- Trailing ``/`` restricts the match to directories (always the case for the
  scanner, since it only walks directories).

Default built-in excludes (always active, before the user file is read)::

    .mediahive
    .torrents
    incomplete
    .incomplete
    .Trash*
    $RECYCLE.BIN
    System Volume Information
"""

from __future__ import annotations

import re
from pathlib import Path

# Built-in patterns that are always excluded (before user file)
_BUILTIN_EXCLUDES: list[str] = [
    ".mediahive",
    ".torrents",
    "incomplete",
    ".incomplete",
    ".Trash*",
    "$RECYCLE.BIN",
    "System Volume Information",
]


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a single gitignore-style pattern to a compiled regex.

    The regex is applied to forward-slash normalised relative paths.
    """
    # Trailing slash just means "directories only" — always true for us
    pattern = pattern.rstrip("/")

    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern.lstrip("/")

    has_slash = "/" in pattern

    # Translate glob-like syntax to regex:
    # 1. Escape regex-special chars (except our glob chars)
    # 2. Handle ** (match zero or more path segments)
    # 3. Handle * (match anything except /)
    # 4. Handle ? (match single char except /)
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # **
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    parts.append("(?:.+/)?")
                    i += 3
                    continue
                parts.append(".*")
                i += 2
                continue
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c in r"\.+^${}()|[]":
            parts.append("\\" + c)
            i += 1
        else:
            parts.append(c)
            i += 1

    regex_str = "".join(parts)

    regex_str = (
        "^" + regex_str if anchored or has_slash else "(?:^|/)" + regex_str
    )

    # Must match the whole remaining path or be a prefix (directory match)
    regex_str += "(?:/.*)?$"

    return re.compile(regex_str, re.IGNORECASE)


class ScanIgnore:
    """Matcher that decides whether a path should be scanned or excluded."""

    def __init__(self, media_root: Path) -> None:
        self.media_root = media_root.resolve()
        self._rules: list[tuple[bool, re.Pattern[str]]] = []  # (negated, regex)
        self._load_builtins()
        self._load_file()

    def _load_builtins(self) -> None:
        for pat in _BUILTIN_EXCLUDES:
            self._rules.append((False, _pattern_to_regex(pat)))

    def _load_file(self) -> None:
        scanignore = self.media_root / ".mediahive" / "scanignore"
        if not scanignore.exists():
            return
        for line in scanignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            self._rules.append((negated, _pattern_to_regex(line)))

    def is_excluded(self, path: Path) -> bool:
        """Return True if *path* should be skipped by the scanner.

        *path* must be an absolute path under ``media_root``.
        """
        try:
            rel = path.resolve().relative_to(self.media_root)
        except ValueError:
            return False  # outside media root — not our business

        # Normalise to forward slashes for matching
        rel_str = rel.as_posix()

        excluded = False
        for negated, regex in self._rules:
            if regex.search(rel_str):
                excluded = not negated
        return excluded

    @property
    def file_path(self) -> Path:
        return self.media_root / ".mediahive" / "scanignore"
