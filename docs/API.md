# API

MediaHive exposes a small local API used by the desktop app and frontend.
All media paths are scoped to a **root**, identified by a friendly `root_id`
(same identifier shown as the root name).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight health check. |
| `GET` | `/api/config` | Returns the current root configuration. |
| `PUT` | `/api/config/roots` | Atomically replace the full root set. |
| `POST` | `/api/play/{root_id}` | Opens a media file with the system player. Also starts an assumed-playback session (see notes). |
| `POST` | `/api/activity` | Reports user input activity; finalizes any assumed-playback session. Returns `{ "status": "ok", "finalized": bool }`. |
| `POST` | `/api/open-folder/{root_id}` | Opens a folder in the system file explorer. |
| `GET` | `/api/meta/{root_id}/{meta_key}` | Returns allowed metadata from `<root>/.mediahive`. |
| `GET` | `/api/meta/playback-state` | Returns merged resume positions across all roots. Series entries carry one continue point per series (`season`/`episode` = last watched) plus a per-episode watch map (`episodes`: `"S<season>E<episode>"` → `{pos, ts, done}`); completing an episode marks it done and advances the point to the next episode. |
| `POST` | `/api/meta/playback-state` | Updates one resume entry (`root_id`, `file_path`, `pos`; null `pos` clears a movie or advances a series' continue point). |
| `GET` | `/api/player/status` | Returns whether remote player control is currently available. |
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable. |
| `GET` | `/api/media/{root_id}/{file_path:path}` | Serves files from the specified root. |
| `GET` | `/api/assets/{root_id}/{asset_type}/{asset_path:path}` | Serves typed assets from `<root>/.mediahive`. |
| `WS` | `/api/ws` | Streams roots, index updates, and task progress for all roots. |

## Notes

- `PUT /api/config/roots` accepts `{ "roots": { "name": "/absolute/path", ... } }`, validates paths, and atomically swaps the active set.
- `POST /api/play/{root_id}` and `POST /api/open-folder/{root_id}` expect JSON request bodies with `file_path` / `folder_path` relative to the root.
- `GET /api/media/{root_id}/{file_path:path}` is constrained to the specified root; path traversal outside the root is rejected.
- `GET /api/assets/{root_id}/{asset_type}/{asset_path:path}` is constrained to `<root>/.mediahive/{asset_type}` where `asset_type` is one of `movies`, `series`, `people`.
- `GET /api/meta/{root_id}/{meta_key}` supports metadata keys currently limited to `playback-state` and `scanignore`.
- `GET /api/player/status` returns `{ "remote": true|false }`.
- `GET /api/mpcbe/status` returns `false` on non-Windows platforms.
- Assumed playback: after `POST /api/play/{root_id}` the launched item is assumed to be playing while the frontend reports no input activity. On the next `POST /api/activity` the guessed position (`resume base + elapsed`, capped at the TMDb runtime) is written once; watches under 5 minutes are discarded (a peek is not progress). A resume entry written by another tracker (e.g. the GUI's MPC-BE tracker) during the session overrides the guess. The MPC-BE tracker likewise ignores sessions shorter than 5 minutes.
