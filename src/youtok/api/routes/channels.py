from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")


@router.post("/preview")
async def preview_channel(
    request: Request,
    channel_url: str = Form(...),
    min_duration_sec: int = Form(0),
    max_duration_sec: int = Form(99999),
    limit: int = Form(50),
):
    videos = [
        {"url": "https://youtube.com/watch?v=mock1", "title": "Mock Video 1 — Introduction to Topic", "duration_sec": 600, "upload_date": "2026-04-01"},
        {"url": "https://youtube.com/watch?v=mock2", "title": "Mock Video 2 — Deep Dive Analysis", "duration_sec": 900, "upload_date": "2026-04-15"},
        {"url": "https://youtube.com/watch?v=mock3", "title": "Mock Video 3 — Advanced Techniques", "duration_sec": 450, "upload_date": "2026-05-01"},
    ]
    videos = [v for v in videos if min_duration_sec <= v["duration_sec"] <= max_duration_sec][:limit]
    return templates.TemplateResponse(request, "partials/channel_preview.html", context={
        "videos": videos, "channel_url": channel_url,
    })
