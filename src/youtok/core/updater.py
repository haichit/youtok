"""Auto-update system for Youtok.

Mirrors Veo Farm's electron-updater pattern:
- Check GitHub Releases on startup + every 30 min
- Auto-download in background when update found
- Auto-install on app quit (or manual install via UI)
- Post-update detection via last-version.txt
- Kill all children (Huey worker, ffmpeg) before restart
"""

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from loguru import logger
from packaging.version import Version

from youtok.version import __version__

GITHUB_OWNER = "haichit"
GITHUB_REPO = "youtok"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_SEC = 30 * 60  # 30 minutes

_state: dict = {"status": "idle"}
_lock = threading.Lock()
_auto_install_on_quit = True
_check_timer: threading.Timer | None = None


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def get_current_version() -> str:
    return __version__


def get_state() -> dict:
    with _lock:
        return dict(_state)


def _set_state(**kwargs):
    with _lock:
        _state.update(kwargs)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _get_exe_path() -> Path:
    if is_frozen():
        return Path(sys.executable)
    return Path(__file__)


def _get_data_dir() -> Path:
    """App data directory for storing last-version.txt etc."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "Youtok"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Post-update detection (like Veo Farm's detectJustUpdated)
# ---------------------------------------------------------------------------

def detect_just_updated() -> dict:
    """Compare current version with last-version.txt.
    Returns {"updated": bool, "from": str|None, "to": str}."""
    version_file = _get_data_dir() / "last-version.txt"
    current = get_current_version()
    prev = None
    try:
        if version_file.exists():
            prev = version_file.read_text().strip()
    except Exception:
        pass
    try:
        version_file.write_text(current)
    except Exception as e:
        logger.warning(f"Cannot persist last-version.txt: {e}")

    updated = prev is not None and prev != current
    return {"updated": updated, "from": prev, "to": current}


# ---------------------------------------------------------------------------
# Check for update
# ---------------------------------------------------------------------------

def check_for_update(auto_download: bool = True) -> dict:
    """Check GitHub for latest release.
    If auto_download=True and update found, starts download immediately
    (mirrors electron-updater's autoDownload=true)."""
    _set_state(status="checking")
    try:
        resp = httpx.get(
            GITHUB_API,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            _set_state(status="idle")
            return {"update_available": False, "reason": "No releases found"}
        resp.raise_for_status()
        data = resp.json()

        tag = data.get("tag_name", "")
        latest_ver = tag.lstrip("v")
        current_ver = get_current_version()

        try:
            has_update = Version(latest_ver) > Version(current_ver)
        except Exception:
            has_update = latest_ver != current_ver

        if not has_update:
            _set_state(status="idle")
            return {
                "update_available": False,
                "current_version": current_ver,
                "latest_version": latest_ver,
            }

        asset_url = None
        asset_name = None
        asset_size = 0
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".exe") or name.endswith(".zip"):
                asset_url = asset.get("browser_download_url")
                asset_name = name
                asset_size = asset.get("size", 0)
                break

        _set_state(
            status="available",
            latest_version=latest_ver,
            asset_url=asset_url,
            asset_name=asset_name,
            asset_size=asset_size,
            release_url=data.get("html_url", ""),
        )

        logger.info(f"Update available: v{current_ver} → v{latest_ver}")

        result = {
            "update_available": True,
            "current_version": current_ver,
            "latest_version": latest_ver,
            "asset_name": asset_name,
            "asset_size": asset_size,
            "release_url": data.get("html_url", ""),
        }

        if auto_download and asset_url:
            download_update()

        return result

    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        _set_state(status="error", error_message=str(e))
        return {"update_available": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_update() -> dict:
    """Download the latest release asset in background."""
    state = get_state()
    if state.get("status") not in ("available",):
        return {"ok": False, "reason": f"Nothing to download (status={state.get('status')})"}

    asset_url = state.get("asset_url")
    if not asset_url:
        _set_state(status="error", error_message="No downloadable asset in release")
        return {"ok": False, "reason": "No exe/zip asset found in release"}

    _set_state(status="downloading", download_percent=0)

    def _download():
        try:
            tmp_dir = Path(tempfile.gettempdir()) / "youtok_update"
            tmp_dir.mkdir(exist_ok=True)

            asset_name = state.get("asset_name", "youtok_update.exe")
            dest = tmp_dir / asset_name

            with httpx.stream("GET", asset_url, timeout=300, follow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = int(downloaded * 100 / total) if total else 0
                        _set_state(download_percent=pct)

            _set_state(
                status="downloaded",
                download_percent=100,
                downloaded_path=str(dest),
            )
            logger.info(f"Update downloaded to {dest}")

        except Exception as e:
            logger.warning(f"Update download failed: {e}")
            _set_state(status="error", error_message=str(e))

    threading.Thread(target=_download, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install_update() -> dict:
    """Replace current exe with downloaded update and schedule restart."""
    state = get_state()
    if state.get("status") != "downloaded":
        return {"ok": False, "reason": f"No downloaded update (status={state.get('status')})"}

    downloaded_path = Path(state.get("downloaded_path", ""))
    if not downloaded_path.exists():
        _set_state(status="error", error_message="Downloaded file missing")
        return {"ok": False, "reason": "Downloaded file not found"}

    if not is_frozen():
        _set_state(status="error", error_message="Cannot self-update in dev mode")
        return {"ok": False, "reason": "Self-update only works in packaged exe mode"}

    try:
        exe_path = _get_exe_path()
        backup_path = exe_path.with_suffix(".exe.bak")

        if backup_path.exists():
            backup_path.unlink()
        shutil.move(str(exe_path), str(backup_path))

        if downloaded_path.name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(downloaded_path, "r") as zf:
                exe_names = [n for n in zf.namelist() if n.endswith(".exe")]
                if exe_names:
                    zf.extract(exe_names[0], exe_path.parent)
                    extracted = exe_path.parent / exe_names[0]
                    if extracted != exe_path:
                        shutil.move(str(extracted), str(exe_path))
        else:
            shutil.copy2(str(downloaded_path), str(exe_path))

        _set_state(status="installed", installed_version=state.get("latest_version"))
        logger.info(f"Update installed: {exe_path}")

        _schedule_restart(str(exe_path), str(backup_path))

        return {"ok": True, "message": "Update installed, restarting..."}

    except Exception as e:
        logger.exception("Update install failed")
        if backup_path.exists() and not exe_path.exists():
            shutil.move(str(backup_path), str(exe_path))
        _set_state(status="error", error_message=str(e))
        return {"ok": False, "reason": str(e)}


def install_on_quit():
    """Called during app shutdown. If update is downloaded, install it silently.
    Mirrors electron-updater's autoInstallOnAppQuit=true."""
    if not _auto_install_on_quit:
        return
    state = get_state()
    if state.get("status") != "downloaded":
        return
    if not is_frozen():
        return
    logger.info("Auto-installing update on quit...")
    install_update()


# ---------------------------------------------------------------------------
# Process management (like Veo Farm's killChildren)
# ---------------------------------------------------------------------------

def _kill_all_children():
    """Kill all child processes (Huey worker, ffmpeg, etc.) before restarting."""
    import signal
    pid = os.getpid()
    logger.info(f"Killing all child processes of PID {pid}")

    if sys.platform == "win32":
        import subprocess
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def _schedule_restart(exe_path: str, backup_path: str):
    """Kill children, launch a detached restarter, then exit.
    The restarter waits for us to die, cleans up, and starts the new exe."""
    import subprocess

    _kill_all_children()

    our_pid = os.getpid()

    if sys.platform == "win32":
        script = f'''@echo off
echo Waiting for Youtok (PID {our_pid}) to exit...
:wait
tasklist /FI "PID eq {our_pid}" 2>NUL | find "{our_pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
echo Process exited. Cleaning up...
taskkill /F /IM youtok.exe 2>nul
del "{backup_path}" 2>nul
echo Starting new version...
start "" "{exe_path}"
del "%~f0"
'''
        bat = Path(tempfile.gettempdir()) / "youtok_restart.bat"
        bat.write_text(script)
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        subprocess.Popen(
            [
                "bash", "-c",
                f'while kill -0 {our_pid} 2>/dev/null; do sleep 1; done; '
                f'rm -f "{backup_path}"; "{exe_path}" &',
            ],
            start_new_session=True,
            close_fds=True,
        )

    import signal
    logger.info("Shutting down for update...")
    os.kill(our_pid, signal.SIGTERM)


# ---------------------------------------------------------------------------
# Startup scheduler (like Veo Farm's setupAutoUpdate)
# ---------------------------------------------------------------------------

def start_update_scheduler():
    """Call on app startup. Checks for update immediately, then every 30 min.
    Mirrors Veo Farm: checkForUpdatesAndNotify() + setInterval(30min)."""
    def _periodic_check():
        global _check_timer
        try:
            check_for_update(auto_download=True)
        except Exception:
            pass
        _check_timer = threading.Timer(CHECK_INTERVAL_SEC, _periodic_check)
        _check_timer.daemon = True
        _check_timer.start()

    threading.Thread(target=_periodic_check, daemon=True).start()
    logger.info("Update scheduler started (check on launch + every 30 min)")
