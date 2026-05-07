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


def _find_cached(video_id: str, work_dir: Path) -> tuple[Path, Path] | None:
    """Search sibling workdirs for existing mp4+wav of same video."""
    parent = work_dir.parent
    if not parent.exists():
        return None
    for sibling in parent.iterdir():
        if sibling == work_dir or not sibling.is_dir():
            continue
        mp4 = sibling / f"{video_id}.mp4"
        wav = sibling / f"{video_id}.wav"
        if mp4.exists() and wav.exists():
            return mp4, wav
    return None


def download_video(url: str, work_dir: Path) -> DownloadResult:
    work_dir.mkdir(parents=True, exist_ok=True)

    meta = subprocess.run(
        [str(settings.ytdlp), "--dump-json", "--no-playlist", url],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    info = json.loads(meta.stdout)
    video_id = info["id"]
    title = info["title"]
    duration = float(info.get("duration", 0))
    channel = info.get("channel")

    video_path = work_dir / f"{video_id}.mp4"
    audio_path = work_dir / f"{video_id}.wav"

    if video_path.exists() and audio_path.exists():
        return DownloadResult(
            video_path=video_path,
            audio_path=audio_path,
            title=title,
            video_id=video_id,
            duration_sec=duration,
            channel_name=channel,
        )

    cached = _find_cached(video_id, work_dir)
    if cached:
        shutil.copy2(cached[0], video_path)
        shutil.copy2(cached[1], audio_path)
        return DownloadResult(
            video_path=video_path,
            audio_path=audio_path,
            title=title,
            video_id=video_id,
            duration_sec=duration,
            channel_name=channel,
        )

    _check_disk_space(work_dir)

    output_template = str(work_dir / f"{video_id}.%(ext)s")
    # Prefer H.264 (avc1) over AV1: most consumer GPUs have hardware decoders for
    # H.264 + HEVC but not AV1. Decoding AV1 on CPU makes scene detect take 4×
    # longer (PySceneDetect must decode every frame). H.264 file is ~30% larger
    # but the decode + scene-detect saving dwarfs that. Fallback chain:
    #   1) AVC1 (H.264) ≤1080p  — fast HW decode
    #   2) any mp4 ≤1080p       — usually AV1, still works
    #   3) any best ≤1080p      — last resort
    subprocess.run([
        str(settings.ytdlp),
        "-f",
        "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "best[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        url,
    ], check=True)

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
