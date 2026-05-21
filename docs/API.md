# API

MediaHive exposes a small local API used by the desktop app and frontend.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight health check. |
| `GET` | `/api/config` | Returns the currently selected media folder. |
| `POST` | `/api/change-folder` | Persists and switches the active media folder without restarting the app. |
| `GET` | `/api/index` | Returns the current in-memory media index. |
| `GET` | `/api/playback/resume-positions` | Returns saved resume positions by media path. |
| `GET` | `/api/status` | Returns scanner and library status information. |
| `POST` | `/api/scan` | Triggers a new scan if the scanner is active. |
| `POST` | `/api/play` | Opens a media file with the system player. |
| `POST` | `/api/open-folder` | Opens a folder in the system file explorer, or selects a file in its parent folder. |
| `GET` | `/api/player/status` | Returns whether remote player control is currently available. |
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable. |
| `GET` | `/api/media/{file_path:path}` | Serves files from the active media root. |
| `WS` | `/api/ws` | Streams live index updates and task progress events. |

## Notes

- `POST /api/change-folder` validates the new folder, saves it to config, and switches the in-memory scanner asynchronously.
- `POST /api/play` and `POST /api/open-folder` expect JSON request bodies matching the frontend calls.
- `GET /api/media/{file_path:path}` is constrained to the current media root.
- `GET /api/player/status` returns `{ "remote": true|false }`.
- `GET /api/mpcbe/status` returns `false` on non-Windows platforms.
