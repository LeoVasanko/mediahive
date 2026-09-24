r"""Platform-appropriate config persistence for MediaHive.

Locations (via platformdirs):
  Config — Windows: %LOCALAPPDATA%\mediahive\config.toml
           macOS:   ~/Library/Application Support/mediahive/config.toml
           Linux:   $XDG_CONFIG_HOME/mediahive/config.toml
  Logs   — Windows: %LOCALAPPDATA%\mediahive\mediahive.log
           macOS:   ~/Library/Logs/mediahive/mediahive.log
           Linux:   $XDG_STATE_HOME/mediahive/mediahive.log
"""

from pathlib import Path

import msgspec
import msgspec.toml
from fastapi_vue import env
from platformdirs import user_config_path, user_log_path


class Config(msgspec.Struct, omit_defaults=True):
    roots: dict[str, str] | None = None
    auto_update: bool = True


# Runtime config shared between the CLI entrypoint and the server process via
# fastapi-vue's env teleport (MEDIAHIVE_CONFIG). Values set here take
# precedence over the persisted config file.
config = env(Config)


def config_dir() -> Path:
    # appauthor=False: avoid the doubled %LOCALAPPDATA%\mediahive\mediahive.
    # roaming=False: config is machine-specific state, not something to sync
    # across a domain profile.
    return user_config_path("mediahive", appauthor=False, roaming=False)


def log_dir() -> Path:
    # opinion=False: no extra Logs/ subdir; mediahive.log sits beside config.
    return user_log_path("mediahive", appauthor=False, opinion=False)


def config_path() -> Path:
    return config_dir() / "config.toml"


def load_config() -> Config:
    path = config_path()
    if path.exists():
        try:
            return msgspec.toml.decode(path.read_bytes(), type=Config)
        except OSError, msgspec.DecodeError, msgspec.ValidationError:
            return Config()
    return Config()


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msgspec.toml.encode(cfg))
