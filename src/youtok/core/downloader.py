import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from youtok.config import settings
from youtok.core.errors import InsufficientDiskSpace


class DownloadResult(BaseModel):
    video_path: Path
    audio_path: Path
    title: str
    video_id: str
    duration_sec: float
    channel_name: str | None


def _check_disk_space(path: Path, min_bytes: int = 5 * 1024**3) -> None:
    free = shutil.disk_usage(path).free
    if free < min_bytes:
        raise InsufficientDiskSpace(
            f"Need {min_bytes / 1024**3:.1f}GB free, only {free / 1024**3:.1f}GB available"
        )


def download_video(url: str, work_dir: Path) -> DownloadResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    _check_disk_space(work_dir)

    meta = subprocess.run(
        [str(settings.ytdlp), "--dump-json", "--no-playlist", url],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    info = json.loads(meta.stdout)
    video_id = info["id"]
    title = info["title"]
    duration = float(info.get("duration", 0))
    channel = info.get("channel")

    output_template = str(work_dir / f"{video_id}.%(ext)s")
    subprocess.run([
        str(settings.ytdlp),
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        url,
    ], check=True)

    video_path = work_dir / f"{video_id}.mp4"
    audio_path = work_dir / f"{video_id}.wav"

    subprocess.run([
        str(settings.ffmpeg), "-y",
        "-i", str(video_path),
        "-ar", "16000", "-ac", "1", "-vn",
        str(audio_path),
    ], check=True, capture_output=True)

    return DownloadResult(
        video_path=video_path,
        audio_path=audio_path,
        title=title,
        video_id=video_id,
        duration_sec=duration,
        channel_name=channel,
    )
