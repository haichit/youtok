import os
import threading
import time
from datetime import datetime

from loguru import logger

from youtok.db.base import SessionLocal
from youtok.db.models import Job
from youtok.queue.huey_app import huey

MOCK_PIPELINE = os.environ.get("YOUTOK_MOCK_PIPELINE", "0") == "1"

# Hard ceiling — Huey is started with --workers WORKER_HARD_LIMIT.
# Actual concurrent execution is gated by a dynamic semaphore that re-reads
# the `concurrent_jobs` setting on each job acquire (1..WORKER_HARD_LIMIT).
WORKER_HARD_LIMIT = 10
DEFAULT_CONCURRENT_JOBS = 1

_concurrency_lock = threading.Lock()
_concurrency_cond = threading.Condition(_concurrency_lock)
_active_jobs = 0


def _get_concurrent_jobs_limit() -> int:
    from youtok.db.crud import get_setting
    try:
        with SessionLocal() as db:
            raw = get_setting(db, "concurrent_jobs", str(DEFAULT_CONCURRENT_JOBS))
        n = int(raw) if raw else DEFAULT_CONCURRENT_JOBS
        return max(1, min(WORKER_HARD_LIMIT, n))
    except Exception:
        return DEFAULT_CONCURRENT_JOBS


def get_active_jobs_count() -> int:
    with _concurrency_lock:
        return _active_jobs


def _wait_for_slot(job_id: int):
    """Block until the number of active jobs is below the user-configured limit.
    Re-reads `concurrent_jobs` setting on every check, so changing it via UI takes effect for QUEUED jobs."""
    global _active_jobs
    with _concurrency_cond:
        while True:
            limit = _get_concurrent_jobs_limit()
            if _active_jobs < limit:
                _active_jobs += 1
                logger.info(f"Job {job_id} acquired slot ({_active_jobs}/{limit} active)")
                return
            logger.info(f"Job {job_id} waiting for slot ({_active_jobs}/{limit} active, queued)")
            # Wake up periodically to re-check setting (in case user lowered/raised limit)
            _concurrency_cond.wait(timeout=2.0)


def _release_slot(job_id: int):
    global _active_jobs
    with _concurrency_cond:
        _active_jobs = max(0, _active_jobs - 1)
        logger.info(f"Job {job_id} released slot ({_active_jobs} active)")
        _concurrency_cond.notify_all()


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

    # Gate on user-configured concurrency limit. This is INSIDE the task,
    # so Huey's thread pool can have many threads waiting; only N actually run.
    _wait_for_slot(job_id)

    def progress_callback(step: str, pct: int, message: str = ""):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                # `status` = broad category (downloading/transcribing/...) used by UI status badge
                # `current_step` = detailed user-facing message (Vietnamese description of current sub-step)
                job.status = step
                job.progress_pct = pct
                job.current_step = message or step
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
        _maybe_auto_upload_drive(job_id)
    finally:
        _release_slot(job_id)


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


def _maybe_auto_upload_drive(job_id: int):
    import json as _json
    from youtok.db.crud import get_drive_token, create_drive_upload
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status != "done":
            return
        try:
            config = _json.loads(job.config_json or "{}")
        except Exception:
            return
        if not config.get("auto_upload_drive"):
            return
        if not get_drive_token(db):
            logger.warning(f"Job {job_id} has auto_upload_drive but Drive not connected, skipping")
            return
        upload = create_drive_upload(db, job_id)
        logger.info(f"Auto-uploading job {job_id} to Drive (upload_id={upload.id})")
    upload_to_drive(upload.id)


@huey.task(retries=0, retry_delay=0)
def upload_to_drive(upload_id: int):
    from pathlib import Path
    from youtok.db.models import DriveUpload
    from youtok.db.crud import get_drive_token, update_drive_upload, save_drive_token
    from youtok.core.google_drive import upload_job_clips

    logger.info(f"Drive upload task started: upload_id={upload_id}")

    with SessionLocal() as db:
        upload = db.query(DriveUpload).filter(DriveUpload.id == upload_id).first()
        if not upload:
            logger.error(f"DriveUpload {upload_id} not found")
            return
        job = db.get(Job, upload.job_id)
        if not job:
            logger.error(f"Job {upload.job_id} not found for upload {upload_id}")
            return

        token_row = get_drive_token(db)
        if not token_row:
            update_drive_upload(db, upload_id, status="failed", error_message="Drive not connected")
            return

        token_json = token_row.token_json
        token_email = token_row.email
        job_id = job.id
        output_dir = job.output_dir
        video_title = job.video_title or f"job-{job_id}"

    clips_dir = Path(output_dir) / "clips"
    if not clips_dir.exists():
        for d in Path(output_dir).iterdir():
            if d.is_dir() and (d / "clips").is_dir():
                clips_dir = d / "clips"
                break

    if not clips_dir.exists():
        with SessionLocal() as db:
            update_drive_upload(db, upload_id, status="failed", error_message=f"Clips directory not found: {clips_dir}")
        return

    with SessionLocal() as db:
        update_drive_upload(db, upload_id, status="uploading", progress_pct=5)

    def progress_cb(current, total, filename):
        pct = int(10 + 85 * current / total)
        with SessionLocal() as db:
            update_drive_upload(db, upload_id, progress_pct=pct, current_file=filename)

    try:
        result = upload_job_clips(
            token_json=token_json,
            job_id=job_id,
            clips_dir=clips_dir,
            video_title=video_title,
            progress_callback=progress_cb,
        )
        with SessionLocal() as db:
            update_drive_upload(
                db, upload_id,
                status="done",
                progress_pct=100,
                drive_folder_id=result["folder_id"],
                drive_folder_url=result["folder_url"],
                finished_at=datetime.utcnow(),
            )
            if result.get("updated_token"):
                save_drive_token(db, email=token_email, token_json=result["updated_token"])
        logger.info(f"Drive upload done: {result['folder_url']}")
    except Exception as e:
        logger.exception(f"Drive upload failed: upload_id={upload_id}")
        with SessionLocal() as db:
            update_drive_upload(
                db, upload_id,
                status="failed",
                error_message=str(e)[:500],
                finished_at=datetime.utcnow(),
            )
