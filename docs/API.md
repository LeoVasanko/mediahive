# API

MediaHive exposes a small local API used by the desktop app and frontend.
All media paths are scoped to a **root**, identified by a stable `root_id`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight health check. |
| `GET` | `/api/config` | Returns the current root configuration. |
| `GET` | `/api/roots` | List all active roots with status. |
| `PUT` | `/api/roots` | Atomically replace the full root set. |
| `GET` | `/api/roots/{root_id}/status` | Returns scanner and library status for one root. |
| `POST` | `/api/roots/{root_id}/scan` | Triggers a new scan for one root. |
| `POST` | `/api/roots/{root_id}/play` | Opens a media file with the system player. |
| `POST` | `/api/roots/{root_id}/open-folder` | Opens a folder in the system file explorer. |
| `GET` | `/api/roots/{root_id}/playback/resume-positions` | Returns saved resume positions for one root. |
| `GET` | `/api/player/status` | Returns whether remote player control is currently available. |
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable. |
| `GET` | `/api/media/{root_id}/{file_path:path}` | Serves files from the specified root. |
| `WS` | `/api/roots/{root_id}/ws` | Streams live index updates and task progress for one root. |

## Notes

- `PUT /api/roots` accepts `{ "roots": { "name": "/absolute/path", ... } }`, validates paths, and atomically swaps the active set.
- `POST /api/roots/{root_id}/play` and `POST /api/roots/{root_id}/open-folder` expect JSON request bodies with `file_path` / `folder_path` relative to the root.
- `GET /api/media/{root_id}/{file_path:path}` is constrained to the specified root; path traversal outside the root is rejected.
- `GET /api/player/status` returns `{ "remote": true|false }`.
- `GET /api/mpcbe/status` returns `false` on non-Windows platforms.
