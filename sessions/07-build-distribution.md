# Session 07 — Build distribution (PyInstaller + NSIS/DMG + GitHub Actions + Auto-update)

> Run **after sessions 01-05 done**. Optional Session 06 polish có thể làm song song hoặc sau. Goal: ship `.exe` Windows + `.app` Mac với auto-update flow giống veo-farm.

## Goal

Distribute Youtok như native app:
- Mac: `Youtok.app` trong DMG, drag-to-Applications.
- Windows: `Youtok-Setup-X.Y.Z.exe` (NSIS installer), tạo shortcut Start Menu + Desktop.
- GitHub Actions CI build cả 2 OS từ tag push.
- In-app update flow: check GitHub Release latest version → notify user → download + install + tree-kill restart.

User experience: double-click `Youtok.app` / `Youtok.exe` → spawn server + worker → mở browser localhost:8000 → license activate (lần đầu) → dashboard.

## Read first

- `../SPEC.md` toàn bộ
- `../README.md` (Mac + Windows install instructions hiện tại)
- `wiki/sources/projects/veo-farm.md` — pattern in-app update reference (electron-builder pattern, áp dụng UX cho Python)
- Code hiện tại: `src/youtok/`, `scripts/`, `pyproject.toml`

## Stack chốt (Option A — PyInstaller pure, không Electron)

| Layer | Tool |
|---|---|
| Bundle Python | PyInstaller `--onedir` |
| Single entry point | `youtok` CLI với subcommands (serve, worker, start, activate, version) |
| Process orchestrator (start) | `multiprocessing` spawn server + worker, mở browser |
| System tray (optional) | `pystray` + Pillow |
| Win installer | NSIS với `nsis-tools` GitHub Action |
| Mac DMG | `create-dmg` shell tool |
| CI | GitHub Actions: macos-14 + windows-2022 |
| Auto-update | httpx check GitHub Releases API + spawn installer + tree-kill (psutil) |
| Versioning | `pyproject.toml` version + `__version__` constant + git tag |

## Folder structure additions

```
projects/youtok/
├── build.spec                            # PyInstaller spec
├── scripts/
│   └── build/
│       ├── build-mac.sh                  # PyInstaller + create-dmg
│       ├── build-win.ps1                 # PyInstaller + makensis
│       └── installer.nsi                 # NSIS template
├── .github/
│   └── workflows/
│       └── build.yml                     # CI workflow
├── src/youtok/
│   ├── __version__.py                    # __version__ = "0.1.0"
│   └── updater/
│       ├── __init__.py
│       ├── checker.py                    # check GitHub Releases
│       └── installer.py                  # download + spawn + tree-kill
└── assets/
    └── icons/
        ├── youtok.icns                   # Mac icon
        ├── youtok.ico                    # Windows icon
        └── youtok.png                    # tray icon
```

## Phase 1 — PyInstaller spec + entry point (~3h)

### 1.1 — Single CLI entry point

File: `src/youtok/cli.py` (extend hiện tại)

```python
import click
import sys
import webbrowser
import time
import socket
import multiprocessing
from pathlib import Path
from youtok.config import settings
from youtok.__version__ import __version__


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """Youtok — YouTube auto-cutter."""
    if ctx.invoked_subcommand is None:
        # Default action when double-clicked: start everything
        ctx.invoke(start)


@main.command()
def version():
    """Print version."""
    click.echo(f"Youtok v{__version__}")


@main.command()
def serve():
    """Run FastAPI server only."""
    import uvicorn
    uvicorn.run("youtok.api.main:app", host=settings.host, port=settings.port, reload=False)


@main.command()
def worker():
    """Run Huey worker only."""
    from huey.consumer import Consumer
    from youtok.queue.huey_app import huey
    Consumer(huey, workers=1).run()


@main.command()
def start():
    """Default: spawn server + worker, open browser. Use this on double-click."""
    if _is_port_open(settings.host, settings.port):
        click.echo(f"Already running on port {settings.port}, opening browser")
        webbrowser.open(f"http://{settings.host}:{settings.port}")
        return
    
    click.echo("Starting server + worker...")
    server = multiprocessing.Process(target=_run_server, daemon=False)
    worker = multiprocessing.Process(target=_run_worker, daemon=False)
    server.start()
    worker.start()
    
    # Wait for server ready
    for _ in range(30):
        time.sleep(1)
        if _is_port_open(settings.host, settings.port):
            break
    else:
        click.echo("Server failed to start, check logs")
        server.terminate()
        worker.terminate()
        sys.exit(1)
    
    webbrowser.open(f"http://{settings.host}:{settings.port}")
    
    # Optional: tray icon (Phase 1.3)
    try:
        from youtok.tray import run_tray
        run_tray(server_proc=server, worker_proc=worker)
    except ImportError:
        # No tray, just wait
        try:
            server.join()
            worker.join()
        except KeyboardInterrupt:
            click.echo("\nShutting down...")
            server.terminate()
            worker.terminate()


def _is_port_open(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return True
    except (socket.timeout, ConnectionRefusedError):
        return False
    finally:
        s.close()


def _run_server():
    import uvicorn
    uvicorn.run("youtok.api.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")


def _run_worker():
    from huey.consumer import Consumer
    from youtok.queue.huey_app import huey
    Consumer(huey, workers=1).run()


@main.command()
def hello():
    """Sanity check (existing)."""
    click.echo(f"ok | data_dir={settings.data_dir} | bin_dir={settings.bin_dir} | version={__version__}")


if __name__ == "__main__":
    main()
```

### 1.2 — Version file

File: `src/youtok/__version__.py`

```python
__version__ = "0.1.0"
```

Sync với `pyproject.toml` version (use `bumpver` tool optional). Hoặc đọc từ pyproject lúc runtime:

```python
import tomllib
from pathlib import Path

def _read_version():
    path = Path(__file__).parent.parent.parent / "pyproject.toml"
    if not path.exists():
        # When bundled, embed version directly
        return "0.1.0"
    with open(path, "rb") as f:
        return tomllib.load(f)["project"]["version"]

__version__ = _read_version()
```

Bundle lúc PyInstaller hardcode version qua build script (sed/replace) để tránh đọc pyproject.toml trong bundle.

### 1.3 — System tray (optional, recommend cho UX pro)

File: `src/youtok/tray.py`

```python
import pystray
from PIL import Image
from pathlib import Path
from youtok.config import settings


def run_tray(server_proc, worker_proc):
    icon_path = settings.assets_dir / "icons" / "youtok.png"
    image = Image.open(icon_path)
    
    def on_open(icon, item):
        import webbrowser
        webbrowser.open(f"http://{settings.host}:{settings.port}")
    
    def on_quit(icon, item):
        server_proc.terminate()
        worker_proc.terminate()
        icon.stop()
    
    menu = pystray.Menu(
        pystray.MenuItem("Open Youtok", on_open, default=True),
        pystray.MenuItem("Quit", on_quit),
    )
    
    icon = pystray.Icon("youtok", image, "Youtok", menu)
    icon.run()
```

Add `pystray` + `pillow` vào pyproject.toml.

### 1.4 — PyInstaller spec

File: `build.spec` (root)

```python
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

is_mac = sys.platform == "darwin"
is_win = sys.platform == "win32"

# Hidden imports — PyInstaller miss these dynamic imports
hiddenimports = [
    # FastAPI/uvicorn
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    # SQLAlchemy
    "sqlalchemy.dialects.sqlite",
    # Pydantic v2
    "pydantic.deprecated.decorator",
    # Huey
    "huey.consumer_options",
    "huey.contrib.sql_huey",
    # WhisperX deps
    "torch",
    "torch._C",
    "torch.nn",
    "torchaudio",
    "faster_whisper",
    "whisperx",
    "transformers",
    # NLTK
    "nltk",
    # PySceneDetect
    "scenedetect.detectors",
    # Anthropic
    "anthropic",
    # Misc
    "websockets",
    "httpx",
] + collect_submodules("youtok")

# Data files to bundle
datas = [
    ("assets/fonts", "assets/fonts"),
    ("assets/keys", "assets/keys"),
    ("assets/icons", "assets/icons"),
    ("src/youtok/web/templates", "youtok/web/templates"),
    ("src/youtok/web/static", "youtok/web/static"),
    ("alembic", "alembic"),
    ("alembic.ini", "."),
]

# Platform-specific binaries
if is_mac:
    datas.append(("assets/bin/mac", "assets/bin/mac"))
elif is_win:
    datas.append(("assets/bin/win", "assets/bin/win"))

# Collect data files from packages
datas += collect_data_files("whisperx")
datas += collect_data_files("faster_whisper")
datas += collect_data_files("scenedetect")
datas += collect_data_files("nltk")

a = Analysis(
    ["src/youtok/cli.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Exclude heavy unused
        "matplotlib",
        "pandas",
        "scipy",
        "tk",
        "tkinter",
    ],
    cipher=block_cipher,
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
    upx=False,  # UPX có thể conflict torch
    console=False,  # Windowed — không show console
    icon="assets/icons/youtok.ico" if is_win else "assets/icons/youtok.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="youtok",
)

# Mac: bundle as .app
if is_mac:
    app = BUNDLE(
        coll,
        name="Youtok.app",
        icon="assets/icons/youtok.icns",
        bundle_identifier="com.haiphan.youtok",
        version="0.1.0",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHumanReadableCopyright": "© 2026 Hai Phan",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
```

### 1.5 — Test PyInstaller local

```bash
uv pip install pyinstaller pystray pillow
uv run pyinstaller build.spec --clean

# Mac
open dist/Youtok.app
# Should: spawn server + worker, open browser, show tray icon

# Win
.\dist\youtok\youtok.exe
```

Verify bằng cách:
1. Browser tự mở localhost:8000.
2. License activate page hiển thị.
3. Submit 1 job test → pipeline chạy đến done.
4. Quit từ tray → cả server + worker terminate.

## Phase 2 — Installer (~3h)

### 2.1 — macOS DMG

File: `scripts/build/build-mac.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
echo "Building Youtok v$VERSION for macOS..."

# Clean
rm -rf build dist

# Run PyInstaller
uv run pyinstaller build.spec --clean

# Create DMG
if ! command -v create-dmg &> /dev/null; then
    brew install create-dmg
fi

create-dmg \
    --volname "Youtok v$VERSION" \
    --volicon "assets/icons/youtok.icns" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "Youtok.app" 175 190 \
    --hide-extension "Youtok.app" \
    --app-drop-link 425 190 \
    "dist/Youtok-$VERSION.dmg" \
    "dist/Youtok.app"

echo "Built: dist/Youtok-$VERSION.dmg"
```

### 2.2 — Windows NSIS

File: `scripts/build/installer.nsi`

```nsis
!define APPNAME "Youtok"
!define COMPANYNAME "Hai Phan"
!define DESCRIPTION "YouTube auto-cutter for 9:16 short-form"
!define VERSIONMAJOR 0
!define VERSIONMINOR 1
!define VERSIONBUILD 0

!include "MUI2.nsh"
!include "FileFunc.nsh"

Name "${APPNAME} v${VERSIONMAJOR}.${VERSIONMINOR}.${VERSIONBUILD}"
OutFile "..\..\dist\Youtok-Setup-${VERSIONMAJOR}.${VERSIONMINOR}.${VERSIONBUILD}.exe"

InstallDir "$LOCALAPPDATA\Programs\Youtok"
InstallDirRegKey HKCU "Software\Youtok" "InstallDir"

RequestExecutionLevel user

!define MUI_ICON "..\..\assets\icons\youtok.ico"
!define MUI_UNICON "..\..\assets\icons\youtok.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\youtok.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Youtok"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"
    SetOutPath $INSTDIR
    File /r "..\..\dist\youtok\*.*"
    
    ; Shortcut Start Menu + Desktop
    CreateDirectory "$SMPROGRAMS\Youtok"
    CreateShortcut "$SMPROGRAMS\Youtok\Youtok.lnk" "$INSTDIR\youtok.exe" "" "$INSTDIR\youtok.exe"
    CreateShortcut "$SMPROGRAMS\Youtok\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
    CreateShortcut "$DESKTOP\Youtok.lnk" "$INSTDIR\youtok.exe" "" "$INSTDIR\youtok.exe"
    
    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    
    ; Registry
    WriteRegStr HKCU "Software\Youtok" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Youtok" "DisplayName" "${APPNAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Youtok" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Youtok" "DisplayVersion" "${VERSIONMAJOR}.${VERSIONMINOR}.${VERSIONBUILD}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Youtok" "Publisher" "${COMPANYNAME}"
SectionEnd

Section "Uninstall"
    ; Ask if user wants to keep data folder (license + history)
    MessageBox MB_YESNO "Remove data folder (license + job history)?" IDNO skip_data
    RMDir /r "$INSTDIR\data"
    skip_data:
    
    ; Remove app files
    RMDir /r "$INSTDIR\_internal"
    Delete "$INSTDIR\youtok.exe"
    Delete "$INSTDIR\Uninstall.exe"
    
    ; Try to remove install dir
    RMDir "$INSTDIR"
    
    ; Shortcuts
    Delete "$SMPROGRAMS\Youtok\Youtok.lnk"
    Delete "$SMPROGRAMS\Youtok\Uninstall.lnk"
    RMDir "$SMPROGRAMS\Youtok"
    Delete "$DESKTOP\Youtok.lnk"
    
    ; Registry
    DeleteRegKey HKCU "Software\Youtok"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Youtok"
SectionEnd
```

File: `scripts/build/build-win.ps1`

```powershell
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

$version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([\d.]+)"').Matches[0].Groups[1].Value
Write-Host "Building Youtok v$version for Windows..."

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

uv run pyinstaller build.spec --clean

# NSIS
if (-not (Get-Command makensis -ErrorAction SilentlyContinue)) {
    Write-Error "NSIS not installed. Install via: choco install nsis"
}

makensis /DVERSIONMAJOR=$($version.Split('.')[0]) /DVERSIONMINOR=$($version.Split('.')[1]) /DVERSIONBUILD=$($version.Split('.')[2]) "scripts\build\installer.nsi"

Write-Host "Built: dist\Youtok-Setup-$version.exe"
```

## Phase 3 — GitHub Actions CI (~2h)

File: `.github/workflows/build.yml`

```yaml
name: Build & Release

on:
  push:
    tags: ['v*.*.*']
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build-mac:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install uv
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "$HOME/.cargo/bin" >> $GITHUB_PATH
      - name: Install deps
        run: |
          uv venv
          source .venv/bin/activate
          uv pip install -e ".[cpu,build]"
          uv pip install pyinstaller pystray pillow
      - name: Download binaries
        run: ./scripts/install-mac.sh
      - name: Build
        run: ./scripts/build/build-mac.sh
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: youtok-mac
          path: dist/Youtok-*.dmg

  build-windows:
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install uv
        run: |
          irm https://astral.sh/uv/install.ps1 | iex
          echo "$env:USERPROFILE\.cargo\bin" >> $env:GITHUB_PATH
        shell: pwsh
      - name: Install NSIS
        run: choco install nsis -y
      - name: Install deps
        run: |
          uv venv
          .venv\Scripts\activate
          uv pip install -e ".[cpu,build]"
          uv pip install pyinstaller pystray pillow
        shell: pwsh
      - name: Download binaries
        run: .\scripts\install-win.ps1
        shell: pwsh
      - name: Build
        run: .\scripts\build\build-win.ps1
        shell: pwsh
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: youtok-win
          path: dist/Youtok-Setup-*.exe

  release:
    needs: [build-mac, build-windows]
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/download-artifact@v4
      - name: Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            youtok-mac/Youtok-*.dmg
            youtok-win/Youtok-Setup-*.exe
          draft: true
          generate_release_notes: true
```

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
build = [
    "pyinstaller>=6.10",
    "pystray>=0.19",
    "pillow>=11",
]
```

## Phase 4 — Auto-update flow (~3h)

### 4.1 — Update checker

File: `src/youtok/updater/checker.py`

```python
import httpx
from packaging.version import Version
from pydantic import BaseModel
from youtok.__version__ import __version__
import sys

GITHUB_REPO = "haiphan/youtok"  # CHANGE to actual repo
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


class UpdateInfo(BaseModel):
    version: str
    download_url: str
    notes: str
    published_at: str


def check_for_update() -> UpdateInfo | None:
    """Returns UpdateInfo if a newer version exists, else None."""
    try:
        resp = httpx.get(RELEASES_URL, timeout=10, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    
    latest = resp.json()
    latest_version = latest["tag_name"].lstrip("v")
    
    if Version(latest_version) <= Version(__version__):
        return None
    
    # Find platform-specific asset
    if sys.platform == "darwin":
        asset_filter = lambda a: a["name"].endswith(".dmg")
    elif sys.platform == "win32":
        asset_filter = lambda a: a["name"].endswith(".exe")
    else:
        return None
    
    asset = next((a for a in latest["assets"] if asset_filter(a)), None)
    if not asset:
        return None
    
    return UpdateInfo(
        version=latest_version,
        download_url=asset["browser_download_url"],
        notes=latest.get("body", ""),
        published_at=latest.get("published_at", ""),
    )
```

### 4.2 — Installer + tree-kill

File: `src/youtok/updater/installer.py`

```python
import subprocess
import sys
import tempfile
import os
from pathlib import Path
import httpx
import psutil
from youtok.updater.checker import UpdateInfo


def download_installer(info: UpdateInfo, progress_cb=None) -> Path:
    """Download installer to temp file, return path."""
    suffix = ".dmg" if sys.platform == "darwin" else ".exe"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    target = Path(tmp.name)
    
    with httpx.stream("GET", info.download_url, follow_redirects=True, timeout=300) as r:
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with target.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(downloaded / total)
    
    return target


def install_and_restart(installer_path: Path):
    """Spawn installer + tree-kill self."""
    if sys.platform == "win32":
        # Win: spawn installer in detached mode
        # /S = silent, /D=path
        subprocess.Popen(
            [str(installer_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    elif sys.platform == "darwin":
        # Mac: open DMG (user must drag to Applications manually for now)
        subprocess.Popen(["open", str(installer_path)])
    
    # Tree-kill self (parent + children)
    current = psutil.Process(os.getpid())
    for child in current.children(recursive=True):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    
    # Give children time to exit
    psutil.wait_procs(current.children(recursive=True), timeout=5)
    
    # Force kill remaining
    for child in current.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    
    # Exit self
    sys.exit(0)
```

### 4.3 — UI integration

Modify `src/youtok/api/main.py`:

```python
from youtok.updater.checker import check_for_update
import asyncio


async def update_check_loop():
    """Check for updates every hour."""
    while True:
        try:
            info = check_for_update()
            if info:
                # Store in app state or DB
                from youtok.api.state import set_update_available
                set_update_available(info)
        except Exception:
            pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    update_task = asyncio.create_task(update_check_loop())
    progress_task = asyncio.create_task(progress_watcher())
    yield
    update_task.cancel()
    progress_task.cancel()
```

Add API route:

```python
# src/youtok/api/routes/update.py

@router.get("/check")
async def get_update_status():
    info = get_pending_update()
    if info:
        return {"available": True, "version": info.version, "notes": info.notes}
    return {"available": False}


@router.post("/install")
async def trigger_install():
    info = get_pending_update()
    if not info:
        raise HTTPException(404, "No update available")
    
    # Download (could be long, run in thread)
    def do_install():
        installer = download_installer(info)
        install_and_restart(installer)
    
    import threading
    threading.Thread(target=do_install, daemon=True).start()
    return {"status": "installing"}
```

UI banner trong `web/templates/base.html`:

```html
<div id="update-banner" class="hidden glass mx-6 mt-2 rounded-xl px-4 py-3 flex items-center justify-between">
  <div>
    <span class="text-accent-pink font-semibold">Update available:</span>
    <span id="update-version" class="text-white"></span>
  </div>
  <button onclick="installUpdate()" class="gradient-btn px-4 py-2 rounded text-sm">Update now</button>
</div>

<script>
  // Check on load + every 30 min
  async function checkUpdate() {
    const r = await fetch("/update/check");
    const data = await r.json();
    if (data.available) {
      document.getElementById("update-version").textContent = "v" + data.version;
      document.getElementById("update-banner").classList.remove("hidden");
    }
  }
  
  async function installUpdate() {
    if (!confirm("Install update? Youtok will close and restart.")) return;
    document.getElementById("update-banner").innerHTML = '<div class="text-white">Downloading...</div>';
    await fetch("/update/install", { method: "POST" });
    // Server will exit; show waiting screen
    setTimeout(() => location.reload(), 30000);
  }
  
  checkUpdate();
  setInterval(checkUpdate, 30 * 60 * 1000);
</script>
```

### 4.4 — Native notification (optional)

Install `plyer` for cross-platform native notifications:

```python
from plyer import notification

def notify_update(version: str):
    notification.notify(
        title="Youtok Update Available",
        message=f"Version {version} is ready to install",
        app_name="Youtok",
        timeout=10,
    )
```

Trigger trong `update_check_loop` khi phát hiện update mới (state change từ no-update → update-available).

## Acceptance test

### Build local Mac

```bash
./scripts/build/build-mac.sh
open dist/Youtok-0.1.0.dmg
# Drag Youtok.app to Applications
open /Applications/Youtok.app
# Verify:
# 1. Browser tự mở localhost:8000
# 2. Tray icon hiện
# 3. License activate page
# 4. Submit job test → done
# 5. Quit từ tray → cả server + worker terminate
```

### Test GitHub Actions CI

```bash
git tag v0.1.0
git push origin v0.1.0
# Visit: https://github.com/haiphan/youtok/actions
# Wait ~15-25 min for both Mac + Win build
# Check: GitHub Releases (draft) có 2 file:
#   - Youtok-0.1.0.dmg
#   - Youtok-Setup-0.1.0.exe
```

### Test auto-update

```bash
# 1. Build v0.1.0 + install
# 2. Bump version → v0.1.1, commit, tag, push
# 3. CI build v0.1.1 + publish release (un-draft)
# 4. Open Youtok v0.1.0 → wait < 1 hour → banner hiện "Update available v0.1.1"
# 5. Click "Update now" → confirm → download → installer spawn → app close
# 6. Installer ghi đè → restart → version trong /settings show v0.1.1
```

### Test Windows production

```powershell
# Trên Win machine production:
# 1. Download Youtok-Setup-0.1.0.exe từ release
# 2. Run installer → install vào %LOCALAPPDATA%\Programs\Youtok
# 3. Click shortcut Desktop → Youtok mở
# 4. License activate → submit 1 video test → done
# 5. Test channel mode + bulk mode
# 6. Update flow: làm như Mac
# 7. Uninstall → check data folder removal prompt
```

## Anti-patterns

- ❌ `--onefile` thay vì `--onedir`: extract temp dir mỗi lần chạy → startup chậm 5-10s + tốn space.
- ❌ UPX compression: conflict với torch/numpy compiled extensions.
- ❌ Bundle PyTorch CUDA wheel: tăng 1.5GB không cần thiết. CPU-only build cho user thường.
- ❌ Hardcode GitHub repo path: dùng env var `GITHUB_REPO` hoặc settings.
- ❌ Update check trong main thread: block UI. Phải async background.
- ❌ Tree-kill thiếu psutil: signal kill không cleanup descendants → orphan process.
- ❌ Ship installer không signed: Mac Gatekeeper block (cần $99/y Apple Dev), Win SmartScreen warning (cần $200/y cert). MVP skip — user phải confirm "Open anyway" lần đầu.
- ❌ Bundle `.env` chứa ANTHROPIC_API_KEY: leak. Tool yêu cầu user nhập API key qua /settings sau activate.

## Notes về API key trong distribution

**Quan trọng**: tool cần `ANTHROPIC_API_KEY` để LLM segmentation. Trong distribution model "license cấp cho user khác":

**Option 1**: Mỗi user tự có Anthropic API key.
- Pros: cost user tự chịu, mày không bao.
- Cons: user phải tự signup Anthropic + setup billing → friction cao.

**Option 2**: Mày bao API cost, key của mày embed trong tool.
- Pros: UX mượt, user paste license + dùng.
- Cons: key leak nếu reverse engineer bundle (PyInstaller --onedir dễ extract). Mày phải gánh cost.

**Option 3**: Proxy API qua server của mày.
- User → tool → mày proxy server → Anthropic.
- Mày kiểm soát rate limit + ban abuse + bill user qua license.
- Cần dev thêm proxy server (1-2 ngày).

→ Recommend MVP **Option 1** (user tự key) cho đơn giản. Nếu license cấp cho non-tech bạn bè không muốn signup → Option 3 (đầu tư sau).

Add UI `/settings` cho user nhập + save Anthropic API key vào `data/license.json` cùng với license info.

## Versioning workflow

Mỗi lần ship:

```bash
# Bump version
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml
sed -i '' 's/__version__ = "0.1.0"/__version__ = "0.1.1"/' src/youtok/__version__.py

# Commit + tag
git add -A
git commit -m "Bump version to 0.1.1"
git tag v0.1.1
git push origin main --tags
```

CI tự build + tạo draft release. Mày review draft → publish → user nhận update.

## When done

1. Build Mac local thành công, .app run được.
2. Build Win local thành công (qua CI), .exe install được trên test Win machine.
3. Tag v0.1.0 push → CI build pass → draft release có 2 file.
4. Auto-update flow test pass: v0.1.0 detect v0.1.1 → install → restart.
5. Update entity youtok status: thêm note "Distribution ready, GitHub Releases CI active".

## Pending sau session 07

- Code signing Mac + Win (cost $99 + $200/year, optional)
- Proxy API server (Option 3) nếu license cấp cho non-tech user
- Telegram bot notification khi build CI fail (giống veo-farm pattern)
- Crash reporting (Sentry hoặc tự log) — gửi log error về server để mày debug
