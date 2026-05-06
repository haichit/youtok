import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from youtok.api.deps import check_license_or_redirect
from youtok.config import settings as app_settings
from youtok.db.base import get_db
from youtok.db.crud import (
    create_logo, delete_logo, get_api_key, get_active_provider, list_logos,
    set_setting, upsert_api_key,
)
from youtok.llm.providers import PROVIDER_DEFAULTS, PROVIDER_CHOICES
from youtok.llm.client import test_provider_connection

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")
from youtok.llm.fx import fmt_vnd, get_usd_vnd_rate
templates.env.globals["fmt_vnd"] = fmt_vnd
templates.env.globals["get_usd_vnd_rate"] = get_usd_vnd_rate


@router.get("/", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect

    from youtok.llm.cost_tracker import get_usage_per_provider
    from youtok.queue.tasks import WORKER_HARD_LIMIT
    from youtok.db.crud import get_setting
    from youtok.db.models import Job
    active = get_active_provider(db)
    keys = {p[0]: get_api_key(db, p[0]) for p in PROVIDER_CHOICES}
    usage = get_usage_per_provider()

    concurrent_jobs = int(get_setting(db, "concurrent_jobs", "1") or "1")
    active_count = db.query(Job).filter(
        Job.status.notin_(["done", "failed", "pending"])
    ).count()

    logos = list_logos(db)

    from youtok.db.crud import get_drive_token
    from youtok.core.google_drive import has_credentials_file
    drive_token = get_drive_token(db)
    drive_status = {
        "has_credentials": has_credentials_file(),
        "connected": drive_token is not None,
        "email": drive_token.email if drive_token else None,
    }

    return templates.TemplateResponse(request, "settings.html", context={
        "active_provider": active,
        "keys": keys,
        "provider_choices": PROVIDER_CHOICES,
        "provider_defaults": PROVIDER_DEFAULTS,
        "usage": usage,
        "concurrent_jobs": concurrent_jobs,
        "concurrent_jobs_max": WORKER_HARD_LIMIT,
        "active_jobs_count": active_count,
        "logos": logos,
        "drive_status": drive_status,
    })


@router.post("/concurrent-jobs")
async def set_concurrent_jobs(value: int = Form(...), db: Session = Depends(get_db)):
    from youtok.queue.tasks import WORKER_HARD_LIMIT
    if value < 1 or value > WORKER_HARD_LIMIT:
        raise HTTPException(400, f"value must be 1..{WORKER_HARD_LIMIT}")
    set_setting(db, "concurrent_jobs", str(value))
    return {"ok": True, "concurrent_jobs": value}


@router.get("/active-jobs-count")
async def active_jobs_count(db: Session = Depends(get_db)):
    """Counts jobs whose status indicates active processing (cross-process safe via DB)."""
    from youtok.db.models import Job
    running = db.query(Job).filter(
        Job.status.notin_(["done", "failed", "pending"])
    ).count()
    return {"active": running}


@router.post("/save")
async def save_settings(
    provider: str = Form(...),
    api_key: str = Form(""),
    stage_a_model: str = Form(""),
    stage_b_model: str = Form(""),
    set_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, "Invalid provider")

    api_key = api_key.strip()

    # Reject obvious placeholder/mask values (bullets, asterisks, dots)
    if api_key and all(ch in "•*·●○◯⬤⭕•·●∘" for ch in api_key):
        raise HTTPException(400, "API key looks like a UI mask — please paste the real key")

    # Reject any non-ASCII characters (real API keys are always ASCII)
    if api_key and any(ord(ch) > 127 for ch in api_key):
        raise HTTPException(400, "API key contains non-ASCII characters — likely a copy/paste artifact")

    existing = get_api_key(db, provider)

    if not api_key:
        # Empty submit → keep existing key, only update model overrides + active flag
        if not existing:
            raise HTTPException(400, "No API key saved yet — please paste the key")
        upsert_api_key(
            db, provider,
            key=existing.key,  # preserve current key
            stage_a_model=stage_a_model or None,
            stage_b_model=stage_b_model or None,
        )
    else:
        upsert_api_key(
            db, provider,
            key=api_key,
            stage_a_model=stage_a_model or None,
            stage_b_model=stage_b_model or None,
        )

    if set_active:
        set_setting(db, "active_provider", provider)

    return {"ok": True, "provider": provider, "active": set_active}


@router.post("/test")
async def test_key(
    provider: str = Form(...),
    api_key: str = Form(...),
):
    ok, msg = test_provider_connection(provider, api_key)
    return {"ok": ok, "message": msg}


@router.post("/activate")
async def activate_provider(
    provider: str = Form(...),
    db: Session = Depends(get_db),
):
    if not get_api_key(db, provider):
        raise HTTPException(400, f"No API key configured for {provider}")
    set_setting(db, "active_provider", provider)
    return {"ok": True, "active": provider}


# --- Logo management ---

LOGO_TOP_W, LOGO_TOP_H = 1080, 150
LOGO_BOT_W, LOGO_BOT_H = 1080, 150


@router.post("/logos")
async def upload_logo(
    name: str = Form(...),
    top_file: UploadFile = File(...),
    bottom_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    for label, f in [("top", top_file), ("bottom", bottom_file)]:
        if not f.filename or not f.filename.lower().endswith(".png"):
            raise HTTPException(400, f"File {label} phải là PNG")

    prefix = uuid.uuid4().hex[:8]
    top_dest = app_settings.logos_dir / f"{prefix}_top.png"
    bot_dest = app_settings.logos_dir / f"{prefix}_bot.png"

    with open(top_dest, "wb") as f:
        shutil.copyfileobj(top_file.file, f)
    with open(bot_dest, "wb") as f:
        shutil.copyfileobj(bottom_file.file, f)

    logo = create_logo(
        db, name=name.strip(),
        top_file_path=str(top_dest),
        bottom_file_path=str(bot_dest),
    )
    return {"ok": True, "id": logo.id, "name": logo.name}


@router.delete("/logos/{logo_id}")
async def remove_logo(logo_id: int, db: Session = Depends(get_db)):
    from youtok.db.crud import get_logo
    from pathlib import Path
    logo = get_logo(db, logo_id)
    if not logo:
        raise HTTPException(404, "Logo not found")
    for fp in [logo.top_file_path, logo.bottom_file_path]:
        p = Path(fp)
        if p.exists():
            p.unlink()
    delete_logo(db, logo_id)
    return {"ok": True}


@router.get("/logos/{logo_id}/preview/{position}")
async def logo_preview(logo_id: int, position: str, db: Session = Depends(get_db)):
    from youtok.db.crud import get_logo
    from pathlib import Path
    if position not in ("top", "bottom"):
        raise HTTPException(400, "Position must be 'top' or 'bottom'")
    logo = get_logo(db, logo_id)
    if not logo:
        raise HTTPException(404, "Logo not found")
    fp = logo.top_file_path if position == "top" else logo.bottom_file_path
    p = Path(fp)
    if not p.exists():
        raise HTTPException(404, "Logo file not found on disk")
    return FileResponse(p, media_type="image/png")
