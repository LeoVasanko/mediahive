"""Platform-appropriate config persistence for MediaHive.

Config file location:
  Windows:  %APPDATA%\\mediahive\\config.toml
  macOS:    ~/Library/Application Support/mediahive/config.toml
  Linux:    $XDG_CONFIG_HOME/mediahive/config.toml  (~/.config/mediahive/config.toml)
"""

import os
import sys
from pathlib import Path

import msgspec
import msgspec.toml


class Config(msgspec.Struct):
    media_folder: str | None = None


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


def load_config() -> Config:
    path = config_path()
    if path.exists():
        try:
            return msgspec.toml.decode(path.read_bytes(), type=Config)
        except Exception:
            return Config()
    return Config()


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msgspec.toml.encode(cfg))
