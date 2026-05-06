from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")


def _normalize_channel_url(url: str) -> str:
    url = url.rstrip("/")
    if "/@" in url and not url.endswith("/videos"):
        url += "/videos"
    elif "/channel/" in url and not url.endswith("/videos"):
        url += "/videos"
    elif "/c/" in url and not url.endswith("/videos"):
        url += "/videos"
    return url


def _fetch_channel_videos(channel_url: str, min_dur: int, max_dur: int, limit: int) -> list[dict]:
    import yt_dlp

    channel_url = _normalize_channel_url(channel_url)
    logger.info(f"Fetching channel videos: {channel_url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": limit * 3,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if not info:
        return []

    entries = info.get("entries") or []
    videos = []
    for entry in entries:
        if not entry:
            continue
        duration = entry.get("duration") or 0
        if duration < min_dur or duration > max_dur:
            continue
        raw_date = entry.get("upload_date") or ""
        if len(raw_date) == 8:
            upload_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        else:
            upload_date = raw_date

        video_id = entry.get("id", "")
        videos.append({
            "url": f"https://youtube.com/watch?v={video_id}" if video_id else entry.get("url", ""),
            "title": entry.get("title", "Untitled"),
            "duration_sec": duration,
            "upload_date": upload_date,
        })
        if len(videos) >= limit:
            break

    return videos


@router.post("/preview")
async def preview_channel(
    request: Request,
    channel_url: str = Form(...),
    min_duration_sec: int = Form(0),
    max_duration_sec: int = Form(99999),
    limit: int = Form(50),
):
    import asyncio

    channel_url = channel_url.strip()
    if not channel_url:
        raise HTTPException(400, "Channel URL is required")

    try:
        loop = asyncio.get_event_loop()
        videos = await loop.run_in_executor(
            None, _fetch_channel_videos, channel_url, min_duration_sec, max_duration_sec, limit,
        )
    except Exception as e:
        logger.exception(f"Failed to fetch channel: {channel_url}")
        raise HTTPException(400, f"Could not fetch channel: {e}")

    return templates.TemplateResponse(request, "partials/channel_preview.html", context={
        "videos": videos, "channel_url": channel_url,
    })
