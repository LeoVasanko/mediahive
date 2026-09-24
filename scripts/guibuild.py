#!/usr/bin/env -S uv run
"""Build the desktop GUI application and package it with Velopack.

Usage:
    uv run scripts/guibuild.py

This runs in the project environment where dependencies
are available via pyproject.toml.

This script:
    1. Reads the version from pyproject.toml
    2. Runs `uv build` to produce the wheel/sdist
    3. On Windows/macOS, downloads the ffmpeg binary for bundling
    4. Builds MediaHive using PyInstaller
    5. Packages with Velopack: Setup.exe (Windows), .pkg (macOS),
       .AppImage (Linux), plus the update feed in build/velopack/
       that release.py uploads for in-app auto-updates
    6. On Windows, also creates a portable ZIP (no auto-updates)
"""

import io
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import NamedTuple

import setuptools_scm
from platformdirs import user_cache_path

# BtbN automated builds always publish a 'latest' tag with this asset.
_FFMPEG_URL = (
    "https://github.com/BtbN/ffmpeg-builds/releases/download/latest"
    "/ffmpeg-master-latest-win64-gpl.zip"
)
_MACOS_ARM64_TOOL_URLS = {
    "ffmpeg": "https://www.osxexperts.net/ffmpeg81arm.zip",
}
_REPO_ROOT = Path(__file__).parent.parent
_ASSETS_DIR = _REPO_ROOT / "mediahive" / "assets"


def _build_cache_dir() -> Path:
    """Return the persistent cross-build cache dir for downloaded tools (CI wipes build/)."""
    return user_cache_path("mediahive-build", appauthor=False, opinion=False)


_FFMPEG_STAGING = _build_cache_dir() / "ffmpeg"

# Velopack CLI (dotnet tool package). Runs on the machine's .NET runtime; the
# produced Setup.exe/Update.exe are native and need no runtime on end-user
# machines. Pin a version whose tools target an installed .NET major.
_VPK_VERSION = "1.2.158"
_VPK_URL = (
    f"https://api.nuget.org/v3-flatcontainer/vpk/{_VPK_VERSION}"
    f"/vpk.{_VPK_VERSION}.nupkg"
)
_VPK_STAGING = _build_cache_dir() / f"vpk-{_VPK_VERSION}"


class _Platform(NamedTuple):
    """Per-platform naming/packaging constants.

    tag is the release artifact suffix. Only Windows keeps an arch marker
    (win64); macOS builds are arm64-only and we ship one Linux flavor.
    """

    tag: str  # win64 / macos / linux
    channel: str  # Velopack update channel: win / osx / linux
    rid: str  # Velopack runtime id
    dist_dir: str  # PyInstaller output dir under build/
    icon: str  # file in mediahive/assets
    main_exe: str
    setup_ext: str


def _platform() -> _Platform:
    if sys.platform == "win32":
        return _Platform(
            "win64",
            "win",
            "win-x64",
            "MediaHive",
            "mediahive.ico",
            "MediaHive.exe",
            ".exe",
        )
    if sys.platform == "darwin":
        return _Platform(
            "macos",
            "osx",
            "osx-arm64",
            "MediaHive.app",
            "mediahive.icns",
            "MediaHive",
            ".pkg",
        )
    return _Platform(
        "linux",
        "linux",
        "linux-x64",
        "MediaHive",
        "mediahive.png",
        "MediaHive",
        ".AppImage",
    )


def setup_artifact_name() -> str:
    """Versionless name so releases/download/latest/<name> links stay valid."""
    p = _platform()
    # Windows keeps the -setup suffix: a bare .exe isn't self-explanatory.
    suffix = "-setup" if sys.platform == "win32" else ""
    return f"MediaHive-{p.tag}{suffix}{p.setup_ext}"


def fetch_ffmpeg() -> Path:
    """Download latest ffmpeg.exe from BtbN builds into the persistent build cache."""
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
        with zf.open(ffmpeg_entry) as src:
            Path(dest).write_bytes(src.read())

    print(f"ffmpeg staged at {dest} ({dest.stat().st_size // 1024 // 1024} MB)")
    return dest


def fetch_macos_arm64_binaries() -> dict[str, Path]:
    """Download prebuilt macOS arm64 ffmpeg binary into the persistent build cache."""
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
            with zf.open(entry_name) as src:
                Path(dest).write_bytes(src.read())

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


_VPK_TFM = "net10.0"
_VPK_REQUIRED_DOTNET_MAJOR = int(re.fullmatch(r"net(\d+)\.0", _VPK_TFM).group(1))


def fetch_vpk() -> Path:
    """Download the Velopack CLI package into the persistent build cache.

    Returns the path to vpk.dll, runnable with `dotnet vpk.dll ...`.
    """
    vpk_dll = _VPK_STAGING / "tools" / _VPK_TFM / "any" / "vpk.dll"
    if vpk_dll.exists():
        print(f"vpk already staged at {_VPK_STAGING}, skipping download.")
        return vpk_dll

    _VPK_STAGING.mkdir(parents=True, exist_ok=True)
    print(f"Downloading vpk from {_VPK_URL} ...")
    with urllib.request.urlopen(_VPK_URL) as resp:
        data = resp.read()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(_VPK_STAGING)

    if not vpk_dll.exists():
        raise RuntimeError(f"vpk.dll not found in package at {vpk_dll}")
    print(f"vpk staged at {_VPK_STAGING}")
    return vpk_dll


def _dotnet_runtime_major(exe: Path) -> int | None:
    """Return the highest installed Microsoft.NETCore.App major version, or None."""
    try:
        result = subprocess.run(
            [str(exe), "--list-runtimes"], capture_output=True, text=True, timeout=30
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    majors = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Microsoft.NETCore.App":
            try:
                majors.append(int(parts[1].split(".")[0]))
            except ValueError:
                continue
    return max(majors, default=None)


def fetch_dotnet() -> str:
    """Resolve a system dotnet host able to run vpk (needs .NET >= 10).

    The .NET SDK is a build prerequisite installed on the build machine —
    downloading a runtime per build is slow and flaky. Several dotnet
    installations may coexist (PATH may resolve to a runtime-only .NET 8
    while scoop holds the SDK 10), so probe known locations and pick the
    newest runtime rather than the first that runs.
    """
    exe_name = "dotnet.exe" if sys.platform == "win32" else "dotnet"
    candidates: list[Path] = []
    root = os.environ.get("DOTNET_ROOT")
    if root:
        candidates.append(Path(root) / exe_name)
    which = shutil.which("dotnet")
    if which:
        candidates.append(Path(which))
    if sys.platform == "win32":
        candidates += [
            Path(r"C:\ProgramData\scoop\apps\dotnet-sdk\current") / exe_name,
            Path(r"C:\Program Files\dotnet") / exe_name,
        ]
    elif sys.platform == "darwin":
        candidates += [
            Path("/opt/homebrew/bin") / exe_name,
            Path("/usr/local/share/dotnet") / exe_name,
        ]
    else:
        candidates += [
            Path("/usr/share/dotnet") / exe_name,
            Path("/usr/lib/dotnet") / exe_name,
            Path.home() / ".dotnet" / exe_name,
        ]

    best: tuple[int, Path] | None = None
    for exe in candidates:
        if not exe.exists():
            continue
        major = _dotnet_runtime_major(exe)
        if major is not None and (best is None or major > best[0]):
            best = (major, exe)

    if best is not None and best[0] >= _VPK_REQUIRED_DOTNET_MAJOR:
        print(f"Using dotnet at {best[1]} (.NET {best[0]})")
        return str(best[1])

    found = f"newest found is .NET {best[0]} at {best[1]}" if best else "none found"
    raise RuntimeError(
        f"vpk requires Microsoft.NETCore.App >= {_VPK_REQUIRED_DOTNET_MAJOR} ({found}). "
        "Install the current .NET SDK on this build machine "
        "(Windows: `scoop install dotnet-sdk`; macOS: `brew install dotnet-sdk`; "
        "Linux: distro `dotnet-sdk` package or the dotnet-install script)."
    )


def build_velopack(version: str) -> Path:
    """Build the Velopack installer/bundle for this platform.

    Windows: per-user Setup.exe. macOS: .pkg installer. Linux: .AppImage.
    Also produces the update feed (releases.<channel>.json, *.nupkg) in
    build/velopack/ for release.py to upload — in-app auto-updates read it
    from the Gitea release. Velopack installs carry no Mark-of-the-Web, so
    the .NET CLR loads pythonnet/pywebview assemblies that it refuses from
    a downloaded ZIP.
    """
    plat = _platform()
    dist_folder = _REPO_ROOT / "build" / plat.dist_dir
    if not dist_folder.exists():
        raise FileNotFoundError(f"Distribution folder not found: {dist_folder}")

    vpk_dll = fetch_vpk()
    releases_dir = _REPO_ROOT / "build" / "velopack"

    cmd = [
        fetch_dotnet(),
        str(vpk_dll),
        "pack",
        "--packId",
        "MediaHive",
        "--packVersion",
        version,
        "--packDir",
        str(dist_folder),
        "--mainExe",
        plat.main_exe,
        "--packAuthors",
        "MediaHive",
        "--packTitle",
        "MediaHive",
        "--icon",
        str(_ASSETS_DIR / plat.icon),
        "--runtime",
        plat.rid,
        "--outputDir",
        str(releases_dir),
    ]
    if sys.platform == "darwin":
        cmd += ["--instWelcome", str(_ASSETS_DIR / "macos-installer-welcome.txt")]
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"vpk failed to start: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"vpk pack failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    setup = next(iter(sorted(releases_dir.glob(f"*Setup*{plat.setup_ext}"))), None)
    if setup is None:
        setup = next(iter(sorted(releases_dir.glob(f"*{plat.setup_ext}"))), None)
    if setup is None:
        raise RuntimeError(f"vpk produced no *{plat.setup_ext} in {releases_dir}")
    if sys.platform == "darwin":
        force_macos_user_install(setup)
    artifact = _REPO_ROOT / "build" / setup_artifact_name()
    artifact.unlink(missing_ok=True)
    setup.rename(artifact)
    rename_feed_package(releases_dir, version, plat.channel)
    return artifact


def rename_feed_package(releases_dir: Path, version: str, channel: str) -> None:
    """Rename this platform's update-feed nupkg in place.

    vpk hardcodes MediaHive-{ver}[-{channel}]-full.nupkg (Windows, the legacy
    default channel, gets no marker). Rename all to the uniform
    mediahive-{ver}-{channel}-full.nupkg: lowercase groups them with the
    wheel/sdist below the capitalized user downloads on the release page,
    and every platform carries its channel. releases.<channel>.json
    references the filename, so patch it too.
    """
    old_name = f"MediaHive-{version}-full.nupkg"
    if not (releases_dir / old_name).exists():
        old_name = f"MediaHive-{version}-{channel}-full.nupkg"
    nupkg = releases_dir / old_name
    if not nupkg.exists():
        raise RuntimeError(f"vpk produced no {old_name} in {releases_dir}")

    new_name = f"mediahive-{version}-{channel}-full.nupkg"
    manifest = releases_dir / f"releases.{channel}.json"
    text = manifest.read_text()
    if old_name not in text:
        raise RuntimeError(f"{manifest.name} does not reference {old_name}")
    manifest.write_text(text.replace(old_name, new_name))
    nupkg.rename(nupkg.with_name(new_name))


def force_macos_user_install(pkg: Path) -> None:
    """Restrict the Velopack-generated pkg to per-user installs (~/Applications).

    Velopack hardcodes two install domains (currentUserHome + localSystem) in
    the distribution XML. System installs land in /Applications, which the
    user may not own — Velopack's UpdateMac then cannot replace the .app on
    auto-update. With a single domain, macOS Installer skips the Destination
    Select page and installs to ~/Applications without admin rights.

    Also drops the `sudo -u "$USER"` prefix from Velopack's postinstall
    script: under a per-user install the script already runs as the
    installing user, and sudo would fail for lack of a tty.

    NB: only ever use `pkgutil --expand` (which keeps component Payloads
    archived) — `--expand-full` flattens payloads to loose files that
    `--flatten` cannot repack, producing a pkg that "installs" nothing.
    """
    expanded = pkg.with_name(pkg.stem + "-expanded")
    shutil.rmtree(expanded, ignore_errors=True)
    subprocess.run(["pkgutil", "--expand", str(pkg), str(expanded)], check=True)

    dist_xml = expanded / "Distribution"
    xml = dist_xml.read_text()
    new_xml, count = re.subn(
        r"<domains [^>]*/>",
        '<domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="false" />',
        xml,
    )
    if count != 1:
        raise RuntimeError("Unexpected distribution.xml: <domains> not found")
    dist_xml.write_text(new_xml)

    # Edit postinstall inside the component pkg. Depending on the macOS
    # version, --expand leaves the component as an archived file (needs a
    # nested expand/flatten round) or as an already-expanded directory.
    components = list(expanded.glob("*.pkg"))
    if len(components) != 1:
        contents = sorted(p.name for p in expanded.iterdir())
        raise RuntimeError(
            f"Unexpected pkg layout: components={components} in {contents}"
        )
    component = components[0]
    if component.is_dir():
        comp_dir = component
    else:
        comp_dir = expanded / (component.stem + "-component")
        subprocess.run(
            ["pkgutil", "--expand", str(component), str(comp_dir)], check=True
        )
    postinstall = comp_dir / "Scripts" / "postinstall"
    script = postinstall.read_text()
    if 'sudo -u "$USER" ' not in script:
        raise RuntimeError("Unexpected postinstall script: sudo prefix not found")
    postinstall.write_text(script.replace('sudo -u "$USER" ', ""))
    if comp_dir is not component:
        subprocess.run(
            ["pkgutil", "--flatten", str(comp_dir), str(component)], check=True
        )
        shutil.rmtree(comp_dir)

    subprocess.run(["pkgutil", "--flatten", str(expanded), str(pkg)], check=True)
    shutil.rmtree(expanded)


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


def create_portable_zip() -> Path:
    """Create the Windows portable ZIP of the build/MediaHive folder.

    Velopack-less plain-folder distribution for users who cannot or do not
    want to run Setup.exe. No auto-updates; the app strips Mark-of-the-Web
    from bundled DLLs at first run instead.
    """
    dist_folder = _REPO_ROOT / "build" / "MediaHive"
    if not dist_folder.exists():
        raise FileNotFoundError(f"Distribution folder not found: {dist_folder}")

    zip_path = _REPO_ROOT / "build" / "MediaHive-win64-portable.zip"
    print(f"Creating {zip_path}...")
    shutil.make_archive(
        str(zip_path.with_suffix("")),  # removes .zip so make_archive can add it
        "zip",
        root_dir=str(dist_folder),  # zip contents of MediaHive/, not the folder itself
    )
    return zip_path


def main() -> None:
    # Windows consoles default to cp1252, which can't encode ✓/✗
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
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
                "Skipping ffmpeg bundling on this platform "
                "(uses system ffmpeg if available)."
            )
        build_wheel()
        build_executable()

        artifacts = [build_velopack(version)]
        if sys.platform == "win32":
            artifacts.append(create_portable_zip())

        for artifact_path in artifacts:
            print(f"✓ Built successfully: {artifact_path}")
            print(f"  Size: {artifact_path.stat().st_size / (1024 * 1024):.1f} MB")
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as e:
        print(f"✗ Build failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
