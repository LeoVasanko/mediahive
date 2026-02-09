"""
FastAPI server for MediaHive.
Replaces Tauri backend with async HTTP server.
"""

import json
import mimetypes
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
import msgspec
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi_vue import Frontend

from mediahive.models.protocol import PlayMediaRequest, OpenFolderRequest

from mediahive.__main__ import DEVMODE

# Vue Frontend static files
frontend = Frontend(Path(__file__).with_name("frontend-build"), cached=["/assets/"])


# Media root path (initialized in lifespan)
MEDIAROOT = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MEDIAROOT
    if not os.environ.get("MEDIAHIVE_PATH"):
        raise RuntimeError("MEDIAHIVE_PATH environment variable must be set")
    MEDIAROOT = Path(os.environ["MEDIAHIVE_PATH"])
    await frontend.load()
    yield


app = FastAPI(title="MediaHive Server", lifespan=lifespan, debug=DEVMODE)

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def linux_to_windows_path(path: str) -> str:
    """Normalize media path (now relative paths are kept as is)."""
    return path


def normalize_path(url_path: str) -> Path:
    """
    Convert URL path to filesystem path.
    URL: /media/.mediahive/Movies/...
    Returns: MEDIAROOT/.mediahive/Movies/...
    """
    clean_path = url_path.lstrip("/")
    return MEDIAROOT / clean_path


# === API Endpoints ===


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/index")
async def load_media_index():
    """
    Load and return the media index from disk.
    Converts Linux paths to Windows paths.
    """
    index_path = MEDIAROOT / ".mediahive" / "index.json"
    if not index_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Index file not found: {index_path}"
        )

    try:
        async with aiofiles.open(index_path, "r", encoding="utf-8") as f:
            content = await f.read()

        index = json.loads(content)

        # Convert all Linux paths to Windows paths
        for movie in index.get("movies", []):
            if movie.get("cover_path"):
                movie["cover_path"] = linux_to_windows_path(movie["cover_path"])
            if movie.get("backdrop_path"):
                movie["backdrop_path"] = linux_to_windows_path(movie["backdrop_path"])
            if movie.get("showreel_images"):
                movie["showreel_images"] = [
                    linux_to_windows_path(p) for p in movie["showreel_images"]
                ]
            for version in movie.get("versions", []):
                version["path"] = linux_to_windows_path(version["path"])
                if version.get("playable_file"):
                    version["playable_file"] = linux_to_windows_path(
                        version["playable_file"]
                    )
                if version.get("torrent_path"):
                    version["torrent_path"] = linux_to_windows_path(
                        version["torrent_path"]
                    )

        for series in index.get("series", []):
            if series.get("cover_path"):
                series["cover_path"] = linux_to_windows_path(series["cover_path"])
            if series.get("backdrop_path"):
                series["backdrop_path"] = linux_to_windows_path(series["backdrop_path"])
            for season in series.get("seasons", []):
                if season.get("poster_path"):
                    season["poster_path"] = linux_to_windows_path(season["poster_path"])
                for episode in season.get("episodes", []):
                    if episode.get("reel_image"):
                        episode["reel_image"] = linux_to_windows_path(
                            episode["reel_image"]
                        )
                    for release in episode.get("releases", []):
                        release["path"] = linux_to_windows_path(release["path"])
                        if release.get("playable_file"):
                            release["playable_file"] = linux_to_windows_path(
                                release["playable_file"]
                            )

        return index

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse index file: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read index file: {e}")


@app.post("/api/play")
async def play_media(request: Request):
    """
    Open a media file with the system's default player.
    """
    req = msgspec.json.decode(await request.body(), type=PlayMediaRequest)
    print(f"[play] Received path: {req.file_path}")
    file_path = MEDIAROOT / req.file_path

    if not file_path.exists():
        print(f"[play] File not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    try:
        # Use os.startfile on Windows (non-blocking)
        if sys.platform == "win32":
            os.startfile(str(file_path))
        else:
            # For other platforms, use xdg-open or open
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(file_path)])

        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to play media: {e}")


@app.post("/api/open-folder")
async def open_folder(request: Request):
    """
    Open a folder in the system file explorer.
    If the path is a file, opens the parent folder and selects the file.
    """
    req = msgspec.json.decode(await request.body(), type=OpenFolderRequest)
    print(f"[open-folder] Received path: {req.folder_path}")
    target_path = MEDIAROOT / req.folder_path

    if not target_path.exists():
        print(f"[open-folder] Path not found: {target_path}")
        raise HTTPException(
            status_code=404, detail=f"Path not found: {req.folder_path}"
        )

    try:
        if sys.platform == "win32":
            if target_path.is_file():
                # Open parent folder and select the file
                subprocess.Popen(["explorer", "/select,", str(target_path)])
            else:
                # Open the folder directly
                subprocess.Popen(["explorer", str(target_path)])
        elif sys.platform == "darwin":
            if target_path.is_file():
                subprocess.Popen(["open", "-R", str(target_path)])
            else:
                subprocess.Popen(["open", str(target_path)])
        else:
            # Linux - just open the folder (no standard way to select)
            folder = target_path.parent if target_path.is_file() else target_path
            subprocess.Popen(["xdg-open", str(folder)])

        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


@app.get("/api/media/{file_path:path}")
async def serve_media_file(file_path: str):
    """
    Serve a media file asynchronously.
    """
    full_path = normalize_path(file_path)

    # Security: ensure path doesn't escape base
    try:
        full_path.resolve().relative_to(MEDIAROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    # Guess content type
    content_type, _ = mimetypes.guess_type(str(full_path))
    if content_type is None:
        content_type = "application/octet-stream"

    # For images, use FileResponse which handles caching headers
    if content_type.startswith("image/"):
        return FileResponse(
            full_path,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )

    # For larger files, stream them
    async def stream_file():
        async with aiofiles.open(full_path, "rb") as f:
            while chunk := await f.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        stream_file(),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )


# Serve the Vue frontend (needs to be last if SPA catch-all is used)
frontend.route(app, "/")
