"""Build the Windows GUI application and package it as a version-numbered ZIP.

Usage:
    uv run scripts/build_windows_gui.py

This runs in the project environment where dependencies are available via pyproject.toml.

This script:
    1. Reads the version from pyproject.toml
    2. Runs `uv build` to produce the wheel/sdist
    3. Downloads the latest ffmpeg.exe
    4. Builds MediaHive.exe using PyInstaller
    5. Creates a ZIP file with the version number
"""

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import setuptools_scm

# BtbN automated builds always publish a 'latest' tag with this asset.
_FFMPEG_URL = (
    "https://github.com/BtbN/ffmpeg-builds/releases/download/latest"
    "/ffmpeg-master-latest-win64-gpl.zip"
)
_FFMPEG_STAGING = Path(__file__).parent.parent / "build" / "ffmpeg"


def fetch_ffmpeg() -> Path:
    """Download latest ffmpeg.exe from BtbN builds into build/ffmpeg/."""
    dest = _FFMPEG_STAGING / "ffmpeg.exe"
    if dest.exists():
        print(f"ffmpeg already staged at {dest}, skipping download.")
        return dest

    _FFMPEG_STAGING.mkdir(parents=True, exist_ok=True)
    print(f"Downloading ffmpeg from {_FFMPEG_URL} ...")
    with urllib.request.urlopen(_FFMPEG_URL) as resp:
        data = resp.read()

    print("Extracting ffmpeg.exe ...")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # The zip contains a top-level folder; ffmpeg.exe is under .../bin/
        ffmpeg_entry = next(
            name for name in zf.namelist()
            if name.endswith("/bin/ffmpeg.exe")
        )
        with zf.open(ffmpeg_entry) as src, open(dest, "wb") as out:
            out.write(src.read())

    print(f"ffmpeg staged at {dest} ({dest.stat().st_size // 1024 // 1024} MB)")
    return dest


def read_version() -> str:
    """Read version via setuptools_scm (same logic as hatch-vcs)."""
    repo_root = Path(__file__).parent.parent
    return setuptools_scm.get_version(root=str(repo_root))


def build_wheel() -> None:
    """Run uv build to produce the wheel and sdist."""
    repo_root = Path(__file__).parent.parent
    cmd = ["uv", "build"]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"uv build failed with exit code {result.returncode}")


def build_exe() -> None:
    """Run PyInstaller to build the executable."""
    repo_root = Path(__file__).parent.parent
    spec_file = Path(__file__).parent / "MediaHive.spec"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(repo_root / "build"),
        "--workpath", str(repo_root / "build" / ".pyinstaller-work"),
        str(spec_file),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with exit code {result.returncode}")


def create_zip(version: str) -> Path:
    """Create a version-numbered ZIP file of the dist/MediaHive folder."""
    repo_root = Path(__file__).parent.parent
    dist_folder = repo_root / "build" / "MediaHive"

    if not dist_folder.exists():
        raise FileNotFoundError(f"Distribution folder not found: {dist_folder}")

    zip_name = f"MediaHive-{version}-win64.zip"
    zip_path = repo_root / "build" / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating {zip_path}...")
    shutil.make_archive(
        str(zip_path.with_suffix("")),  # removes .zip so make_archive can add it
        "zip",
        root_dir=str(dist_folder),  # zip contents of MediaHive/, not the folder itself
    )
    return zip_path


def main() -> None:
    try:
        version = read_version()
        print(f"MediaHive version: {version}")

        fetch_ffmpeg()
        build_wheel()
        build_exe()
        zip_path = create_zip(version)

        print(f"✓ Built successfully: {zip_path}")
        print(f"  Size: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")
    except Exception as e:
        print(f"✗ Build failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
