"""Scan orchestration — background tasks for continuous media scanning.

All scanning logic lives here in hivescan.  Communication with the mediahive
server happens exclusively through an async ``send`` callable that pushes
:class:`~mediahive.models.events.ScanEvent` messages (``Upsert`` /
``Task``) onto an :class:`asyncio.Queue` owned by the caller.

The scanner recursively walks the media root, respecting ignore patterns
defined in ``.mediahive/scanignore`` (gitignore-style syntax).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiopathlib import AsyncPath

from mediahive.hivescan.indexer import _process_movies, _process_series
from mediahive.hivescan.models import ContentType, ParsedContent
from mediahive.hivescan.parsing import parse_download
from mediahive.hivescan.scanignore import ScanIgnore
from mediahive.hivescan.scanning import categorize_downloads, clear_scan_caches
from mediahive.hivescan.showreel import (
    episode_reel_exists,
    generate_episode_reel,
    generate_showreel_images,
    get_existing_episode_reel_sources,
    get_existing_showreel_source_sets,
    load_probe_records,
    movie_showreels_exist,
    probe_records_dirty,
    save_probe_records,
)
from mediahive.hivescan.tmdb_client import set_cache_dir
from mediahive.hivescan.utils import (
    DEFAULT_OUTPUT_FOLDER,
    make_relative_path,
)
from mediahive.models.data import TaskInfo
from mediahive.models.events import (
    EpisodeReel,
    MovieShowreel,
    ScanEvent,
    Sync,
    Task,
    Upsert,
)

logger = logging.getLogger("hivescan.scanner")

# Discovery walk tunables: number of concurrent directory readers, and the
# exponential backoff schedule for subtrees that repeatedly contain no items
# of interest (60s, 120s, 240s, ... capped at 1h).  Only subtrees with at
# least _MIN_TRACKED_DIRS directories earn backoff entries — smaller trees
# are cheap enough to rescan every pass and are left untracked.
_SCAN_WORKERS = 16
_EMPTY_BACKOFF_BASE_S = 60.0
_EMPTY_BACKOFF_MAX_S = 3600.0
_MIN_TRACKED_DIRS = 8

# Type alias for the send callable
Send = Callable[[ScanEvent], Awaitable[None]]


# ---------------------------------------------------------------------------
# RootScanner — per-root scanning runtime
# ---------------------------------------------------------------------------


class RootScanner:
    """Scanner instance for a single media root."""

    def __init__(
        self,
        root_id: str,
        media_root: Path,
        send: Send,
        indexed_paths: Callable[[], set[str]] | None = None,
    ) -> None:
        self.root_id = root_id
        self.media_root = media_root
        self._send = send
        # Returns the torrent paths currently present in the index; used to
        # reprocess items missing from the database despite unchanged mtimes.
        self._indexed_paths = indexed_paths
        self._output_dir = media_root / DEFAULT_OUTPUT_FOLDER
        self._scanignore = ScanIgnore(media_root)

        # Runtime state
        self._scan_task: asyncio.Task | None = None
        self._showreel_queue: asyncio.Queue = asyncio.Queue()
        self._showreel_worker_task: asyncio.Task | None = None
        self._rescan_worker_task: asyncio.Task | None = None
        self._seen_mtimes: dict[str, int] = {}
        # Candidates that were fully processed but produced no index entries
        # (no playable file, no parseable episodes, or non-media content).
        # Tracked with their mtime so they are not reprocessed on every
        # rescan — the DB-aware gating alone cannot skip them, because they
        # never appear in the index.  An entry is dropped as soon as the
        # item's mtime changes or it starts producing index entries.
        self._empty_mtimes: dict[str, int] = {}
        self._empty_dirty = False
        # Per-directory backoff state (relpath -> {until, streak, items})
        self._dir_state: dict[str, dict] = {}
        self._dir_state_dirty = False

        # Persisted state (under .mediahive/)
        self._scan_state_path = self._output_dir / "scan-state.json"
        self._reel_state_path = self._output_dir / "reel-state.json"
        self._probe_cache_path = self._output_dir / "probe-cache.json"
        self._reel_state: dict[str, dict[str, dict]] = {"movies": {}, "episodes": {}}
        self._reel_state_dirty = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise and start background workers."""
        await AsyncPath(self._output_dir).mkdir(parents=True, exist_ok=True)
        set_cache_dir(self._output_dir / ".tmdb-cache")
        await asyncio.to_thread(self._load_scan_state)
        await asyncio.to_thread(load_probe_records, self._probe_cache_path)
        await asyncio.to_thread(self._load_reel_state)

        logger.info(
            "Scanner started for root %s — path=%s, scanignore=%s, known-paths=%d",
            self.root_id,
            self.media_root,
            "loaded" if self._scanignore.file_path.exists() else "defaults only",
            len(self._seen_mtimes),
        )

        self._showreel_worker_task = asyncio.create_task(self._showreel_worker())
        self._rescan_worker_task = asyncio.create_task(self._rescan_loop())

    async def stop(self) -> None:
        """Cancel all background tasks."""
        tasks = [
            self._scan_task,
            self._showreel_worker_task,
            self._rescan_worker_task,
        ]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        # Wait briefly for graceful shutdown to avoid lingering scanner tasks.
        # All tasks are awaited concurrently (a slow one must not delay the
        # others), under a single overall timeout.
        pending = [task for task in tasks if task and not task.done()]
        if pending:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=2.0
                )
        # Best-effort persistence of scanner state, concurrently.
        with contextlib.suppress(Exception):
            await asyncio.gather(
                asyncio.to_thread(self._save_scan_state),
                asyncio.to_thread(self._save_reel_state),
                asyncio.to_thread(save_probe_records),
            )

    def is_scanning(self) -> bool:
        return self._scan_task is not None and not self._scan_task.done()

    def showreel_queue_size(self) -> int:
        return self._showreel_queue.qsize()

    # ------------------------------------------------------------------
    # Scan-state persistence
    # ------------------------------------------------------------------

    def _load_scan_state(self) -> None:
        try:
            data = json.loads(self._scan_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except OSError, ValueError:
            logger.exception("Failed to load scan state from %s", self._scan_state_path)
            return
        seen = data.get("seen_mtimes")
        if isinstance(seen, dict):
            self._seen_mtimes = {str(k): int(v) for k, v in seen.items()}
        empty = data.get("empty_mtimes")
        if isinstance(empty, dict):
            self._empty_mtimes = {str(k): int(v) for k, v in empty.items()}
        logger.info(
            "Loaded scan state from %s: seen=%d, empty=%d",
            self._scan_state_path,
            len(self._seen_mtimes),
            len(self._empty_mtimes),
        )
        dir_state = data.get("dir_state")
        if isinstance(dir_state, dict):
            for key, value in dir_state.items():
                if isinstance(value, dict):
                    self._dir_state[str(key)] = {
                        "until": float(value.get("until") or 0.0),
                        "streak": int(value.get("streak") or 0),
                        "items": bool(value.get("items")),
                    }

    def _save_scan_state(self) -> None:
        try:
            payload = json.dumps({
                "version": 2,
                "seen_mtimes": self._seen_mtimes,
                "empty_mtimes": self._empty_mtimes,
                "dir_state": self._dir_state,
            })
            tmp = self._scan_state_path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._scan_state_path)
            logger.info(
                "Saved scan state to %s: seen=%d, empty=%d, dir_state=%d",
                self._scan_state_path,
                len(self._seen_mtimes),
                len(self._empty_mtimes),
                len(self._dir_state),
            )
        except OSError, TypeError, ValueError:
            logger.exception("Failed to save scan state to %s", self._scan_state_path)

    # ------------------------------------------------------------------
    # Reel-state persistence and gating
    #
    # ``reel-state.json`` records, per media folder (movies) or per episode,
    # whether reel generation succeeded or failed for the current video file
    # (keyed by mtime+size).  This stops the showreel worker from retrying
    # permanently unreadable files on every scan, and stops short videos from
    # being re-queued forever because they legitimately have fewer reels than
    # the maximum.
    # ------------------------------------------------------------------

    # Retry delay for failed generations: 6h, 12h, 24h, ... capped at a week.
    _REEL_RETRY_BASE_HOURS = 6
    _REEL_RETRY_MAX_HOURS = 168

    def _load_reel_state(self) -> None:
        try:
            data = json.loads(self._reel_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except OSError, ValueError:
            logger.exception("Failed to load reel state from %s", self._reel_state_path)
            return
        for bucket in ("movies", "episodes"):
            records = data.get(bucket)
            if isinstance(records, dict):
                self._reel_state[bucket] = records

    def _save_reel_state(self) -> None:
        try:
            payload = json.dumps({"version": 1, **self._reel_state})
            tmp = self._reel_state_path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._reel_state_path)
        except OSError, TypeError, ValueError:
            logger.exception("Failed to save reel state to %s", self._reel_state_path)

    def _reel_state_bucket(
        self,
        kind: str,
        media_folder: Path,
        season_num: int | None = None,
        episode_num: int | None = None,
    ) -> tuple[dict[str, dict], str]:
        folder_key = make_relative_path(
            media_folder.as_posix(), self.media_root.as_posix()
        )
        if kind == "movie":
            return self._reel_state["movies"], folder_key
        key = f"{folder_key}#S{season_num:02d}E{episode_num:02d}"
        return self._reel_state["episodes"], key

    def _record_reel_state(
        self,
        kind: str,
        media_folder: Path,
        season_num: int | None,
        episode_num: int | None,
        video_path: str,
        sig: tuple[int, int] | None,
        status: str,
        error: str | None = None,
    ) -> None:
        bucket, key = self._reel_state_bucket(
            kind, media_folder, season_num, episode_num
        )
        previous = bucket.get(key)
        attempts = (
            0 if status == "done" else int((previous or {}).get("attempts", 0)) + 1
        )
        bucket[key] = {
            "video": make_relative_path(video_path, self.media_root.as_posix()),
            "mtime": sig[0] if sig else None,
            "size": sig[1] if sig else None,
            "status": status,
            "attempts": attempts,
            "last": time.time(),
            "error": error,
        }
        self._reel_state_dirty = True

    async def _reel_needed(
        self,
        kind: str,
        video_path: str,
        media_folder: Path,
        season_num: int | None = None,
        episode_num: int | None = None,
    ) -> tuple[bool, tuple[int, int] | None]:
        """Decide whether a reel-generation task should be queued.

        Returns ``(needed, signature)`` where signature is the video file's
        (mtime, size) or None when it cannot be stat'ed.
        """
        sig: tuple[int, int] | None = None
        try:
            st = await AsyncPath(video_path).stat()
            sig = (int(st.st_mtime), st.st_size)
        except OSError, ValueError:
            pass

        bucket, key = self._reel_state_bucket(
            kind, media_folder, season_num, episode_num
        )
        rec = bucket.get(key)
        if (
            rec is not None
            and sig is not None
            and rec.get("mtime") == sig[0]
            and rec.get("size") == sig[1]
        ):
            if rec.get("status") == "done":
                return False, sig
            attempts = int(rec.get("attempts", 1))
            delay = (
                min(
                    self._REEL_RETRY_BASE_HOURS * 2**attempts,
                    self._REEL_RETRY_MAX_HOURS,
                )
                * 3600
            )
            if time.time() - float(rec.get("last", 0)) < delay:
                return False, sig

        if kind == "movie":
            exists = await movie_showreels_exist(media_folder)
        else:
            exists = await episode_reel_exists(media_folder, season_num, episode_num)
        if exists:
            if rec is None and sig is not None:
                # Reels already on disk (e.g. generated before this feature):
                # record success so future scans take the cheap path.
                self._record_reel_state(
                    kind, media_folder, season_num, episode_num, video_path, sig, "done"
                )
            return False, sig
        return True, sig

    # ------------------------------------------------------------------
    # Internal scan orchestration
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        self._scan_task = asyncio.create_task(self._run_scan())

    async def _rescan_loop(self) -> None:
        try:
            while True:
                self._start_scan()
                if self._scan_task:
                    await self._scan_task
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Rescan loop error")

    async def _discover_downloads(
        self, task_id: str
    ) -> tuple[list[ParsedContent], dict[str, int], set[str]]:
        """Walk the media root with a pool of parallel workers.

        Each directory costs exactly one ``scandir`` round trip (entry
        mtimes come from the directory listing itself), and up to
        ``_SCAN_WORKERS`` directories are read concurrently — over a
        network mount the walk is latency-bound, so this is close to an
        N-fold speedup over the old serialized recursion.

        Subtree scheduling is adaptive (``_dir_state``, persisted in
        scan-state.json): directories that repeatedly yield no candidates
        are penalised with exponential backoff (1, 2, 4, ... minutes up to
        ``_EMPTY_BACKOFF_MAX_S``), while trees with items of interest or
        recent changes are scanned every pass and queued first, so new
        downloads appear almost immediately even while a large cold tree
        is still being walked.

        Returns ``(downloads, found_mtimes, known_paths)``: the new/changed
        items to process, the mtimes observed for them (committed to
        ``_seen_mtimes`` only after the scan completes successfully), and
        the full set of media-root-relative candidate paths (backed-off
        subtrees contribute their previously known paths, so deletion
        sync never drops them unseen).
        """
        downloads: list[ParsedContent] = []
        found_mtimes: dict[str, int] = {}
        known_paths: set[str] = set()
        media_root_str = self.media_root.as_posix()
        now = time.time()
        dirs_visited = 0
        # Torrent paths currently in the index; a matching mtime alone is not
        # enough to skip an item that the database does not actually have.
        indexed = self._indexed_paths() if self._indexed_paths else None

        media_container_dirs = {"BDMV", "VIDEO_TS", "HVDVD_TS"}
        video_extensions = {
            ".mkv",
            ".mp4",
            ".avi",
            ".m4v",
            ".mov",
            ".wmv",
            ".flv",
            ".webm",
            ".ts",
            ".m2ts",
        }

        queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        seq = itertools.count()

        def _scan_dir(
            directory: Path,
            stop_event: threading.Event,
        ) -> tuple[list[tuple[Path, int | None]], list[tuple[Path, int]], bool]:
            """One scandir; DirEntry stats are free from the listing."""
            child_dirs: list[tuple[Path, int | None]] = []
            video_files: list[tuple[Path, int]] = []
            is_media_container = False
            with os.scandir(directory) as entries:
                for entry in entries:
                    if stop_event.is_set():
                        break
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if name.upper() in media_container_dirs:
                            is_media_container = True
                        try:
                            mtime: int | None = int(entry.stat().st_mtime)
                        except OSError:
                            mtime = None
                        child_dirs.append((Path(entry.path), mtime))
                    else:
                        item = Path(entry.path)
                        if item.suffix.lower() not in video_extensions:
                            continue
                        try:
                            video_files.append((item, int(entry.stat().st_mtime)))
                        except OSError:
                            continue
            return child_dirs, video_files, is_media_container

        async def _report(detail: str) -> None:
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=detail,
                    )
                )
            )

        async def _register_candidate(
            path: Path, relpath: str, mtime: int | None
        ) -> bool:
            """Record a candidate; return True when it is new/changed."""
            known_paths.add(relpath)
            if mtime is None:
                try:
                    mtime = int((await AsyncPath(path).stat()).st_mtime)
                except OSError, ValueError:
                    logger.info(
                        "Skipping candidate, stat failed: %s "
                        "(in_seen=%s, stored_mtime=%s)",
                        relpath,
                        relpath in self._seen_mtimes,
                        self._seen_mtimes.get(relpath),
                    )
                    return False
            stored = self._seen_mtimes.get(relpath)
            if stored == mtime:
                if indexed is not None and relpath in indexed:
                    # Produces index entries; drop any stale empty-marker.
                    if relpath in self._empty_mtimes:
                        del self._empty_mtimes[relpath]
                        self._empty_dirty = True
                    return False
                if indexed is None or self._empty_mtimes.get(relpath) == mtime:
                    return False
                # Unchanged on disk but missing from the index (snapshot
                # wiped, upsert lost, ...): reprocess it unless it is not
                # indexable content anyway.
                logger.info(
                    "Reprocessing unchanged item missing from index: %s "
                    "(stored_mtime=%d == current_mtime=%d, indexed=%s, "
                    "empty_marker=%s)",
                    relpath,
                    stored,
                    mtime,
                    "n/a" if indexed is None else "no",
                    self._empty_mtimes.get(relpath),
                )
                parsed = await parse_download(path)
                if parsed.content_type is ContentType.OTHER:
                    self._empty_mtimes[relpath] = mtime
                    self._empty_dirty = True
                    return False
                downloads.append(parsed)
                return True
            logger.info(
                "Treating as new/changed: %s (stored_mtime=%s, "
                "current_mtime=%d, in_seen=%s, in_index=%s, empty_marker=%s, "
                "seen_total=%d, known_total=%d)",
                relpath,
                "ABSENT" if stored is None else str(stored),
                mtime,
                relpath in self._seen_mtimes,
                "n/a" if indexed is None else (relpath in indexed),
                self._empty_mtimes.get(relpath),
                len(self._seen_mtimes),
                len(known_paths),
            )
            found_mtimes[relpath] = mtime
            downloads.append(await parse_download(path))
            return True

        def _carry_known(relpath: str) -> None:
            """Keep previously known paths of a skipped (backed-off) subtree."""
            prefix = relpath + "/"
            for p in self._seen_mtimes:
                if p == relpath or p.startswith(prefix):
                    known_paths.add(p)

        def _complete_node(node: dict) -> None:
            """Fold a finished subtree into the backoff state and its parent."""
            rel = node["rel"]
            if rel is not None and not stop_event.is_set():
                prev = self._dir_state.get(rel)
                if node["items"] or node["changed"] or node["dirs"] < _MIN_TRACKED_DIRS:
                    # Hot, changed, or tiny subtree: no tracking needed —
                    # default scheduling (scan every pass) is already right.
                    if prev is not None:
                        del self._dir_state[rel]
                        self._dir_state_dirty = True
                else:
                    streak = int((prev or {}).get("streak", 0)) + 1
                    delay = min(
                        _EMPTY_BACKOFF_BASE_S * 2 ** (streak - 1),
                        _EMPTY_BACKOFF_MAX_S,
                    )
                    new = {"until": now + delay, "streak": streak, "items": False}
                    if prev != new:
                        self._dir_state[rel] = new
                        self._dir_state_dirty = True
            parent = node["parent"]
            if parent is not None:
                parent["items"] = parent["items"] or node["items"]
                parent["changed"] = parent["changed"] or node["changed"]
                parent["dirs"] += node["dirs"]
                parent["pending"] -= 1
                if parent["pending"] == 0:
                    _complete_node(parent)

        def _child_priority(state: dict | None) -> int:
            # Items of interest (or recently changed) first, never-seen
            # directories next (they may hold brand-new content), known
            # empty last.
            if state is None:
                return 1
            if state.get("items"):
                return 0
            return 2

        async def _enqueue_children(
            node: dict, child_dirs: list[tuple[Path, int | None]]
        ) -> None:
            for child, child_mtime in child_dirs:
                crel = make_relative_path(str(child), media_root_str)
                state = self._dir_state.get(crel)
                if (
                    state is not None
                    and not state.get("items")
                    and float(state.get("until", 0.0)) > now
                ):
                    _carry_known(crel)
                    continue
                cnode = {
                    "parent": node,
                    "pending": 1,  # self-reference, released after processing
                    "items": False,
                    "changed": False,
                    "dirs": 0,
                    "rel": crel,
                }
                node["pending"] += 1
                await queue.put((
                    _child_priority(state),
                    next(seq),
                    child,
                    child_mtime,
                    cnode,
                ))

        async def _worker(stop_event: threading.Event) -> None:
            nonlocal dirs_visited
            while True:
                _, _, path, mtime, node = await queue.get()
                rel = make_relative_path(str(path), media_root_str)
                try:
                    if stop_event.is_set():
                        continue
                    dirs_visited += 1
                    node["dirs"] += 1
                    try:
                        (
                            child_dirs,
                            video_files,
                            is_media_container,
                        ) = await asyncio.to_thread(_scan_dir, path, stop_event)
                    except OSError, PermissionError:
                        logger.debug("Cannot list directory: %s", path)
                        # Unreadable (e.g. a transient network-mount failure):
                        # keep its previously known paths so the Sync event
                        # cannot delete the subtree's items, and don't let it
                        # earn backoff.
                        _carry_known(rel)
                        node["changed"] = True
                        child_dirs, video_files, is_media_container = [], [], False
                    if self._scanignore:
                        child_dirs = [
                            c
                            for c in child_dirs
                            if not self._scanignore.is_excluded(c[0])
                        ]
                        video_files = [
                            f
                            for f in video_files
                            if not self._scanignore.is_excluded(f[0])
                        ]
                    if dirs_visited % 8 == 1:
                        await _report(f"Scanning: {rel} ({len(downloads)} found)")

                    if is_media_container or not child_dirs:
                        # Disc structure or leaf directory: the directory
                        # itself is the candidate.  For backoff purposes it
                        # only counts as an item of interest when it actually
                        # holds video — a leaf of nothing but flac/mp3 files
                        # must not keep its whole subtree hot.
                        # Directory mtimes are lazy (NTFS) and unreliable
                        # (SMB), so fold in the newest video file mtime —
                        # file mtimes are reliable, and this is what makes a
                        # still-growing download show up immediately.
                        if is_media_container or video_files:
                            node["items"] = True
                        if video_files:
                            newest = max(fm for _, fm in video_files)
                            mtime = max(mtime or 0, newest)
                        if await _register_candidate(path, rel, mtime):
                            node["changed"] = True
                    else:
                        for file_path, file_mtime in video_files:
                            file_rel = make_relative_path(
                                str(file_path), media_root_str
                            )
                            node["items"] = True
                            # No "or" short-circuit here: every file must be
                            # registered even after an earlier file in this
                            # directory was found changed, otherwise the
                            # skipped files fall out of known_paths, get
                            # pruned at finalize, and are rediscovered as
                            # "new" on every subsequent scan.
                            if await _register_candidate(
                                file_path, file_rel, file_mtime
                            ):
                                node["changed"] = True
                        await _enqueue_children(node, child_dirs)
                except Exception:
                    # Never let one bad directory kill the worker or truncate
                    # the walk — an incomplete known_paths set would make the
                    # Sync event delete items that still exist on disk.
                    logger.exception("Discovery failed for directory: %s", path)
                    _carry_known(rel)
                    node["changed"] = True
                finally:
                    node["pending"] -= 1
                    if node["pending"] == 0:
                        _complete_node(node)
                    queue.task_done()

        logger.debug("Starting filesystem discovery at %s", self.media_root)
        await _report(f"Scanning: {self.media_root}")

        stop_event = threading.Event()
        try:
            child_dirs, video_files, _ = await asyncio.to_thread(
                _scan_dir, self.media_root, stop_event
            )
        except asyncio.CancelledError:
            stop_event.set()
            raise
        except OSError, PermissionError:
            # Failing to list the root must fail the whole scan: returning an
            # empty known_paths here would make the Sync event delete every
            # item in the index.
            raise RuntimeError(f"Cannot list media root: {self.media_root}")

        if self._scanignore:
            child_dirs = [
                c for c in child_dirs if not self._scanignore.is_excluded(c[0])
            ]
            video_files = [
                f for f in video_files if not self._scanignore.is_excluded(f[0])
            ]

        root_node = {
            "parent": None,
            "pending": 1,
            "items": False,
            "changed": False,
            "dirs": 0,
            "rel": None,
        }
        for file_path, file_mtime in video_files:
            rel = make_relative_path(str(file_path), media_root_str)
            root_node["items"] = True
            await _register_candidate(file_path, rel, file_mtime)
        await _enqueue_children(root_node, child_dirs)

        workers = [
            asyncio.create_task(_worker(stop_event)) for _ in range(_SCAN_WORKERS)
        ]
        try:
            await queue.join()
        finally:
            stop_event.set()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        logger.debug(
            "Discovery complete: %d downloads found, %d directories visited",
            len(downloads),
            dirs_visited,
        )
        return downloads, found_mtimes, known_paths

    async def _finalize_scan(
        self, found_mtimes: dict[str, int], known_paths: set[str]
    ) -> None:
        """Commit discovery state after a fully completed scan.

        Commits observed mtimes (so cancelled/failed scans retry their items),
        prunes vanished paths, persists state when anything changed, and sends
        the Sync event that lets the store drop deleted torrents.
        """
        pruned = sorted(k for k in self._seen_mtimes if k not in known_paths)
        if pruned:
            logger.info(
                "Finalize: pruning %d previously seen path(s) not in this "
                "scan's known set: %s%s",
                len(pruned),
                pruned[:10],
                " ..." if len(pruned) > 10 else "",
            )
        committed = {k: v for k, v in self._seen_mtimes.items() if k in known_paths}
        committed.update(found_mtimes)
        state_changed = committed != self._seen_mtimes
        self._seen_mtimes = committed
        if found_mtimes:
            logger.info(
                "Finalize: committing %d new/changed mtime(s): %s%s",
                len(found_mtimes),
                sorted(found_mtimes)[:10],
                " ..." if len(found_mtimes) > 10 else "",
            )

        # Drop empty-markers for vanished or since-changed paths.
        pruned_empty = {
            k: v
            for k, v in self._empty_mtimes.items()
            if k in known_paths and committed.get(k) == v
        }
        if pruned_empty != self._empty_mtimes:
            self._empty_mtimes = pruned_empty
            self._empty_dirty = True

        await self._send(Sync(paths=sorted(known_paths)))

        if state_changed or self._dir_state_dirty or self._empty_dirty:
            self._dir_state_dirty = False
            self._empty_dirty = False
            await asyncio.to_thread(self._save_scan_state)
        if probe_records_dirty():
            await asyncio.to_thread(save_probe_records)
        if self._reel_state_dirty:
            self._reel_state_dirty = False
            await asyncio.to_thread(self._save_reel_state)

    async def _run_scan(self) -> None:
        """Full scan pipeline:

        1. Discover downloads
        2. Categorise → movies / series
        3. Iterate async generators, send each item as Upsert
        4. Queue showreel tasks.
        """
        task_id = f"scan-{uuid.uuid4().hex[:8]}"
        media_root_str = self.media_root.as_posix()
        started_at = time.monotonic()

        try:
            logger.debug("Scan started (%s) for root %s", task_id, self.root_id)
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail="Starting scan...",
                    )
                )
            )

            clear_scan_caches()
            downloads, found_mtimes, known_paths = await self._discover_downloads(
                task_id
            )
            elapsed = time.monotonic() - started_at

            if not downloads:
                logger.info(
                    "Scan (%s): no changes — %d items inspected in %.1fs",
                    task_id,
                    len(known_paths),
                    elapsed,
                )
                await self._finalize_scan(found_mtimes, known_paths)
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="completed",
                            progress=1,
                            detail="No new items",
                        )
                    )
                )
                return

            logger.info(
                "Scan (%s): %d new/changed of %d items found in %.1fs",
                task_id,
                len(downloads),
                len(known_paths),
                elapsed,
            )
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="running",
                        progress=0,
                        detail=f"Processing {len(downloads)} items...",
                    )
                )
            )

            categories = categorize_downloads(downloads)
            n_movies = len(categories[ContentType.MOVIE])
            n_series = len(categories[ContentType.SERIES])
            total = n_movies + n_series
            processed = 0

            logger.info("Categorised: %d movies, %d series", n_movies, n_series)

            # Process movies
            if n_movies:
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=0,
                            detail=f"Processing {n_movies} movies...",
                        )
                    )
                )
            async for (
                movie_id,
                movie,
                showreel_task,
                people,
                scanned,
            ) in _process_movies(
                categories,
                self._output_dir,
                fetch_covers=True,
                generate_showreels=True,
                media_root=media_root_str,
                root_id=self.root_id,
            ):
                await self._send(
                    Upsert(
                        kind="movie",
                        id=movie_id,
                        item=movie,
                        people=people or None,
                        scanned=scanned,
                    )
                )
                if showreel_task:
                    needed, sig = await self._reel_needed(
                        "movie", showreel_task[0], showreel_task[1]
                    )
                    if needed:
                        await self._showreel_queue.put((
                            "movie",
                            movie_id,
                            showreel_task,
                            sig,
                        ))
                        logger.info(
                            "[%d/%d] Movie: %s (showreel queued, queue=%d)",
                            processed + 1,
                            total,
                            movie.title,
                            self._showreel_queue.qsize(),
                        )
                    else:
                        logger.debug(
                            "[%d/%d] Movie: %s (showreel up to date)",
                            processed + 1,
                            total,
                            movie.title,
                        )
                else:
                    logger.debug(
                        "[%d/%d] Movie: %s (no showreel task)",
                        processed + 1,
                        total,
                        movie.title,
                    )
                processed += 1
                progress = round(processed / total, 3) if total else 1
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=progress,
                            detail=movie.title,
                        )
                    )
                )

            # Process series
            if n_series:
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=processed / total if total else 0.5,
                            detail=f"Processing {n_series} series...",
                        )
                    )
                )
            async for (
                series_id,
                series,
                ep_reel_tasks,
                people,
                scanned,
            ) in _process_series(
                categories,
                self._output_dir,
                fetch_covers=True,
                generate_showreels=True,
                media_root=media_root_str,
                root_id=self.root_id,
            ):
                await self._send(
                    Upsert(
                        kind="series",
                        id=series_id,
                        item=series,
                        people=people or None,
                        scanned=scanned,
                    )
                )
                queued = 0
                for task in ep_reel_tasks:
                    needed, sig = await self._reel_needed(
                        "episode", task[0], task[1], task[2], task[3]
                    )
                    if needed:
                        await self._showreel_queue.put((
                            "episode",
                            series_id,
                            task,
                            sig,
                        ))
                        queued += 1
                if queued:
                    logger.info(
                        "[%d/%d] Series: %s (%d episode reels queued, queue=%d)",
                        processed + 1,
                        total,
                        series.title,
                        queued,
                        self._showreel_queue.qsize(),
                    )
                else:
                    logger.debug(
                        "[%d/%d] Series: %s (reels up to date)",
                        processed + 1,
                        total,
                        series.title,
                    )
                processed += 1
                progress = round(processed / total, 3) if total else 1
                await self._send(
                    Task(
                        data=TaskInfo(
                            id=task_id,
                            status="running",
                            progress=progress,
                            detail=series.title,
                        )
                    )
                )

            # Record processed candidates that yielded no index entries (no
            # playable file, no parseable episodes, ...) so later rescans
            # skip them while their mtime is unchanged instead of
            # reprocessing — and re-logging — them on every pass.  The
            # snapshot here predates this scan's upserts, so an item that
            # just produced content may be marked empty once; the marker is
            # dropped again on the next pass when it shows up in the index.
            indexed_snapshot = self._indexed_paths() if self._indexed_paths else set()
            for item in downloads:
                rel = make_relative_path(item.path.as_posix(), media_root_str)
                if rel in indexed_snapshot:
                    if rel in self._empty_mtimes:
                        del self._empty_mtimes[rel]
                        self._empty_dirty = True
                else:
                    m = found_mtimes.get(rel, self._seen_mtimes.get(rel))
                    if m is not None and self._empty_mtimes.get(rel) != m:
                        self._empty_mtimes[rel] = m
                        self._empty_dirty = True

            await self._finalize_scan(found_mtimes, known_paths)
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="completed",
                        progress=1,
                        detail=f"Done — {n_movies} movies, {n_series} series",
                    )
                )
            )
            logger.info(
                "Scan complete (%s): %d movies, %d series updated, "
                "showreel queue=%d, %.1fs total",
                task_id,
                n_movies,
                n_series,
                self._showreel_queue.qsize(),
                time.monotonic() - started_at,
            )

        except asyncio.CancelledError:
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="cancelled",
                        progress=0,
                        detail="Scan cancelled",
                    )
                )
            )
            logger.info("Scan cancelled (%s)", task_id)
        except Exception:
            logger.exception("Scan failed (%s)", task_id)
            await self._send(
                Task(
                    data=TaskInfo(
                        id=task_id,
                        status="error",
                        progress=0,
                        detail="Scan error",
                    )
                )
            )

    # ------------------------------------------------------------------
    # Showreel worker
    # ------------------------------------------------------------------

    async def _showreel_worker(self) -> None:
        """Background worker that generates showreels one at a time."""
        logger.info("Showreel worker started for root %s", self.root_id)
        media_root_path = self.media_root

        while True:
            try:
                kind, item_id, task_data, sig = await self._showreel_queue.get()
                task_id = f"showreel-{uuid.uuid4().hex[:8]}"
                remaining = self._showreel_queue.qsize()

                if kind == "movie":
                    video_path, media_folder, title = task_data
                    logger.info(
                        "Showreel dequeued: %s (video=%s, folder=%s, %d remaining)",
                        title,
                        video_path,
                        media_folder,
                        remaining,
                    )
                    needed, _ = await self._reel_needed(
                        "movie", video_path, media_folder
                    )
                    if not needed:
                        logger.info("Showreel skipped (already exists): %s", title)
                        self._showreel_queue.task_done()
                        continue
                    await self._send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="running",
                                progress=0,
                                detail=f"Showreel: {title}",
                            )
                        )
                    )
                    generated = await generate_showreel_images(
                        video_path,
                        media_folder,
                        title=title,
                    )
                    if generated:
                        source_sets = get_existing_showreel_source_sets(
                            media_folder, media_root=media_root_path
                        )
                        paths = [sources[0] for sources in source_sets if sources]
                        self._record_reel_state(
                            "movie", media_folder, None, None, video_path, sig, "done"
                        )
                        await self._send(
                            MovieShowreel(
                                id=item_id,
                                showreel_images=paths or None,
                                showreel_source_sets=source_sets or None,
                            )
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="completed",
                                    progress=1,
                                    detail=(
                                        f"Showreel: {title} ({len(generated)} reels)"
                                    ),
                                )
                            )
                        )
                    else:
                        self._record_reel_state(
                            "movie",
                            media_folder,
                            None,
                            None,
                            video_path,
                            sig,
                            "failed",
                            error="no reels produced",
                        )
                        logger.warning(
                            "Showreel generation returned nothing: %s", title
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="error",
                                    progress=0,
                                    detail=f"Showreel failed: {title}",
                                )
                            )
                        )

                elif kind == "episode":
                    video_path, media_folder, season_num, episode_num, series_title = (
                        task_data
                    )
                    ep_code = f"S{season_num:02d}E{episode_num:02d}"
                    logger.info(
                        "Episode reel dequeued: %s %s (video=%s, %d remaining)",
                        series_title,
                        ep_code,
                        video_path,
                        remaining,
                    )
                    needed, _ = await self._reel_needed(
                        "episode", video_path, media_folder, season_num, episode_num
                    )
                    if not needed:
                        logger.info(
                            "Episode reel skipped (already exists): %s %s",
                            series_title,
                            ep_code,
                        )
                        self._showreel_queue.task_done()
                        continue
                    await self._send(
                        Task(
                            data=TaskInfo(
                                id=task_id,
                                status="running",
                                progress=0,
                                detail=f"Reel: {series_title} {ep_code}",
                            )
                        )
                    )
                    reel_path = await generate_episode_reel(
                        video_path,
                        media_folder,
                        season_num,
                        episode_num,
                    )
                    if reel_path:
                        reel_sources = get_existing_episode_reel_sources(
                            media_folder,
                            season_num,
                            episode_num,
                            media_root=media_root_path,
                        )
                        reel_image = (
                            reel_sources[0]
                            if reel_sources
                            else make_relative_path(
                                reel_path, media_root_path.as_posix()
                            )
                        )
                        self._record_reel_state(
                            "episode",
                            media_folder,
                            season_num,
                            episode_num,
                            video_path,
                            sig,
                            "done",
                        )
                        await self._send(
                            EpisodeReel(
                                id=item_id,
                                season=season_num,
                                episode=episode_num,
                                reel_image=reel_image,
                                reel_sources=reel_sources or None,
                            )
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="completed",
                                    progress=1,
                                    detail=f"Reel: {series_title} {ep_code}",
                                )
                            )
                        )
                    else:
                        self._record_reel_state(
                            "episode",
                            media_folder,
                            season_num,
                            episode_num,
                            video_path,
                            sig,
                            "failed",
                            error="no reel produced",
                        )
                        logger.warning(
                            "Episode reel generation failed: %s %s",
                            series_title,
                            ep_code,
                        )
                        await self._send(
                            Task(
                                data=TaskInfo(
                                    id=task_id,
                                    status="error",
                                    progress=0,
                                    detail=f"Reel failed: {series_title} {ep_code}",
                                )
                            )
                        )

                self._showreel_queue.task_done()
                if self._showreel_queue.empty() and self._reel_state_dirty:
                    # Persist as soon as the queue drains; scans may be far
                    # apart and a crash would otherwise lose the records.
                    self._reel_state_dirty = False
                    await asyncio.to_thread(self._save_reel_state)

            except asyncio.CancelledError:
                logger.info("Showreel worker shutting down for root %s", self.root_id)
                return
            except Exception:
                logger.exception(
                    "Showreel worker error (queue size=%d)",
                    self._showreel_queue.qsize(),
                )
                with contextlib.suppress(ValueError):
                    self._showreel_queue.task_done()


# ---------------------------------------------------------------------------
# Legacy module-level API removed — use RootScanner per root instead.
# ---------------------------------------------------------------------------
