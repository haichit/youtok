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


@router.get("/")
async def list_jobs_partial(request: Request, partial: int = 0, db: Session = Depends(get_db)):
    jobs = crud.list_jobs(db, limit=50)
    if partial:
        return templates.TemplateResponse(request, "partials/job_table.html", context={"jobs": jobs})
    raise HTTPException(404)


@router.post("/")
async def create_job(
    source_url: str = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = crud.get_active_license(db)
    if not license:
        return RedirectResponse("/activate", status_code=303)
    job = crud.create_job(
        db,
        license_id=license.id,
        source_type="video",
        source_url=source_url,
        output_dir=output_dir,
    )
    process_job(job.id)
    return RedirectResponse(
        f"/jobs/{job.id}", status_code=303,
        headers={"HX-Redirect": f"/jobs/{job.id}"},
    )


@router.post("/bulk")
async def create_bulk(
    urls: str = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = crud.get_active_license(db)
    if not license:
        return RedirectResponse("/activate", status_code=303)
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


@router.get("/{job_id}/clips")
async def clips_partial(job_id: int, request: Request, db: Session = Depends(get_db)):
    clips = crud.get_clips_for_job(db, job_id)
    return templates.TemplateResponse(request, "partials/clip_grid.html", context={"clips": clips})


@router.get("/{job_id}/manifest")
async def manifest_file(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    manifest_path = Path(job.output_dir) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Manifest not found")
    return FileResponse(manifest_path, media_type="application/json")
