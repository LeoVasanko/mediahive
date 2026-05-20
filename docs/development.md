# Development

This document covers the developer-facing ways to run MediaHive locally. The main [README.md](../README.md) is aimed at Windows end users.

## Requirements

- Python 3.14+
- `uv`
- Node.js 18+

## Install Dependencies

```bash
uv sync --extra gui --group dev
cd frontend
npm install
```

## Run The Backend Directly

```bash
uv run mediahive /path/to/media/folder
```

This starts the FastAPI backend and serves the built frontend.

## Run Frontend + Backend In Development

```bash
uv run scripts/devserver.py /path/to/media/folder
```

This starts the FastAPI backend with auto-reload plus the Vite frontend dev server.

## Run The Desktop App In Development

```bash
uv run --extra gui python -m mediahive.winmain /path/to/media/folder
```

This launches the same pywebview-based desktop flow used by the Windows build.

## Notes

- The selected media folder is scanned continuously by the backend.
- The Windows desktop app remembers the chosen folder between launches.
- HTTP and WebSocket endpoints are documented in [API.md](API.md).
- MPC-BE integration details live in [mpc-be.md](mpc-be.md).
*** Add File: c:\mediahive\docs\API.md
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
| `GET` | `/api/mpcbe/status` | Reports whether MPC-BE's local web interface is reachable. |
| `GET` | `/api/media/{file_path:path}` | Serves files from the active media root. |
| `WS` | `/api/ws` | Streams live index updates and task progress events. |

## Notes

- `POST /api/change-folder` validates the new folder, saves it to config, and switches the in-memory scanner asynchronously.
- `POST /api/play` and `POST /api/open-folder` expect JSON request bodies matching the frontend calls.
- `GET /api/media/{file_path:path}` is constrained to the current media root.
- `GET /api/mpcbe/status` only checks the local MPC-BE web interface.
