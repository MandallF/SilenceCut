# PyInstaller spec for SilenceCut
# Build: pyinstaller silencecut.spec --noconfirm

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files


ROOT = Path(os.getcwd()).resolve()
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"

datas = []

# Frontend static build, mounted by FastAPI at /
if FRONTEND_DIST.is_dir():
    datas.append((str(FRONTEND_DIST), "frontend_dist"))

# Backend Python modules — keep them next to the entry script.
for fname in ("main.py", "analyzer.py", "exporter.py", "ffmpeg_path.py", "premiere_xml.py"):
    p = BACKEND / fname
    if p.exists():
        datas.append((str(p), "backend"))

# imageio-ffmpeg ships its static binary as a package data file.
datas += collect_data_files("imageio_ffmpeg")

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("h11")
hiddenimports += collect_submodules("pydantic")
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "imageio_ffmpeg",
    "psutil",
]


a = Analysis(
    ["app.py"],
    pathex=[str(ROOT), str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib",
        "scipy",
        "librosa", "numba", "llvmlite",
        "test", "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SilenceCut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
