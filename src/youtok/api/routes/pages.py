from fastapi import APIRouter, Depends, Request
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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_activated():
        return RedirectResponse("/activate")
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    jobs = crud.list_jobs(db, limit=50)
    stats = crud.get_stats(db)
    return templates.TemplateResponse(request, "dashboard.html", context={
        "jobs": jobs, "stats": stats,
    })


@router.get("/jobs/new", response_class=HTMLResponse)
async def jobs_new(request: Request):
    if redirect := check_license_or_redirect():
        return redirect
    return templates.TemplateResponse(request, "new_job.html")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/dashboard")
    clips = crud.get_clips_for_job(db, job_id)
    return templates.TemplateResponse(request, "job_detail.html", context={
        "job": job, "clips": clips,
    })
