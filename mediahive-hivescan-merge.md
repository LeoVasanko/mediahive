# Hivescan → FastAPI Server Conversion

> **Status:** Backend complete. Frontend integration and mediahive bridge pending.

## Architecture

Two separate FastAPI servers sharing the same codebase, launched independently:

- **mediahive** (port 8420) — frontend, media serving, play/open-folder actions. *Not yet connected to scanner.*
- **hivescan server** (port 8421) — scanning, TMDb lookups, cover downloads, showreel generation. Owns the in-memory index. Exposes WS for index state + updates.

Each has its own entry point in `pyproject.toml`:

```
hivescan = "hivescan.__main__:main"
mediahive = "mediahive.__main__:main"
```

The `hivescan` CLI accepts scan paths as positional args, with `--host` / `--port` / `-o` options. Configuration is passed to the server via `HIVESCAN_PATHS` and `HIVESCAN_OUTPUT` environment variables.

## In-Memory Index (`hivescan/index_store.py`)

- `IndexStore` class holds two plain dicts (`movies: dict[str, dict]`, `series: dict[str, dict]`), keyed by item `id`. Single source of truth — all mutations are synchronous in the asyncio event loop, no locks needed.
- `index.json` on disk is a **recovery snapshot** only. Written via a debounced background task (`SNAPSHOT_DEBOUNCE = 5.0` seconds). On startup, `load_snapshot()` populates dicts from disk to avoid full rescan; the scan then runs on top to pick up changes.
- Upsert by item `id`. Items from previous runs are preserved (offline drives).
- `flush_snapshot()` forces an immediate write (called on shutdown).
- Snapshot format: version 5, includes `stats`, sorted `movies` and `series` lists, `media_root`, `generated_at` timestamp.

## WebSocket Protocol

The hivescan server exposes `GET /ws`. On connect:

1. Server calls `IndexStore.connect(ws)` — accepts the socket, adds it to `_clients: set[WebSocket]`, sends the full current state:
   ```json
   {"type": "init", "data": {"movies": [...], "series": [...]}}
   ```
2. Live pushes on every mutation:
   ```json
   {"type": "upsert", "kind": "movie", "item": { ... }}
   {"type": "upsert", "kind": "series", "item": { ... }}
   {"type": "remove", "kind": "movie", "id": "abc123"}
   {"type": "task", "data": {"id": "scan-abc12345", "status": "running", "progress": 0.42, "detail": "The Matrix (1999)"}}
   ```
3. Task messages have `status` values: `running`, `completed`, `cancelled`, `error`.
4. Dead connections are cleaned up via `_safe_send()` — failed sends cause the socket to be discarded.

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/ws` | Live index updates & task progress |
| `POST` | `/api/scan` | Trigger a new scan (optional `paths` body override). Returns `{"status": "already_running"}` if a scan is in progress. |
| `GET` | `/api/status` | `{scanning, movies, series, showreel_queue}` |
| `GET` | `/api/index` | Full index as JSON (HTTP fallback for non-WS clients) |

CORS is enabled for all origins.

## Scanning (asyncio, no threads)

- `hivescan/indexer.py`: `_process_movies` and `_process_series` are **async generators**.
  - `_process_movies` yields `Tuple[dict, Optional[Tuple[str, Path, str]]]` — the movie dict and an optional showreel task `(video_path, media_folder, title)`.
  - `_process_series` yields `Tuple[dict, List[Tuple[str, Path, int, int, str]]]` — the series dict and a list of episode reel tasks `(video_path, media_folder, season_num, episode_num, series_title)`.
- TMDb HTTP calls use `httpx.AsyncClient` (lazy-initialised singleton). Disk cache stays synchronous (fast local I/O).
- Cover/backdrop downloads use a separate `httpx.AsyncClient` in `images.py`.
- File scanning (`Path.rglob`, `stat`, `Path.iterdir`): runs synchronously in the event loop (fast enough). Glob expansion for scan paths happens at startup in the lifespan handler.
- Each yielded item is upserted into the in-memory dict (sync) and broadcast to WS clients (non-blocking `asyncio.create_task` per client send).
- `print()` calls throughout replaced with `logging.getLogger()`.

## Showreel Generation

- Background `_showreel_worker` task started at server startup, runs for the lifetime of the server.
- Uses an `asyncio.Queue` — scan tasks push `("movie", task_data, item_id)` or `("episode", task_data, item_id)` tuples.
- Processes one task at a time to avoid saturating CPU/GPU.
- Invokes ffmpeg/ffprobe via `asyncio.create_subprocess_exec` with `asyncio.wait_for` timeouts (replaced all `subprocess.run` calls).
- `generate_showreel_images` accepts an `on_progress` callback (replaced `tqdm` progress bar parameter).
- `generate_episode_reel` no longer takes a progress bar parameter.
- On completion: updates the relevant item in the in-memory dict (`showreel_images` for movies, `reel_image` for episodes) → re-upserts → WS broadcast.
- Skips tasks where showreels/reels already exist on disk.

## File Changes

### Modified

| File | Changes |
|---|---|
| `indexer.py` | `_process_movies` / `_process_series` → async generators yielding `(item_dict, showreel_tasks)`. `_build_seasons_data` → async. Removed `generate_media_index()` and `_run_showreel_generation()` (server orchestrates now). Removed `json`, `datetime`, `tqdm` imports. Added `logging`. `print()` → `logger.info()`. |
| `tmdb_client.py` | `httpx.Client` → `httpx.AsyncClient` (lazy singleton via `_get_http_client()`). All public functions async: `tmdb_api_request`, `fetch_movie_details`, `fetch_series_details`, `fetch_season_details`, `fetch_movie_info`, `fetch_series_info`, `_search_movie_with_fallbacks`, `_search_series_with_fallbacks`. `time.sleep(1)` → `await asyncio.sleep(1)`. Disk cache functions unchanged. |
| `images.py` | `urllib.request` → `httpx.AsyncClient` (lazy singleton via `_get_image_client()`). All functions async: `_download_image`, `download_cover_image`, `download_backdrop_image`, `download_season_poster`. |
| `showreel.py` | `subprocess.run` → `asyncio.create_subprocess_exec` + `asyncio.wait_for`. All ffmpeg/ffprobe functions async: `get_av1_encoder`, `detect_dovi_profile`, `is_hdr_video`, `detect_crop`, `get_video_duration`, `generate_showreel_images`, `generate_episode_reel`. `tqdm` removed. `print()` → `logger.debug/info/warning/error`. Sync helper functions unchanged (`get_expected_showreel_paths`, `get_expected_episode_reel_path`, `movie_showreels_exist`, `episode_reel_exists`, `get_bluray_uri`, `get_encoder_options`, `get_dovi_to_hdr10_filter`). |
| `__main__.py` | Was CLI argparse with `--no-showreels`/`--no-covers` and direct scan execution. Now a server launcher: positional `paths` args, `--host` (default `0.0.0.0`), `--port` (default `8421`), `-o`/`--output-dir`. Sets `HIVESCAN_PATHS`/`HIVESCAN_OUTPUT` env vars and calls `hivescan.server.run()`. |
| `__init__.py` | Replaced `generate_media_index` export with `IndexStore`. Updated module docstring. |
| `pyproject.toml` | Removed `tqdm>=4.67.3` from dependencies. Entry points unchanged. |

### New

| File | Purpose |
|---|---|
| `hivescan/index_store.py` | `IndexStore` class (~200 lines): in-memory dict storage, `upsert_movie/series`, `remove_movie/series`, `load_snapshot`, `flush_snapshot`, debounced `_write_snapshot`, WS client management (`connect`, `disconnect`), `_broadcast` with `_safe_send`, `broadcast_task`, `get_full_index`. |
| `hivescan/server.py` | FastAPI app (~350 lines): lifespan handler (env config, glob expansion, store init, auto-scan), WS endpoint, HTTP endpoints (`/api/scan`, `/api/status`, `/api/index`), `_run_scan` orchestrator (iterates async generators, upserts, queues showreels, broadcasts progress), `_showreel_worker` (queue consumer), `run()` standalone entry. |

### Unchanged

`scanning.py`, `parsing.py`, `models.py`, `utils.py` — all remain synchronous.

## Plan: mediahive Integration

### Data flow

```
hivescan (8421)  ──WS──▶  mediahive (8420)  ──WS──▶  browser
                          keeps its own
                          in-memory dict
```

mediahive keeps its own in-memory copy of the index (`movies: dict`, `series: dict`), populated from one of two sources:

1. **hivescan WS** (preferred) — on startup, mediahive connects to `ws://localhost:8421/ws`. The `init` message seeds the dicts, then `upsert`/`remove` messages keep them in sync. Task messages are forwarded to browser clients as-is.
2. **Disk fallback** — if hivescan is not running (connection refused / WS drops and doesn't reconnect), load `index.json` from disk once as a static snapshot. The frontend still works, just without live updates.

Auto-reconnect: if the WS drops, mediahive retries on a backoff. When it reconnects, it gets a fresh `init` and replaces its dicts entirely.

### `mediahive/server.py` changes

- **New WS client task** started in `lifespan`. Connects to hivescan, handles messages, updates in-memory dicts. On failure, loads `index.json` fallback.
- **New WS endpoint** `GET /ws` for browser clients. On connect: send `{"type": "init", "data": {movies, series}}` from the local dicts. Forward `upsert`, `remove`, `task` messages as they arrive from hivescan.
- **Existing `GET /api/index`** — return from in-memory dicts instead of reading `index.json` each time. Keeps working for non-WS clients. Remove the `linux_to_windows_path` conversion (paths are already relative).
- Keep `play`, `open-folder`, `media/{path}` endpoints unchanged.

### Frontend changes

| File | Change |
|---|---|
| `composables/useWebSocket.ts` (new) | Connect to mediahive's `/ws` (same origin, no CORS). On `init`: replace reactive index. On `upsert`: merge item by id. On `remove`: delete by id. On `task`: update reactive task state. Auto-reconnect with backoff. |
| `types.ts` | Add `WsMessage`, `TaskInfo` types. |
| `App.vue` | Replace `loadMediaIndex()` fetch with the WS composable. Items appear incrementally as scan runs. Fall back to `GET /api/index` if WS never connects. |
| `Header.vue` | Show task indicator (spinner + progress text) when scan/showreel tasks are active. |
| `api.ts` | Remove `loadMediaIndex()`. Keep `playMedia()`, `openFolder()`, `getCoverUrl()`. |
