import os
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from youtok.api.deps import check_license_or_redirect
from youtok.db import crud
from youtok.db.base import get_db
from youtok.db.models import Job
from youtok.queue.tasks import process_job

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")
from youtok.llm.fx import fmt_vnd, get_usd_vnd_rate
templates.env.globals["fmt_vnd"] = fmt_vnd
templates.env.globals["get_usd_vnd_rate"] = get_usd_vnd_rate


def _push_recent_output_dir(db, path: str, max_items: int = 5):
    """Push path to front of recent_output_dirs list, dedupe, cap. Backward-compat: also keep last_output_dir."""
    import json as _json
    raw = crud.get_setting(db, "recent_output_dirs") or "[]"
    try:
        items = _json.loads(raw) or []
    except Exception:
        items = []
    # Dedupe: remove existing occurrence, then put at front
    items = [p for p in items if p != path]
    items.insert(0, path)
    items = items[:max_items]
    crud.set_setting(db, "recent_output_dirs", _json.dumps(items))
    crud.set_setting(db, "last_output_dir", path)  # legacy key still updated


@router.get("/")
async def list_jobs_partial(
    request: Request,
    partial: int = 0,
    filter: str = "",
    page: int = 1,
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    from datetime import datetime, timedelta
    from youtok.llm.cost_tracker import get_total_cost_for_job
    valid = {"", "running", "done", "failed", "pending"}
    f = filter if filter in valid else ""
    df = dt = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
    except ValueError:
        df = None
    try:
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        dt = None
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
    all_filtered = crud.list_all_filtered_jobs(db, status_filter=f or None, date_from=df, date_to=dt)
    total_cost = round(sum(get_total_cost_for_job(j.id) for j in all_filtered), 4)
    if partial:
        return templates.TemplateResponse(request, "partials/job_table.html", context={
            "jobs": jobs, "costs": costs, "page": page, "pages": pages,
            "total": total, "active_filter": f,
            "total_cost": total_cost,
            "date_from": date_from, "date_to": date_to,
        })
    raise HTTPException(404)


@router.post("/bulk-delete")
async def bulk_delete(ids: str = Form(...), db: Session = Depends(get_db)):
    deleted = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            jid = int(raw)
        except ValueError:
            continue
        crud.delete_job(db, jid)
        deleted.append(jid)
    return {"ok": True, "deleted": deleted}


@router.post("/{job_id}/rerun")
async def rerun_job(job_id: int, db: Session = Depends(get_db)):
    old = db.get(Job, job_id)
    if not old:
        raise HTTPException(404, "Job not found")
    license = crud.get_active_license(db)
    if not license:
        raise HTTPException(400, "No active license")
    new_job = crud.create_job(
        db,
        license_id=license.id,
        source_type=old.source_type,
        source_url=old.source_url,
        output_dir=old.output_dir,
        config_json=old.config_json,
    )
    process_job(new_job.id)
    return {"ok": True, "new_job_id": new_job.id}


@router.post("/")
async def create_job(
    source_url: str = Form(...),
    output_dir: str = Form(...),
    provider: str = Form(""),
    logo_id: str = Form(""),
    auto_upload_drive: str = Form(""),
    db: Session = Depends(get_db),
):
    from youtok.llm.providers import PROVIDER_DEFAULTS
    import json as _json
    license = crud.get_active_license(db)
    if not license:
        return RedirectResponse("/activate", status_code=303)
    if provider and provider in PROVIDER_DEFAULTS and crud.get_api_key(db, provider):
        crud.set_setting(db, "active_provider", provider)
        crud.set_setting(db, "last_chosen_provider", provider)
    config = {}
    if logo_id and logo_id.isdigit():
        config["logo_id"] = int(logo_id)
    if auto_upload_drive == "1":
        config["auto_upload_drive"] = True
    job = crud.create_job(
        db,
        license_id=license.id,
        source_type="video",
        source_url=source_url,
        output_dir=output_dir,
        config_json=_json.dumps(config) if config else "{}",
    )
    _push_recent_output_dir(db, output_dir)
    process_job(job.id)
    return RedirectResponse(
        f"/jobs/{job.id}", status_code=303,
        headers={"HX-Redirect": f"/jobs/{job.id}"},
    )


@router.post("/bulk")
async def create_bulk(
    urls: str = Form(...),
    output_dir: str = Form(...),
    provider: str = Form(""),
    logo_id: str = Form(""),
    auto_upload_drive: str = Form(""),
    db: Session = Depends(get_db),
):
    from youtok.llm.providers import PROVIDER_DEFAULTS
    import json as _json
    license = crud.get_active_license(db)
    if not license:
        return RedirectResponse("/activate", status_code=303)
    if provider and provider in PROVIDER_DEFAULTS and crud.get_api_key(db, provider):
        crud.set_setting(db, "active_provider", provider)
        crud.set_setting(db, "last_chosen_provider", provider)
    config = {}
    if logo_id and logo_id.isdigit():
        config["logo_id"] = int(logo_id)
    if auto_upload_drive == "1":
        config["auto_upload_drive"] = True
    config_str = _json.dumps(config) if config else "{}"
    _push_recent_output_dir(db, output_dir)
    for url in urls.splitlines():
        url = url.strip()
        if not url:
            continue
        job = crud.create_job(
            db,
            license_id=license.id,
            source_type="bulk",
            source_url=url,
            output_dir=output_dir,
            config_json=config_str,
        )
        process_job(job.id)
    return RedirectResponse(
        "/dashboard", status_code=303,
        headers={"HX-Redirect": "/dashboard"},
    )


@router.post("/channel")
async def create_from_channel(
    channel_url: str = Form(...),
    selected_video_urls: list[str] = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = crud.get_active_license(db)
    if not license:
        return RedirectResponse("/activate", status_code=303)
    parent = crud.create_job(
        db,
        license_id=license.id,
        source_type="channel",
        source_url=channel_url,
        output_dir=output_dir,
    )
    for url in selected_video_urls:
        child = crud.create_job(
            db,
            license_id=license.id,
            parent_job_id=parent.id,
            source_type="video",
            source_url=url,
            output_dir=output_dir,
        )
        process_job(child.id)
    return RedirectResponse(
        "/dashboard", status_code=303,
        headers={"HX-Redirect": "/dashboard"},
    )


@router.delete("/{job_id}")
async def delete_job(job_id: int, db: Session = Depends(get_db)):
    crud.delete_job(db, job_id)
    return JSONResponse({"ok": True})


@router.get("/{job_id}/progress")
async def job_progress(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "step": job.current_step or job.status,
        "pct": job.progress_pct,
        "status": job.status,
        "message": job.error_message or "",
    }


@router.get("/{job_id}/clips")
async def clips_partial(job_id: int, request: Request, db: Session = Depends(get_db)):
    from youtok.llm.cost_tracker import get_total_cost_for_job
    clips = crud.get_clips_for_job(db, job_id)
    total_cost = get_total_cost_for_job(job_id)
    per_clip = round(total_cost / len(clips), 4) if clips else 0.0
    return templates.TemplateResponse(request, "partials/clip_grid.html", context={
        "clips": clips,
        "total_cost": round(total_cost, 4),
        "per_clip_cost": per_clip,
    })


@router.get("/{job_id}/manifest")
async def manifest_file(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    manifest_path = Path(job.output_dir) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Manifest not found")
    return FileResponse(manifest_path, media_type="application/json")


def _job_clips_root(job: Job) -> Path | None:
    """Locate the actual `clips/` folder for a job. Output layout is
    `<output_dir>/<slug-title>_<videoid>/clips/`. Returns None if not found."""
    base = Path(job.output_dir)
    if not base.exists():
        return None
    # First-level subdir(s) — pick the one whose name ends with the video_id
    candidates = [p for p in base.iterdir() if p.is_dir() and (p / "clips").is_dir()]
    if not candidates:
        return None
    # If multiple (rare — same output_dir reused), pick newest
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] / "clips"


@router.get("/{job_id}/download")
async def download_all_clips(job_id: int, db: Session = Depends(get_db)):
    """Zip all output clips for a job and stream the zip back."""
    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "done":
        raise HTTPException(400, f"Job not done yet (status={job.status})")

    clips_dir = _job_clips_root(job)
    if not clips_dir or not clips_dir.exists():
        raise HTTPException(404, "Clips directory not found on disk")

    mp4_files = sorted(clips_dir.glob("*.mp4"))
    if not mp4_files:
        raise HTTPException(404, "No clip files found")

    # Build zip in memory (clips are small enough)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in mp4_files:
            zf.write(f, arcname=f.name)
        # Include manifest.json if present
        manifest = clips_dir.parent / "manifest.json"
        if manifest.exists():
            zf.write(manifest, arcname="manifest.json")
    buf.seek(0)

    safe_title = (job.video_title or f"job-{job_id}").replace("/", "-").replace(" ", "_")[:80]
    filename = f"{safe_title}_clips.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}/download/{part_number}")
async def download_one_clip(job_id: int, part_number: int, db: Session = Depends(get_db)):
    """Download a single clip mp4 file by its part number (1-based)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    from youtok.db.models import Clip as ClipModel
    clip = db.query(ClipModel).filter(
        ClipModel.job_id == job_id, ClipModel.part_number == part_number
    ).first()
    if not clip:
        raise HTTPException(404, "Clip not found")

    p = Path(clip.output_path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {p.name}")

    return FileResponse(p, media_type="video/mp4", filename=p.name)
