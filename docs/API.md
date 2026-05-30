# API

MediaHive exposes a small local API used by the desktop app and frontend.
All media paths are scoped to a **root**, identified by a friendly `root_id`
(same identifier shown as the root name).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight health check. |
| `GET` | `/api/config` | Returns the current root configuration. |
| `GET` | `/api/roots` | List all active roots with status. |
| `PUT` | `/api/roots` | Atomically replace the full root set. |
| `POST` | `/api/play/{root_id}` | Opens a media file with the system player. |
| `POST` | `/api/open-folder/{root_id}` | Opens a folder in the system file explorer. |
| `GET` | `/api/meta/{root_id}/{meta_key}` | Returns allowed metadata from `<root>/.mediahive`. |
| `GET` | `/api/player/status` | Returns whether remote player control is currently available. |
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable. |
| `GET` | `/api/media/{root_id}/{file_path:path}` | Serves files from the specified root. |
| `GET` | `/api/assets/{root_id}/{asset_type}/{asset_path:path}` | Serves typed assets from `<root>/.mediahive`. |
| `WS` | `/api/ws/{root_id}` | Streams live index updates and task progress for one root. |

## Notes

- `PUT /api/roots` accepts `{ "roots": { "name": "/absolute/path", ... } }`, validates paths, and atomically swaps the active set.
- `POST /api/play/{root_id}` and `POST /api/open-folder/{root_id}` expect JSON request bodies with `file_path` / `folder_path` relative to the root.
- `GET /api/media/{root_id}/{file_path:path}` is constrained to the specified root; path traversal outside the root is rejected.
- `GET /api/assets/{root_id}/{asset_type}/{asset_path:path}` is constrained to `<root>/.mediahive/{asset_type}` where `asset_type` is one of `movies`, `series`, `people`.
- `GET /api/meta/{root_id}/{meta_key}` supports metadata keys currently limited to `playback-state` and `scanignore`.
- `GET /api/player/status` returns `{ "remote": true|false }`.
- `GET /api/mpcbe/status` returns `false` on non-Windows platforms.
