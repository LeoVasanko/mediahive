"""Media player detection and launching.

Windows: registry + known install paths.
macOS: /Applications + PATH.
Linux: PATH (which) only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import msgspec


class PlayerInfo(msgspec.Struct):
    """Descriptor for a detected media player."""

    id: str
    name: str
    family: str
    path: str | None = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _which(cmd: str) -> str | None:
    """Find a command in PATH."""
    return shutil.which(cmd)


# ---------------------------------------------------------------------------
# Windows detection
# ---------------------------------------------------------------------------


def _winreg_lookup(key_path: str, value_name: str = "") -> str | None:
    """Read a string value from the Windows registry."""
    try:
        import winreg

        # key_path like r"HKLM\Software\MPC-BE Team\MPC-BE"
        parts = key_path.split("\\", 1)
        hive_name = parts[0].upper()
        subpath = parts[1] if len(parts) > 1 else ""
        hive = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT,
        }.get(hive_name)
        if hive is None:
            return None
        with winreg.OpenKey(hive, subpath) as key:
            val, _ = winreg.QueryValueEx(key, value_name or None)
            if isinstance(val, str):
                return val
    except OSError:
        pass
    return None


def _expand_command_path(cmd: str) -> Path | None:
    r"""Extract the executable path from a shell\open\command string.

    Handles quoted paths like ``"C:\Program Files\Player\player.exe" "%1"``
    and unquoted like ``C:\Program Files\Player\player.exe "%1"``.
    """
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end != -1:
            exe = cmd[1:end]
            return Path(exe) if Path(exe).exists() else None
    # Space-separated, take first token
    parts = cmd.split()
    if parts:
        candidate = Path(parts[0])
        if candidate.exists():
            return candidate
    return None


def _detect_default_player() -> PlayerInfo | None:
    """Detect which program is associated with .mkv files."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r".mkv") as key:
            progid, _ = winreg.QueryValueEx(key, None)
        if not progid:
            return None
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, f"{progid}\\shell\\open\\command"
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, None)
        exe_path = _expand_command_path(cmd) if cmd else None
        name = progid.replace(".", " ").title()
        return PlayerInfo(
            id="default-associated",
            name=f"Default ({name})",
            family="default",
            path=str(exe_path) if exe_path else None,
        )
    except OSError:
        return None


def _detect_mpc_be() -> PlayerInfo | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "MPC-BE"
        / "mpc-be64.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "MPC-BE"
        / "mpc-be.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "MPC-BE"
        / "mpc-be.exe",
    ]
    # Registry path used by MPC-BE installer
    reg = _winreg_lookup(r"HKLM\Software\MPC-BE Team\MPC-BE", "ExePath")
    if reg:
        candidates.insert(0, Path(reg))

    for path in candidates:
        if path.exists():
            return PlayerInfo(id="mpc-be", name="MPC-BE", family="mpc", path=str(path))
    return None


def _detect_mpc_hc() -> PlayerInfo | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "MPC-HC"
        / "mpc-hc64.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "MPC-HC"
        / "mpc-hc.exe",
    ]
    for path in candidates:
        if path.exists():
            return PlayerInfo(id="mpc-hc", name="MPC-HC", family="mpc", path=str(path))
    return None


def _detect_vlc() -> PlayerInfo | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "VideoLAN"
        / "VLC"
        / "vlc.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "VideoLAN"
        / "VLC"
        / "vlc.exe",
    ]
    for path in candidates:
        if path.exists():
            return PlayerInfo(
                id="vlc", name="VLC media player", family="vlc", path=str(path)
            )
    return None


def _detect_potplayer() -> PlayerInfo | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "DAUM"
        / "PotPlayer"
        / "PotPlayer64.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "DAUM"
        / "PotPlayer"
        / "PotPlayer.exe",
    ]
    for path in candidates:
        if path.exists():
            return PlayerInfo(
                id="potplayer", name="PotPlayer", family="potplayer", path=str(path)
            )
    return None


def _detect_mpv() -> PlayerInfo | None:
    # Check PATH first
    mpv_in_path = shutil.which("mpv")
    if mpv_in_path:
        return PlayerInfo(id="mpv", name="mpv", family="mpv", path=mpv_in_path)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        / "mpv"
        / "mpv.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "mpv" / "mpv.exe",
    ]
    for path in candidates:
        if path.exists():
            return PlayerInfo(id="mpv", name="mpv", family="mpv", path=str(path))
    return None


# ---------------------------------------------------------------------------
# macOS detection
# ---------------------------------------------------------------------------


def _detect_macos_app(
    bundle_name: str, display_name: str, family: str
) -> PlayerInfo | None:
    """Detect an app in /Applications or ~/Applications."""
    for apps_dir in (Path("/Applications"), Path.home() / "Applications"):
        app_path = apps_dir / f"{bundle_name}.app"
        if app_path.exists():
            # Find the actual executable inside the bundle
            macos_dir = app_path / "Contents" / "MacOS"
            if macos_dir.exists():
                # Often the executable name matches the bundle name
                exe = macos_dir / bundle_name
                if exe.exists():
                    return PlayerInfo(
                        id=family, name=display_name, family=family, path=str(exe)
                    )
                # Fallback: any executable in MacOS dir
                for child in macos_dir.iterdir():
                    if child.is_file() and os.access(child, os.X_OK):
                        return PlayerInfo(
                            id=family, name=display_name, family=family, path=str(child)
                        )
    return None


def _detect_iina() -> PlayerInfo | None:
    return _detect_macos_app("IINA", "IINA", "mpv")


def _detect_vlc_macos() -> PlayerInfo | None:
    return _detect_macos_app("VLC", "VLC media player", "vlc")


# ---------------------------------------------------------------------------
# Linux detection
# ---------------------------------------------------------------------------


def _detect_vlc_linux() -> PlayerInfo | None:
    path = _which("vlc")
    if path:
        return PlayerInfo(id="vlc", name="VLC media player", family="vlc", path=path)
    return None


def _detect_smplayer() -> PlayerInfo | None:
    path = _which("smplayer")
    if path:
        return PlayerInfo(id="smplayer", name="SMPlayer", family="mpv", path=path)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_players() -> list[PlayerInfo]:
    """Return a list of detected players plus default and custom entries."""
    detected: list[PlayerInfo] = []

    if sys.platform == "win32":
        # Specific players
        for detector in (
            _detect_mpc_be,
            _detect_mpc_hc,
            _detect_vlc,
            _detect_potplayer,
            _detect_mpv,
        ):
            player = detector()
            if player:
                detected.append(player)

        # Default associated player (optional, for info)
        default_assoc = _detect_default_player()
        if default_assoc and default_assoc.path:
            # Only add if it's a different executable than one we already found
            existing_paths = {p.path.lower() for p in detected if p.path}
            if default_assoc.path.lower() not in existing_paths:
                detected.append(default_assoc)

    elif sys.platform == "darwin":
        for detector in (_detect_iina, _detect_vlc_macos):
            player = detector()
            if player:
                detected.append(player)

    else:
        # Linux / other Unix
        for detector in (_detect_vlc_linux, _detect_smplayer):
            player = detector()
            if player:
                detected.append(player)

    # Always include the abstract "default" and "custom" options
    result: list[PlayerInfo] = [
        PlayerInfo(id="default", name="System Default", family="default"),
    ]
    result.extend(detected)
    result.append(PlayerInfo(id="custom", name="Custom…", family="custom"))
    return result


def launch_player(
    player_id: str,
    file_path: Path,
    player_path: str | None = None,
    custom_cmd: str | None = None,
) -> None:
    """Launch a media file with the specified player.

    Args:
        player_id: One of "default", "custom", or a detected player id.
        file_path: Absolute path to the media file.
        player_path: Executable path for detected players (from detection).
        custom_cmd: Raw command string for "custom" player (with %s).

    """
    if player_id == "default" or not player_id:
        if sys.platform == "win32":
            os.startfile(str(file_path))
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(
            [opener, str(file_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    if player_id == "custom":
        if not custom_cmd:
            raise ValueError("Custom player command is empty")
        cmd_str = custom_cmd.replace("%s", str(file_path))
        if "%s" not in custom_cmd:
            cmd_str = f'{custom_cmd} "{file_path}"'
        # Use shell=True for custom commands so arguments are parsed naturally
        subprocess.Popen(
            cmd_str, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return

    if not player_path:
        raise ValueError(f"Player path not provided for {player_id}")

    # Detected player: pass file path as the single argument
    subprocess.Popen(
        [player_path, str(file_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
