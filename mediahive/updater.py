"""Optional Velopack auto-update integration (GUI builds only).

In development and portable-ZIP runs Velopack is either not installed or the
app is not a Velopack installation; every helper degrades to a no-op then, so
callers never need to special-case those environments.
"""

import logging

from mediahive.config import load_config

logger = logging.getLogger(__name__)

VELOPACK_REPO_URL = "https://git.zi.fi/LeoVasanko/mediahive"


def _manager():
    """Return a Velopack UpdateManager, or None when updates are unavailable."""
    try:
        import velopack
    except ImportError:
        return None
    try:
        return velopack.UpdateManager(velopack.GiteaSource(VELOPACK_REPO_URL))
    except RuntimeError, OSError:
        # Not a Velopack installation (dev / portable run).
        return None


def pending_update() -> str | None:
    """Version of a downloaded update staged for the next launch, if any."""
    mgr = _manager()
    if mgr is None:
        return None
    try:
        asset = mgr.get_update_pending_restart()
    except RuntimeError, OSError:
        return None
    return str(asset.Version) if asset is not None else None


def apply_pending_and_restart() -> bool:
    """Apply the staged update and restart into it. False when nothing pending."""
    mgr = _manager()
    if mgr is None:
        return False
    try:
        asset = mgr.get_update_pending_restart()
        if asset is None:
            return False
        logger.info("Velopack: applying staged update %s and restarting", asset.Version)
        mgr.apply_updates_and_restart(asset)
    except (RuntimeError, OSError) as exc:
        logger.warning("Velopack: failed to apply staged update: %s", exc)
        return False
    return True


def check_and_download() -> None:
    """Download available updates in the background, unless disabled in config.

    Downloaded updates are applied automatically by Velopack on the next app
    start, so the running session is never interrupted. Network failures and
    non-Velopack runs are expected and skipped quietly.
    """
    if not load_config().auto_update:
        logger.info("Velopack: automatic updates disabled, skipping check")
        return
    mgr = _manager()
    if mgr is None:
        return
    try:
        info = mgr.check_for_updates()
        if info is None:
            logger.info("Velopack: no update available")
            return
        version = info.TargetFullRelease.Version
        logger.info("Velopack: downloading update %s", version)
        mgr.download_updates(info)
        logger.info("Velopack: update %s staged, applies on next launch", version)
    except (RuntimeError, OSError) as exc:
        logger.info("Velopack update check skipped: %s", exc)
