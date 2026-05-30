"""Root registry, per-root context, and supervisor for multi-root media support."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import msgspec

from mediahive.config import load_config, save_config
from mediahive.index_store import IndexStore
from mediahive.models.events import ScanEvent, Task, Upsert

logger = logging.getLogger("mediahive.root_registry")

# ---------------------------------------------------------------------------
# Root path normalization and friendly name derivation
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Canonicalize a path for stable ID generation.

    - expanduser() only (do not resolve symlinks/mapped drives)
    - lower-case drive letter on Windows
    - strip trailing separators
    - use forward slashes
    """
    p = Path(path).expanduser()
    posix = p.as_posix()
    # Canonicalize drive-only roots ("Z:") to drive root ("Z:/") so paths are absolute.
    if len(posix) == 2 and posix[1] == ":" and posix[0].isalpha():
        posix = f"{posix}/"
    # Windows drive letter normalization
    if len(posix) >= 2 and posix[1] == ":":
        posix = posix[0].lower() + posix[1:]
    # Strip trailing slash (except root "/")
    while (
        len(posix) > 1
        and posix.endswith("/")
        and not (len(posix) == 3 and posix[1] == ":" and posix[2] == "/")
    ):
        posix = posix[:-1]
    return posix


def _derive_root_name(path: str) -> str:
    """Derive a friendly root name from a path basename/anchor."""
    normalized = (path or "").replace("\\", "/").rstrip("/")
    if not normalized:
        return "media"
    parts = [segment for segment in normalized.split("/") if segment]
    if parts:
        leaf = parts[-1]
        if len(leaf) == 2 and leaf[1] == ":" and leaf[0].isalpha():
            return leaf[0]
        return leaf
    return "media"


# ---------------------------------------------------------------------------
# Root entry
# ---------------------------------------------------------------------------


class RootEntry(msgspec.Struct):
    path: str
    root_id: str


# ---------------------------------------------------------------------------
# Root context
# ---------------------------------------------------------------------------


class RootContext:
    """Runtime container for a single media root."""

    def __init__(self, root_id: str, root_path: Path) -> None:
        self.root_id = root_id
        self.root_path = root_path
        self.status = "loading"
        self.error: str | None = None

        snapshot_path = root_path / ".mediahive" / "index.json"
        self.store = IndexStore(snapshot_path)

        # Scanner is injected later by the supervisor
        self.scanner: object | None = None

        # Event queue and consumer
        self._events: asyncio.Queue[ScanEvent] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start consumer immediately and load snapshot in the background."""
        self.status = "loading"
        self.error = None

        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_events())

        if self._startup_task is None or self._startup_task.done():
            self._startup_task = asyncio.create_task(self._load_snapshot_background())

    async def _load_snapshot_background(self) -> None:
        """Load snapshot without blocking root activation paths."""
        try:
            await self.store.load_snapshot()
            self.status = "ready"
            logger.info(
                "Root %s ready: %d movies, %d series",
                self.root_id,
                len(self.store.movies),
                len(self.store.series),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)
            logger.exception("Root %s failed to load snapshot", self.root_id)

    async def stop(self) -> None:
        """Stop consumer, flush snapshot, stop scanner."""
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._startup_task

        if self.scanner is not None:
            try:
                await self.scanner.stop()
            except Exception:
                logger.exception("Error stopping scanner for root %s", self.root_id)
            self.scanner = None

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task

        try:
            await self.store.flush_snapshot()
        except Exception:
            logger.exception("Error flushing snapshot for root %s", self.root_id)

    async def send_event(self, event: ScanEvent) -> None:
        """Push a scanner event into this root's queue."""
        await self._events.put(event)

    async def _consume_events(self) -> None:
        while True:
            try:
                event = await self._events.get()
                if isinstance(event, Upsert):
                    if event.kind == "movie":
                        self.store.upsert_movie(event.id, event.item, event.people)
                    else:
                        self.store.upsert_series(event.id, event.item, event.people)
                elif isinstance(event, Task):
                    self.store.broadcast_task(event.data)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Error processing scan event for root %s", self.root_id
                )


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    """Manages the active set of RootContexts and handles atomic replacement."""

    def __init__(self) -> None:
        # Active contexts keyed by root_id
        self._contexts: dict[str, RootContext] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, root_id: str) -> RootContext | None:
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
                "snapshot_loaded": ctx.store.snapshot_loaded,
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
            if ctx.status not in {"ready", "scanning"}:
                continue
            movies.extend(ctx.store.movies.values())
            series.extend(ctx.store.series.values())
            total_movie_versions += sum(len(m.files) for m in ctx.store.movies.values())
            total_series_episodes += sum(
                sum(len(season.episodes) for season in s.seasons)
                for s in ctx.store.series.values()
            )

        from datetime import datetime

        return {
            "v": 1,
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

    async def replace_roots(
        self, roots: dict[str, str]
    ) -> tuple[list[RootEntry], list[dict]]:
        """Atomically replace the active root set.

        Returns (accepted_entries, failed_entries_with_reason).
        """
        async with self._lock:
            # Validate and canonicalize
            candidates: list[RootEntry] = []
            seen_paths: set[str] = set()
            failed: list[dict] = []

            for path_str in roots.values():
                p = Path(path_str).expanduser()
                if not p.exists() or not p.is_dir():
                    failed.append({
                        "path": path_str,
                        "reason": "not a directory",
                    })
                    continue
                norm = _normalize_path(p.as_posix())
                if norm in seen_paths:
                    failed.append({
                        "path": path_str,
                        "reason": "duplicate path",
                    })
                    continue
                seen_paths.add(norm)

                # Friendly names should reflect the configured root path (e.g. "Z:" -> "Z"),
                # not the resolved physical target (which may be a UNC path).
                configured_path = Path(path_str).expanduser().as_posix()
                base_name = _derive_root_name(configured_path)
                unique_name = base_name
                suffix = 2
                existing_names = {e.name for e in candidates}
                while unique_name in existing_names:
                    unique_name = f"{base_name}{suffix}"
                    suffix += 1

                # root_id now uses the same friendly identifier as the display name.
                candidates.append(RootEntry(path=norm, root_id=unique_name))

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
                    roots={e.root_id: e.path for e in candidates},
                )
            )

            return candidates, failed

    async def shutdown(self) -> None:
        async with self._lock:
            for ctx in list(self._contexts.values()):
                await ctx.stop()
            self._contexts.clear()
