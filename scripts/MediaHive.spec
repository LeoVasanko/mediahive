# MediaHive.spec  — PyInstaller build for the MediaHive desktop GUI app
#
# Build manually (from repo root):
#   uv run --no-project --python 3.14 --with ".[gui]" --with pyinstaller \
#     pyinstaller --noconfirm --clean scripts/MediaHive.spec
#
# Or use the build script (recommended—handles versioning and packaging):
#   uv run scripts/winbuild.py

import sys
import mediahive.winmain
import mediahive.server
from pathlib import Path

block_cipher = None

_pkg = Path(mediahive.server.__file__).parent
_frontend_build = _pkg / "frontend-build"
_logo_webp = _pkg / "assets" / "mediahive.webp"
_icon_win = _pkg / "assets" / "mediahive.ico"
_icon_mac = _pkg / "assets" / "mediahive.icns"
_tools_dir = Path(SPECPATH).parent / "build" / "ffmpeg"
_tool_names = ["ffmpeg.exe"] if sys.platform == "win32" else ["ffmpeg"]

_binaries = []
for _tool_name in _tool_names:
    _tool_path = _tools_dir / _tool_name
    if _tool_path.exists():
        _binaries.append((str(_tool_path), "."))

_datas = [
    # Bundled Vue frontend served by the FastAPI backend
    (str(_frontend_build), "mediahive/frontend-build"),
]
if _icon_win.exists():
    _datas.append((str(_icon_win), "mediahive/assets"))
if _icon_mac.exists():
    _datas.append((str(_icon_mac), "mediahive/assets"))
if _logo_webp.exists():
    _datas.append((str(_logo_webp), "mediahive/assets"))

_hiddenimports = [
    # uvicorn dynamic imports
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # mediahive & hivescan modules imported at runtime
    "mediahive.server",
    "mediahive.hivescan.scanner",
    "mediahive.hivescan.indexer",
    "mediahive.hivescan.scanning",
    "mediahive.hivescan.images",
    "mediahive.hivescan.showreel",
    "mediahive.hivescan.tmdb_client",
    # async / ASGI internals
    "anyio",
    "anyio._backends._asyncio",
    "starlette.routing",
    # msgspec TOML write backend
    "tomli_w",
]

if sys.platform == "darwin":
    _hiddenimports.extend(
        [
            # pywebview Qt backend selected dynamically via webview.start(gui="qt")
            "webview.platforms.qt",
            "qtpy",
            "PyQt5",
            "PyQt5.QtCore",
            "PyQt5.QtGui",
            "PyQt5.QtWidgets",
            "PyQt5.QtWebEngineWidgets",
        ]
    )

a = Analysis(
    [mediahive.winmain.__file__],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaHive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    icon=(
        str(_icon_mac)
        if sys.platform == "darwin" and _icon_mac.exists()
        else str(_icon_win) if _icon_win.exists() else None
    ),
    # windowed=True hides the console; the backend subprocess inherits this
    console=False,
    windowed=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MediaHive",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MediaHive.app",
        icon=str(_icon_mac) if _icon_mac.exists() else None,
        bundle_identifier="fi.zi.mediahive",
    )
