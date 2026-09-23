# API

MediaHive exposes a small local API used by the desktop app and frontend.
All media paths are scoped to a **root**, identified by a friendly `root_id`
(same identifier shown as the root name).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight health check. |
| `GET` | `/api/config` | Returns the current root configuration. |
| `PUT` | `/api/config/roots` | Atomically replace the full root set. Returns `{ "status": "ok", "accepted": [{path, root_id}], "failed": [...] }`. |
| `POST` | `/api/play/{root_id}` | Opens a media file with a media player. Also starts an assumed-playback session (see notes). |
| `GET` | `/api/players` | Lists detected media players. Returns `{ "players": [{id, name, family, path}] }`, including synthetic `default` and `custom` entries. |
| `POST` | `/api/activity` | Reports user input activity; finalizes any assumed-playback session. Returns `{ "status": "ok", "finalized": bool }`. |
| `POST` | `/api/open-folder/{root_id}` | Opens a folder in the system file explorer; given a file path, selects the file instead. |
| `GET` | `/api/meta/{root_id}/{meta_key}` | Returns allowed metadata from `<root>/.mediahive`, as `{ "key": meta_key, "data": ... }`. |
| `GET` | `/api/meta/playback-state` | Returns merged resume positions across all roots, as `{ "key": "playback-state", "data": ... }`. Series entries carry one continue point per series (`season`/`episode` = last watched) plus a per-episode watch map (`episodes`: `"S<season>E<episode>"` → `{pos, ts, done}`); completing an episode marks it done and advances the point to the next episode. |
| `POST` | `/api/meta/playback-state` | Updates one resume entry (`root_id`, `file_path`, `pos`; null `pos` clears a movie or advances a series' continue point). Returns `{ "status": "ok", "slug", "pos" }` plus `season`/`episode` when the continue point advances. |
| `GET` | `/api/player/status` | Returns whether remote player control is currently available. Accepts an optional `?port=` override (default 13579). |
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable, as `{ "reachable": true|false }`. Accepts an optional `?port=` override; always `false` on non-Windows. |
| `GET` | `/api/media/{root_id}/{file_path:path}` | Serves files from the specified root. |
| `GET` | `/api/assets/{root_id}/{asset_type}/{asset_path:path}` | Serves typed assets from `<root>/.mediahive`. |
| `WS` | `/api/ws` | Streams roots, index updates, and task progress for all roots (see WebSocket notes). |

## Notes

- `PUT /api/config/roots` accepts `{ "roots": { "name": "/absolute/path", ... } }`, validates paths, and atomically swaps the active set.
- `POST /api/play/{root_id}` and `POST /api/open-folder/{root_id}` expect JSON request bodies with `file_path` / `folder_path` relative to the root. The play body additionally accepts `player_id` (a value from `GET /api/players`; unknown ids yield 400) and `player_custom_cmd` (command template used when `player_id` is `custom`).
- `GET /api/media/{root_id}/{file_path:path}` is constrained to the specified root; path traversal outside the root is rejected. Single-range requests are supported (`206` with `Content-Range`, `416` on invalid ranges), responses carry a weak `ETag` (`If-None-Match` yields `304`) and `Cache-Control: public, max-age=604800, immutable`.
- `GET /api/assets/{root_id}/{asset_type}/{asset_path:path}` is constrained to `<root>/.mediahive/{asset_type}` where `asset_type` is one of `movies`, `series`, `people`.
- `GET /api/meta/{root_id}/{meta_key}` supports metadata keys currently limited to `playback-state` and `scanignore`.
- `GET /api/player/status` returns `{ "remote": true|false }`.
- Roots may also be provided at startup via CLI arguments (`mediahive /path/to/media ...`), which are passed to the server through fastapi-vue's env config (`mediahive.config.config`) and override the persisted configuration.
- Assumed playback: after `POST /api/play/{root_id}` the launched item is assumed to be playing while the frontend reports no input activity. On the next `POST /api/activity` the guessed position (`resume base + elapsed`, capped at the TMDb runtime) is written once; watches under 5 minutes are discarded (a peek is not progress). A resume entry written by another tracker (e.g. the GUI's MPC-BE tracker) during the session overrides the guess. The MPC-BE tracker likewise ignores sessions shorter than 5 minutes.

## WebSocket

`GET /api/ws` sends tagged msgspec JSON messages as binary frames (message shapes are defined in `mediahive/models/protocol.py`):

- `roots` — full root list and per-root status: `{roots: [{root_id, path, status, error, snapshot_loaded, movies, series}]}`.
- `init` — full index payload `{roots: {root_id: {movies, series, people}}}`, re-sent when the root set changes or a snapshot finishes loading.
- `upsert` — single item inserted or updated: `{root_id, kind ("movie"|"series"), id, item, people?}`.
- `remove` — single item removed: `{root_id, kind, id}`.
- `task` — background task progress: `{root_id, data}`.

Clients must send (any) text frame to keep the receive loop alive.
