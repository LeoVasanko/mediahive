"""Hatch build hook for building Vue frontend during package build."""

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found]
    BuildHookInterface,
)

sys.path.insert(0, str(Path(__file__).parent))
from buildutil import build


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data) -> None:
        super().initialize(version, build_data)
        root = Path(self.root)
        frontend_src = root / "frontend"
        frontend_build = root / "mediahive" / "frontend-build"

        # When building a wheel from sdist, frontend sources may be omitted
        # while prebuilt assets are already present in mediahive/frontend-build.
        if frontend_src.exists():
            build(str(frontend_src))
            return

        if frontend_build.exists():
            return

        msg = (
            "Frontend build is missing. Expected either source directory "
            f"'{frontend_src}' or prebuilt assets in '{frontend_build}'."
        )
        raise RuntimeError(msg)
