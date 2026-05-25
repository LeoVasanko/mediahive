"""Build the desktop GUI application and package it as a version-numbered ZIP.

Usage:
    uv run scripts/winbuild.py

This runs in the project environment where dependencies are available via pyproject.toml.

This script:
    1. Reads the version from pyproject.toml
    2. Runs `uv build` to produce the wheel/sdist
    3. On Windows, downloads the latest ffmpeg.exe for bundling
    4. On macOS arm64, downloads a prebuilt ffmpeg binary for bundling
    4. Builds MediaHive using PyInstaller
    5. Creates a ZIP file with the version number
"""

import io
import platform
import shutil
import stat
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
_MACOS_ARM64_TOOL_URLS = {
    "ffmpeg": "https://www.osxexperts.net/ffmpeg81arm.zip",
}
_FFMPEG_STAGING = Path(__file__).parent.parent / "build" / "ffmpeg"
_REPO_ROOT = Path(__file__).parent.parent
_ASSETS_DIR = _REPO_ROOT / "mediahive" / "assets"


def _platform_zip_suffix() -> str:
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine or "unknown")

    if sys.platform == "win32":
        return "win64"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"linux-{arch}"


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
            name for name in zf.namelist() if name.endswith("/bin/ffmpeg.exe")
        )
        with zf.open(ffmpeg_entry) as src, Path(dest).open("wb") as out:
            out.write(src.read())

    print(f"ffmpeg staged at {dest} ({dest.stat().st_size // 1024 // 1024} MB)")
    return dest


def fetch_macos_arm64_binaries() -> dict[str, Path]:
    """Download prebuilt macOS arm64 ffmpeg binary into build/ffmpeg/."""
    if sys.platform != "darwin" or platform.machine().lower() not in {
        "arm64",
        "aarch64",
    }:
        raise RuntimeError("macOS bundling is only supported for arm64 builds")

    _FFMPEG_STAGING.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}

    for tool_name, url in _MACOS_ARM64_TOOL_URLS.items():
        dest = _FFMPEG_STAGING / tool_name
        if dest.exists():
            print(f"{tool_name} already staged at {dest}, skipping download.")
            staged[tool_name] = dest
            continue

        print(f"Downloading {tool_name} from {url} ...")
        with urllib.request.urlopen(url) as resp:
            data = resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entry_name = next(
                name
                for name in zf.namelist()
                if Path(name).name == tool_name and not name.endswith("/")
            )
            with zf.open(entry_name) as src, Path(dest).open("wb") as out:
                out.write(src.read())

        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Make the bundled binary runnable when copied out of the zip/app on macOS.
        subprocess.run(["xattr", "-cr", str(dest)], check=False)
        subprocess.run(["codesign", "-f", "-s", "-", str(dest)], check=True)

        staged[tool_name] = dest
        print(
            f"{tool_name} staged at {dest} ({dest.stat().st_size // 1024 // 1024} MB)"
        )

    return staged


def ensure_macos_icon() -> Path:
    """Create mediahive.icns from mediahive.ico when building on macOS."""
    icon_icns = _ASSETS_DIR / "mediahive.icns"
    if icon_icns.exists():
        return icon_icns

    icon_ico = _ASSETS_DIR / "mediahive.ico"
    if not icon_ico.exists():
        raise FileNotFoundError(f"Missing source icon: {icon_ico}")

    iconset_dir = _REPO_ROOT / "build" / "mediahive.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)
    base_png = _REPO_ROOT / "build" / "mediahive-icon-1024.png"

    subprocess.run(
        ["sips", "-s", "format", "png", str(icon_ico), "--out", str(base_png)],
        check=True,
    )

    size_entries = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for pixels, name in size_entries:
        subprocess.run(
            [
                "sips",
                "-z",
                str(pixels),
                str(pixels),
                str(base_png),
                "--out",
                str(iconset_dir / name),
            ],
            check=True,
        )

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icon_icns)],
        check=True,
    )
    print(f"macOS app icon generated: {icon_icns}")
    return icon_icns


def read_version() -> str:
    """Read version via setuptools_scm (same logic as hatch-vcs)."""
    return setuptools_scm.get_version(root=str(_REPO_ROOT))


def build_wheel() -> None:
    """Run uv build to produce the wheel and sdist."""
    repo_root = _REPO_ROOT
    cmd = ["uv", "build"]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"uv build failed with exit code {result.returncode}")


def build_executable() -> None:
    """Run PyInstaller to build the desktop GUI app."""
    repo_root = _REPO_ROOT
    spec_file = Path(__file__).parent / "MediaHive.spec"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(repo_root / "build"),
        "--workpath",
        str(repo_root / "build" / ".pyinstaller-work"),
        str(spec_file),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with exit code {result.returncode}")


def create_zip(version: str) -> Path:
    """Create a version-numbered ZIP file of the build/MediaHive folder."""
    repo_root = _REPO_ROOT
    dist_folder = repo_root / "build" / "MediaHive"

    if not dist_folder.exists():
        raise FileNotFoundError(f"Distribution folder not found: {dist_folder}")

    zip_name = f"MediaHive-{version}-{_platform_zip_suffix()}.zip"
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

        if sys.platform == "win32":
            fetch_ffmpeg()
        elif sys.platform == "darwin":
            fetch_macos_arm64_binaries()
            ensure_macos_icon()
        else:
            print(
                "Skipping ffmpeg bundling on this platform (uses system ffmpeg if available)."
            )
        build_wheel()
        build_executable()
        zip_path = create_zip(version)

        print(f"✓ Built successfully: {zip_path}")
        print(f"  Size: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as e:
        print(f"✗ Build failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
