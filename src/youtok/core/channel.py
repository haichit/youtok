"""Channel / playlist enumeration via yt-dlp --flat-playlist."""
from __future__ import annotations

import json
import subprocess

from loguru import logger
from pydantic import BaseModel

from youtok.config import settings


class VideoMeta(BaseModel):
    video_id: str
    url: str
    title: str
    duration_sec: float | None = None
    upload_date: str | None = None  # YYYYMMDD


class ChannelFilters(BaseModel):
    min_duration_sec: int = 0
    max_duration_sec: int = 99999
    limit: int = 100


def enumerate_channel(url: str, filters: ChannelFilters) -> list[VideoMeta]:
    """Enumerate videos from a channel/playlist URL.

    Uses `yt-dlp --flat-playlist --dump-json -I 1:N <url>`.
    Each line of stdout is a JSON dict with metadata for one video.

    Note: --flat-playlist often does NOT populate `duration` — videos with
    `duration_sec=None` are kept (UI will show "? min"). Duration filtering
    only excludes videos where duration IS known and out of range.
    """
    cmd = [
        str(settings.ytdlp),
        "--flat-playlist",
        "--dump-json",
        "-I", f"1:{filters.limit}",
        url,
    ]
    logger.info(f"channel enumerate: {' '.join(cmd)}")

    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )

    videos: list[VideoMeta] = []
    for line in out.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"channel enumerate: skipping bad JSON line: {line[:80]}")
            continue
        videos.append(
            VideoMeta(
                video_id=info["id"],
                url=info.get("url")
                or info.get("webpage_url")
                or f"https://youtube.com/watch?v={info['id']}",
                title=info.get("title", ""),
                duration_sec=info.get("duration"),
                upload_date=info.get("upload_date"),
            )
        )

    filtered = [
        v
        for v in videos
        if v.duration_sec is None
        or filters.min_duration_sec <= v.duration_sec <= filters.max_duration_sec
    ]

    logger.info(
        f"channel enumerate: got {len(videos)} videos, {len(filtered)} after filter"
    )
    return filtered
