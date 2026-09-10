# Development

This document covers the developer-facing ways to run MediaHive locally. The main [README.md](../README.md) covers end-user startup across platforms (portable ZIPs on Windows/macOS, `uvx --from mediahive[gui] mediahive` on Linux/other).

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

## Building And Releasing

The helper scripts are directly executable via their `uv run` shebang (on Windows, run them with `uv run scripts/<name>.py`):

- `./scripts/guibuild.py` builds the PyInstaller desktop app and a versioned portable ZIP under `build/`.
- `./scripts/release.py` publishes a release to the Gitea releases page.

Python packaging builds the frontend automatically through the hatch build hook `scripts/fastapi-vue/buildhook.py` (see `pyproject.toml`), so wheels and sdists always ship a fresh `mediahive/frontend-build`.

## Notes

- The selected media folder is scanned continuously by the backend.
- The desktop app remembers the chosen folder between launches.
- HTTP and WebSocket endpoints are documented in [API.md](API.md).
- MPC-BE integration details (Windows only) live in [mpc-be.md](mpc-be.md).
- Scanner/indexer design notes and the v0.5.0 rescan fixes are reviewed in [scanning-review.md](scanning-review.md).
