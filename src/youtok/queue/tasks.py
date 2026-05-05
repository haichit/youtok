import os
import time
from datetime import datetime

from loguru import logger

from youtok.db.base import SessionLocal
from youtok.db.models import Job
from youtok.queue.huey_app import huey

MOCK_PIPELINE = os.environ.get("YOUTOK_MOCK_PIPELINE", "0") == "1"


def _mock_pipeline(job_id: int, progress_callback):
    """10-second fake pipeline for dev testing. Does NOT call Anthropic API."""
    steps = [
        ("downloading", 10, "Downloading video..."),
        ("downloading", 15, "Download complete"),
        ("transcribing", 25, "Transcribing audio..."),
        ("transcribing", 40, "12 sentences found"),
        ("segmenting", 50, "Stage A — outline"),
        ("segmenting", 60, "3 clips planned"),
        ("snapping", 65, "Cuts snapped"),
        ("cutting", 75, "Rendering clip 1/3"),
        ("cutting", 85, "Rendering clip 2/3"),
        ("cutting", 95, "Rendering clip 3/3"),
        ("done", 100, "Complete"),
    ]
    for step, pct, msg in steps:
        time.sleep(1)
        progress_callback(step, pct, msg)


def _get_run_pipeline():
    if MOCK_PIPELINE:
        logger.info("Using MOCK pipeline (YOUTOK_MOCK_PIPELINE=1)")
        return _mock_pipeline
    from youtok.core.pipeline import run_pipeline
    return run_pipeline


@huey.task(retries=0, retry_delay=0)
def process_job(job_id: int):
    logger.info(f"Worker picked up job {job_id}")

    def progress_callback(step: str, pct: int, message: str = ""):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.current_step = step
                job.progress_pct = pct
                if message:
                    logger.info(f"job {job_id} | {step} {pct}% | {message}")
                db.commit()

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.error(f"Job {job_id} not found in DB")
            return
        job.status = "downloading"
        job.started_at = datetime.utcnow()
        db.commit()

    try:
        pipeline_fn = _get_run_pipeline()
        pipeline_fn(job_id, progress_callback)
    except Exception as e:
        logger.exception(f"Job {job_id} failed")
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(e)[:500]
                job.finished_at = datetime.utcnow()
                db.commit()
        raise
    else:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job and job.status not in ("done", "failed"):
                job.status = "done"
                job.progress_pct = 100
                job.finished_at = datetime.utcnow()
                db.commit()
        update_parent_progress(job_id)


def update_parent_progress(child_job_id: int):
    """Roll up child progress to parent job (channel jobs)."""
    with SessionLocal() as db:
        child = db.get(Job, child_job_id)
        if not child or not child.parent_job_id:
            return
        parent = db.get(Job, child.parent_job_id)
        if not parent:
            return

        siblings = db.query(Job).filter(Job.parent_job_id == parent.id).all()
        if not siblings:
            return

        avg_pct = sum(s.progress_pct for s in siblings) // len(siblings)
        parent.progress_pct = avg_pct

        statuses = [s.status for s in siblings]
        if all(s in ("done", "failed") for s in statuses):
            parent.status = "failed" if any(s == "failed" for s in statuses) else "done"
            parent.finished_at = datetime.utcnow()
        else:
            parent.status = "running"

        db.commit()
