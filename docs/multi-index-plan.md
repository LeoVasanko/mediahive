# Multi-Root Implementation Notes

## Overview

MediaHive now supports multiple independent media roots. Each root is a filesystem directory with its own index, scanner, and WebSocket stream. The frontend merges per-root state into a single reactive view.

## Architecture

### Root Identity

- **Root ID**: friendly root name derived from configured path basename.
- **Name/ID collision handling**: suffixes `2`, `3`, … are appended to keep each root ID unique.
- **Path normalization**: lower-case Windows drive letter, strip trailing slashes, forward slashes only (`as_posix()`).

### Per-Root Runtime (`RootContext`)

Each active root gets an isolated `RootContext` managed by the `Supervisor`:

- `root_id`, `root_path` — stable identifiers
- `IndexStore` — owns snapshot at `<root>/.mediahive/index.json`
- `RootScanner` — per-root scanning instance (replaced legacy global scanner)
- `asyncio.Queue` + consumer task — bridges scanner events to WebSocket
- `status`: `idle` | `loading` | `ready` | `scanning` | `error`

### Supervisor

- Holds `dict[str, RootContext]` keyed by `root_id`.
- `replace_roots(new_roots)` atomically swaps the active set:
  1. Validate & canonicalize paths.
  2. Derive unique friendly `root_id` for each.
  3. Prepare new `RootContext`s (load snapshots).
  4. Swap dict atomically.
  5. Stop removed contexts in background with bounded timeout.
- Exposes merged read helpers (`merged_index`, `all_statuses`).

### Item IDs

`root_id` is stored separately on each item.

- `Movie.id` uses a slug built from the movie title and year, for example `spider-man-no-way-home-2021`.
- `Series.id` uses a slug built from the series title, for example `lost`.
- Legacy snapshot migrations are handled by `scripts/indexmigr.py`, not during app startup.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/roots` | List all roots (name, path, root_id, status) |
| `PUT /api/roots` | Atomically replace full root map `{name: path}` |
| `WS /api/ws/{root_id}` | Per-root WebSocket (init/upsert/remove/task + status/task events) |
| `GET /api/media/{root_id}/{path:path}` | Serve media file scoped to root |
| `GET /api/assets/{root_id}/{asset_type}/{asset_path:path}` | Serve `.mediahive/{asset_type}` assets (`movies`, `series`, `people`) |
| `POST /api/play/{root_id}` | Play file within root |
| `POST /api/open-folder/{root_id}` | Open folder within root |
| `GET /api/meta/{root_id}/{meta_key}` | Per-root metadata (for example `playback-state`) |
| `POST /api/ui/pick-folder` | Native OS folder picker (returns path) |

> **Removed legacy endpoints**: `/api/change-folder`, `/api/index`, `/api/scan`, `/api/status`, `/api/playback/resume-positions`. No backwards compatibility is maintained.

## macOS Startup Safety

The server **must not** touch the filesystem during startup, because macOS may show permission dialogs that block the event loop and prevent the HTTP server from accepting requests.

- `lifespan()` creates a background task (`_activate_all_roots()`) and immediately yields.
- All filesystem validation (`exists()`, `is_dir()`, `resolve()`) runs in a thread pool via `asyncio.to_thread()`.
- CLI entry points (`__main__.py`, `winmain.py`, `hivescan/__main__.py`) pass raw paths via the `MEDIAHIVE_ROOTS` environment variable; they do **not** validate paths before starting the server.

## POSIX Path Enforcement

All stored and transmitted paths use forward slashes exclusively:

- `_normalize_path()` always returns POSIX paths.
- Config stores `p.as_posix()`.
- URLs use `/` separators.
- `Path(root_path) / relative_path` works correctly on Windows because `Path` accepts POSIX separators.

## Config Migration

- Old `media_folder` string is auto-migrated to `roots: {basename: path}` on load.
- `roots` is persisted back to TOML config.

## Scanner

- Legacy global module-level scanner API was removed from `hivescan/scanner.py`.
- `RootScanner` is the only scanning interface.
- Each `RootScanner` owns its own `showreel_queue`, `scan_task`, `rescan_worker_task`, and `_seen_mtimes`.

## Frontend

- `useMediaWebSocket.ts` manages one WebSocket per active root.
- `App.vue` merges per-root `movieMap`/`seriesMap` into a single `mediaIndex`.
- `Header.vue` provides add/remove root UI via `PUT /api/roots`.
- Playback URLs are root-qualified (`/api/media/{root_id}/...`).
- Metadata cache assets use typed root paths (`/api/assets/{root_id}/{asset_type}/...`) rather than exposing `.mediahive` in URLs.
