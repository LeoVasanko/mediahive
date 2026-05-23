"""Root registry, per-root context, and supervisor for multi-root media support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import msgspec

from mediahive.config import load_config, save_config
from mediahive.index_store import IndexStore
from mediahive.models.events import ScanEvent, Task, Upsert
from mediahive.models.data import TaskInfo

logger = logging.getLogger("mediahive.root_registry")

# ---------------------------------------------------------------------------
# Root ID
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Canonicalize a path for stable ID generation.

    - resolve() to follow symlinks and normalize ..
    - lower-case drive letter on Windows
    - strip trailing separators
    - use forward slashes
    """
    p = Path(path).expanduser().resolve()
    posix = p.as_posix()
    # Windows drive letter normalization
    if len(posix) >= 2 and posix[1] == ":":
        posix = posix[0].lower() + posix[1:]
    # Strip trailing slash (except root "/")
    while len(posix) > 1 and posix.endswith("/"):
        posix = posix[:-1]
    return posix


def compute_root_id(path: str) -> str:
    """Return a stable 12-char hex root ID from a normalized path."""
    normalized = _normalize_path(path)
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return h[:12]


# ---------------------------------------------------------------------------
# Root entry
# ---------------------------------------------------------------------------


class RootEntry(msgspec.Struct):
    name: str
    path: str
    root_id: str


# ---------------------------------------------------------------------------
# Root context
# ---------------------------------------------------------------------------


class RootContext:
    """Runtime container for a single media root."""

    def __init__(self, root_id: str, root_path: Path):
        self.root_id = root_id
        self.root_path = root_path
        self.status = "loading"
        self.error: Optional[str] = None

        snapshot_path = root_path / ".mediahive" / "index.json"
        self.store = IndexStore(snapshot_path, media_root=root_path.as_posix(), root_id=root_id)

        # Scanner is injected later by the supervisor
        self.scanner: Optional[object] = None

        # Event queue and consumer
        self._events: asyncio.Queue[ScanEvent] = asyncio.Queue()
        self._consumer_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Load snapshot and start event consumer."""
        try:
            await self.store.load_snapshot()
            self.status = "ready"
            logger.info(
                "Root %s ready: %d movies, %d series",
                self.root_id,
                len(self.store.movies),
                len(self.store.series),
            )
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)
            logger.exception("Root %s failed to load snapshot", self.root_id)

        self._consumer_task = asyncio.create_task(self._consume_events())

    async def stop(self) -> None:
        """Stop consumer, flush snapshot, stop scanner."""
        if self.scanner is not None:
            try:
                await self.scanner.stop()
            except Exception:
                logger.exception("Error stopping scanner for root %s", self.root_id)
            self.scanner = None

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        try:
            await self.store.flush_snapshot()
        except Exception:
            logger.exception("Error flushing snapshot for root %s", self.root_id)

    async def send_event(self, event: ScanEvent) -> None:
        """Called by the scanner to push an event into this root's queue."""
        await self._events.put(event)

    async def _consume_events(self) -> None:
        while True:
            try:
                event = await self._events.get()
                if isinstance(event, Upsert):
                    if event.kind == "movie":
                        self.store.upsert_movie(event.item)
                    else:
                        self.store.upsert_series(event.item)
                elif isinstance(event, Task):
                    self.store.broadcast_task(event.data)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Error processing scan event for root %s", self.root_id)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    """Manages the active set of RootContexts and handles atomic replacement."""

    def __init__(self):
        # Active contexts keyed by root_id
        self._contexts: dict[str, RootContext] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, root_id: str) -> Optional[RootContext]:
        return self._contexts.get(root_id)

    def all_contexts(self) -> dict[str, RootContext]:
        return self._contexts.copy()

    def all_statuses(self) -> list[dict]:
        return [
            {
                "root_id": ctx.root_id,
                "path": ctx.root_path.as_posix(),
                "status": ctx.status,
                "error": ctx.error,
                "movies": len(ctx.store.movies),
                "series": len(ctx.store.series),
            }
            for ctx in self._contexts.values()
        ]

    def merged_index(self) -> dict:
        """Return a merged index snapshot from all ready roots."""
        movies = []
        series = []
        total_movie_versions = 0
        total_series_episodes = 0
        for ctx in self._contexts.values():
            if ctx.status != "ready" and ctx.status != "scanning":
                continue
            movies.extend(ctx.store.movies.values())
            series.extend(ctx.store.series.values())
            total_movie_versions += sum(len(m.torrents) for m in ctx.store.movies.values())
            total_series_episodes += sum(
                sum(len(season.episodes) for season in s.seasons)
                for s in ctx.store.series.values()
            )

        from datetime import datetime
        from mediahive.models.data import IndexSnapshot, MediaStats

        return {
            "version": 7,
            "generated_at": datetime.now().isoformat(),
            "stats": {
                "total_movies": len(movies),
                "total_movie_versions": total_movie_versions,
                "total_series": len(series),
                "total_series_episodes": total_series_episodes,
            },
            "movies": movies,
            "series": series,
        }

    # ------------------------------------------------------------------
    # Atomic replacement
    # ------------------------------------------------------------------

    async def replace_roots(self, roots: dict[str, str]) -> tuple[list[RootEntry], list[dict]]:
        """Atomically replace the active root set.

        Returns (accepted_entries, failed_entries_with_reason).
        """
        async with self._lock:
            # Validate and canonicalize
            candidates: list[RootEntry] = []
            seen_paths: set[str] = set()
            seen_ids: set[str] = set()
            failed: list[dict] = []

            for name, path_str in roots.items():
                name = name.strip()
                if not name:
                    failed.append({"name": name, "path": path_str, "reason": "empty name"})
                    continue
                p = Path(path_str).expanduser().resolve()
                if not p.exists() or not p.is_dir():
                    failed.append({"name": name, "path": path_str, "reason": "not a directory"})
                    continue
                norm = _normalize_path(p.as_posix())
                if norm in seen_paths:
                    failed.append({"name": name, "path": path_str, "reason": "duplicate path"})
                    continue
                seen_paths.add(norm)
                rid = compute_root_id(str(p))
                if rid in seen_ids:
                    # Extremely unlikely hash collision — fall back to full hash
                    rid = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
                seen_ids.add(rid)
                candidates.append(RootEntry(name=name, path=norm, root_id=rid))

            # Build desired root_id set
            desired_ids = {e.root_id for e in candidates}

            # Stop scanners for roots that are being removed or changed
            old_contexts = list(self._contexts.values())
            for ctx in old_contexts:
                if ctx.root_id not in desired_ids:
                    asyncio.create_task(ctx.stop())

            # Prepare new contexts
            new_contexts: dict[str, RootContext] = {}
            for entry in candidates:
                existing = self._contexts.get(entry.root_id)
                if existing and existing.root_path.as_posix() == entry.path:
                    # Reuse existing context
                    new_contexts[entry.root_id] = existing
                else:
                    # If existing path changed, stop old one
                    if existing:
                        asyncio.create_task(existing.stop())
                    ctx = RootContext(entry.root_id, Path(entry.path))
                    await ctx.start()
                    new_contexts[entry.root_id] = ctx

            # Atomic swap
            self._contexts = new_contexts

            # Persist to config
            cfg = load_config()
            save_config(
                msgspec.structs.replace(
                    cfg,
                    roots={e.name: e.path for e in candidates},
                )
            )

            return candidates, failed

    async def shutdown(self) -> None:
        async with self._lock:
            for ctx in list(self._contexts.values()):
                await ctx.stop()
            self._contexts.clear()
