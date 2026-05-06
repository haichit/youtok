# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Youtok.

Build: pyinstaller youtok.spec --noconfirm
Output: dist/youtok/youtok.exe
"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Build datas list dynamically — skip entries that don't exist (e.g. vendor/ffmpeg only in CI)
_all_datas = [
    ("src/youtok/web/templates", "youtok/web/templates"),
    ("src/youtok/web/static", "youtok/web/static"),
    ("src/youtok/version.py", "youtok"),
    # Vendor binaries — bundle into assets/bin/win/ to match config.bin_dir.
    # Run scripts/fetch_vendor_bins.py before building.
    ("vendor/ffmpeg/ffmpeg.exe", "assets/bin/win"),
    ("vendor/ffmpeg/ffprobe.exe", "assets/bin/win"),
    ("vendor/yt-dlp/yt-dlp.exe", "assets/bin/win"),
    ("assets", "assets"),
]
datas = [(src, dst) for src, dst in _all_datas if os.path.exists(src)]
datas += collect_data_files("litellm")
datas += collect_data_files("tiktoken")
datas += collect_data_files("tiktoken_ext")

_litellm_hiddenimports = collect_submodules("litellm")
_tiktoken_hiddenimports = collect_submodules("tiktoken") + collect_submodules("tiktoken_ext") + [
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
]

# pywebview: bundle all backends + clr_loader (needed for EdgeChromium on Windows)
_webview_hiddenimports = collect_submodules("webview") + collect_submodules("clr_loader") + [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr_loader",
    "pythonnet",
]
datas += collect_data_files("webview")
datas += collect_data_files("clr_loader")

# faster-whisper: ctranslate2 ships native DLLs as data, av/onnxruntime ship
# native libs PyInstaller's analyzer doesn't always pick up.
_whisper_hiddenimports = (
    collect_submodules("faster_whisper")
    + collect_submodules("ctranslate2")
    + collect_submodules("onnxruntime")
    + collect_submodules("av")
)
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")
datas += collect_data_files("onnxruntime")
datas += collect_data_files("av")

a = Analysis(
    ["src/youtok/cli.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=_litellm_hiddenimports + _tiktoken_hiddenimports + _webview_hiddenimports + _whisper_hiddenimports + [
        "youtok.api.main",
        "youtok.api.routes.activate",
        "youtok.api.routes.channels",
        "youtok.api.routes.drive",
        "youtok.api.routes.jobs",
        "youtok.api.routes.pages",
        "youtok.api.routes.settings",
        "youtok.api.routes.update",
        "youtok.api.ws",
        "youtok.core.pipeline",
        "youtok.core.updater",
        "youtok.core.google_drive",
        "youtok.queue.tasks",
        "youtok.queue.huey_app",
        "youtok.db.models",
        "youtok.db.crud",
        "youtok.db.base",
        "youtok.llm.cost_tracker",
        "youtok.llm.providers",
        "youtok.llm.fx",
        "youtok.license.manager",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite",
        "huey.backends",
        "huey.contrib",
        "engineio.async_drivers.threading",
        "google.auth.transport.requests",
        "google_auth_oauthlib.flow",
        "googleapiclient.discovery",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "notebook",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="youtok",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if os.path.exists("assets/icon.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="youtok",
)
