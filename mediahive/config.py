r"""Platform-appropriate config persistence for MediaHive.

Config file location:
  Windows:  %APPDATA%\mediahive\config.toml
  macOS:    ~/Library/Application Support/mediahive/config.toml
  Linux:    $XDG_CONFIG_HOME/mediahive/config.toml  (~/.config/mediahive/config.toml)
"""

import os
import sys
from pathlib import Path

import msgspec
import msgspec.toml


class Config(msgspec.Struct, omit_defaults=True):
    media_folder: str | None = None
    roots: dict[str, str] | None = None


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "mediahive"


def config_path() -> Path:
    return config_dir() / "config.toml"


def _migrate_legacy_media_folder(cfg: Config) -> Config:
    """If roots is empty but media_folder exists, seed roots with it."""
    if cfg.roots:
        return cfg
    if not cfg.media_folder:
        return cfg
    path = Path(cfg.media_folder)
    name = path.name or path.anchor.strip("/\\").lower() or "media"
    # Resolve collisions simply by using the basename; if user had weird layout
    # they can rename via the UI later.
    return msgspec.structs.replace(cfg, roots={name: cfg.media_folder})


def load_config() -> Config:
    path = config_path()
    if path.exists():
        try:
            cfg = msgspec.toml.decode(path.read_bytes(), type=Config)
            return _migrate_legacy_media_folder(cfg)
        except OSError, msgspec.DecodeError, msgspec.ValidationError:
            return Config()
    return Config()


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msgspec.toml.encode(cfg))
