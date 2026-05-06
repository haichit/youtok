from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from youtok.api.deps import check_license_or_redirect
from youtok.db import crud
from youtok.db.base import get_db
from youtok.db.models import Job
from youtok.license.manager import is_activated

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")
from youtok.llm.fx import fmt_vnd, get_usd_vnd_rate
templates.env.globals["fmt_vnd"] = fmt_vnd
templates.env.globals["get_usd_vnd_rate"] = get_usd_vnd_rate


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_activated():
        return RedirectResponse("/activate")
    return RedirectResponse("/dashboard")


def _parse_date_range(date_from: str, date_to: str):
    from datetime import datetime, timedelta
    df = dt = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
    except ValueError:
        df = None
    try:
        if date_to:
            # exclusive upper-bound: include the entire 'date_to' day
            dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        dt = None
    return df, dt


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    filter: str = "",
    page: int = 1,
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    if redirect := check_license_or_redirect():
        return redirect
    from youtok.llm.cost_tracker import get_total_cost_for_job
    valid = {"", "running", "done", "failed", "pending"}
    f = filter if filter in valid else ""
    df, dt = _parse_date_range(date_from, date_to)
    page = max(1, page)
    per_page = 50
    offset = (page - 1) * per_page
    jobs = crud.list_jobs(
        db, limit=per_page, offset=offset,
        status_filter=f or None, date_from=df, date_to=dt,
    )
    total = crud.count_jobs(db, status_filter=f or None, date_from=df, date_to=dt)
    pages = max(1, (total + per_page - 1) // per_page)
    costs = {j.id: round(get_total_cost_for_job(j.id), 4) for j in jobs}
    # Total cost across ALL filtered jobs (not just current page)
    all_filtered = crud.list_all_filtered_jobs(db, status_filter=f or None, date_from=df, date_to=dt)
    total_cost = round(sum(get_total_cost_for_job(j.id) for j in all_filtered), 4)
    stats = crud.get_stats(db)
    return templates.TemplateResponse(request, "dashboard.html", context={
        "jobs": jobs, "stats": stats, "active_filter": f,
        "costs": costs, "page": page, "pages": pages, "total": total,
        "total_cost": total_cost,
        "date_from": date_from, "date_to": date_to,
    })


@router.get("/fs/browse")
async def fs_browse(path: str = ""):
    if not path:
        path = str(Path.home())
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except Exception as e:
        raise HTTPException(400, f"Invalid path: {e}")
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, f"Not a directory: {p}")
    try:
        entries = []
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                entries.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": entries, "home": str(Path.home())}


@router.post("/fs/mkdir")
async def fs_mkdir(path: str = Form(...)):
    p = Path(path).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Cannot create folder: {e}")
    return {"ok": True, "path": str(p.resolve())}


@router.post("/fs/open")
async def fs_open(path: str = Form(...)):
    """Open a folder or file in the OS file manager (Finder on macOS, Explorer on Windows)."""
    import platform
    import subprocess
    p = Path(path).expanduser()
    if not p.exists():
        raise HTTPException(404, f"Not found: {p}")
    try:
        sysname = platform.system()
        if sysname == "Darwin":
            subprocess.run(["open", str(p)], check=False)
        elif sysname == "Windows":
            subprocess.run(["explorer", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception as e:
        raise HTTPException(500, f"Cannot open: {e}")
    return {"ok": True}


@router.get("/fs/file")
async def fs_file(path: str):
    """Stream a local file (used to preview video clips inline)."""
    from fastapi.responses import FileResponse
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"Not a file: {p}")
    return FileResponse(p)


@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    if redirect := check_license_or_redirect():
        return redirect
    from youtok.llm.cost_tracker import get_history_summary
    from youtok.llm.providers import PROVIDER_DEFAULTS
    summary = get_history_summary(date_from=date_from or None, date_to=date_to or None)
    return templates.TemplateResponse(request, "history.html", context={
        "summary": summary,
        "provider_defaults": PROVIDER_DEFAULTS,
        "date_from": date_from,
        "date_to": date_to,
    })


@router.get("/jobs/new", response_class=HTMLResponse)
async def jobs_new(request: Request, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    import json as _json
    from youtok.llm.providers import PROVIDER_CHOICES, PROVIDER_DEFAULTS

    # Recent output folders — list of last 5 user-submitted paths (most recent first)
    recent_raw = crud.get_setting(db, "recent_output_dirs") or "[]"
    try:
        recent_dirs = _json.loads(recent_raw) or []
    except Exception:
        recent_dirs = []
    # Filter out paths that no longer exist on disk (auto-cleanup stale entries)
    recent_dirs = [p for p in recent_dirs if Path(p).expanduser().exists()]

    # Default value: most recent existing folder; fallback to ~/Videos/Youtok
    if recent_dirs:
        default_dir = recent_dirs[0]
    else:
        default_dir = str(Path.home() / "Videos" / "Youtok")

    chosen = crud.get_setting(db, "last_chosen_provider") or crud.get_active_provider(db)
    providers_with_keys = [(pid, label) for pid, label in PROVIDER_CHOICES if crud.get_api_key(db, pid)]
    logos = crud.list_logos(db)
    drive_connected = crud.get_drive_token(db) is not None
    return templates.TemplateResponse(request, "new_job.html", context={
        "default_output_dir": default_dir,
        "recent_output_dirs": recent_dirs,
        "provider_choices": PROVIDER_CHOICES,
        "providers_with_keys": providers_with_keys,
        "provider_defaults": PROVIDER_DEFAULTS,
        "chosen_provider": chosen,
        "logos": logos,
        "drive_connected": drive_connected,
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/dashboard")
    clips = crud.get_clips_for_job(db, job_id)
    drive_token = crud.get_drive_token(db)
    drive_upload = crud.get_drive_upload(db, job_id)
    return templates.TemplateResponse(request, "job_detail.html", context={
        "job": job, "clips": clips,
        "drive_connected": drive_token is not None,
        "drive_upload": drive_upload,
    })
