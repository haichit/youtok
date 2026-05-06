"""Download yt-dlp + ffmpeg + ffprobe (Windows x64) into vendor/ for the
PyInstaller build to bundle. Idempotent — skips files that already exist.

Run before `pyinstaller youtok.spec`.

Layout produced:
    vendor/
      yt-dlp/yt-dlp.exe
      ffmpeg/ffmpeg.exe
      ffmpeg/ffprobe.exe
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
# gyan.dev "release-essentials": static, GPL, ffmpeg+ffprobe only — ~75MB zip
# (BtbN's full GPL build is ~400MB and bundles codecs we don't need.)
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  -> {url}")
    print(f"     {dest}")
    with urllib.request.urlopen(url) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)


def fetch_ytdlp() -> None:
    target = VENDOR / "yt-dlp" / "yt-dlp.exe"
    if target.exists():
        print(f"[skip] {target.relative_to(ROOT)} already exists")
        return
    print("[fetch] yt-dlp")
    _download(YTDLP_URL, target)


def fetch_ffmpeg() -> None:
    ff = VENDOR / "ffmpeg" / "ffmpeg.exe"
    fp = VENDOR / "ffmpeg" / "ffprobe.exe"
    if ff.exists() and fp.exists():
        print(f"[skip] vendor/ffmpeg/{{ffmpeg,ffprobe}}.exe already exist")
        return
    print("[fetch] ffmpeg + ffprobe (BtbN release zip)")
    print(f"  -> {FFMPEG_ZIP_URL}")
    with urllib.request.urlopen(FFMPEG_ZIP_URL) as r:
        data = r.read()
    print(f"  zip downloaded: {len(data) / 1024**2:.1f} MB")
    extracted = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                target = VENDOR / "ffmpeg" / base
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                print(f"     extracted -> {target.relative_to(ROOT)}")
                extracted += 1
    if extracted < 2:
        sys.exit(f"ffmpeg zip did not contain both binaries (got {extracted})")


def main() -> None:
    if sys.platform != "win32":
        print("This script only fetches Windows x64 binaries.")
        sys.exit(1)
    VENDOR.mkdir(exist_ok=True)
    fetch_ytdlp()
    fetch_ffmpeg()
    print("done.")


if __name__ == "__main__":
    main()
