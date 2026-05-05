from datetime import datetime

from sqlalchemy.orm import Session

from youtok.db.models import Clip, Job, License


def create_license(db: Session, **kwargs) -> License:
    lic = License(**kwargs)
    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic


def get_license_by_hash(db: Session, key_hash: str) -> License | None:
    return db.query(License).filter(License.key_hash == key_hash).first()


def get_active_license(db: Session) -> License | None:
    return db.query(License).filter(License.status == "active").first()


def create_job(db: Session, **kwargs) -> Job:
    job = Job(**kwargs)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: int) -> Job | None:
    return db.query(Job).filter(Job.id == job_id).first()


def list_jobs(db: Session, limit: int = 50) -> list[Job]:
    return db.query(Job).order_by(Job.created_at.desc()).limit(limit).all()


def update_job_progress(
    db: Session, job_id: int, status: str, progress_pct: int, current_step: str | None = None
) -> None:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        job.status = status
        job.progress_pct = progress_pct
        job.current_step = current_step
        if status == "done":
            job.finished_at = datetime.utcnow()
        db.commit()


def create_clip(db: Session, **kwargs) -> Clip:
    clip = Clip(**kwargs)
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return clip


def get_clips_for_job(db: Session, job_id: int) -> list[Clip]:
    return db.query(Clip).filter(Clip.job_id == job_id).order_by(Clip.part_number).all()


def get_stats(db: Session) -> dict:
    total = db.query(Job).count()
    running = db.query(Job).filter(Job.status.notin_(["done", "failed", "pending"])).count()
    done = db.query(Job).filter(Job.status == "done").count()
    failed = db.query(Job).filter(Job.status == "failed").count()
    return {"total": total, "running": running, "done": done, "failed": failed}


def delete_job(db: Session, job_id: int) -> None:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        db.query(Clip).filter(Clip.job_id == job_id).delete()
        db.delete(job)
        db.commit()
