from datetime import datetime

from sqlalchemy.orm import Session

from youtok.db.models import ApiKey, Clip, DriveToken, DriveUpload, Job, License, Logo, Setting


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


def _apply_status_filter(q, status_filter: str | None):
    if status_filter == "running":
        return q.filter(Job.status.notin_(["done", "failed", "pending"]))
    elif status_filter == "done":
        return q.filter(Job.status == "done")
    elif status_filter == "failed":
        return q.filter(Job.status == "failed")
    elif status_filter == "pending":
        return q.filter(Job.status == "pending")
    return q


def _apply_date_filter(q, date_from: datetime | None, date_to: datetime | None):
    if date_from:
        q = q.filter(Job.created_at >= date_from)
    if date_to:
        q = q.filter(Job.created_at < date_to)
    return q


def list_jobs(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    status_filter: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[Job]:
    q = _apply_status_filter(db.query(Job), status_filter)
    q = _apply_date_filter(q, date_from, date_to)
    return q.order_by(Job.created_at.desc()).offset(offset).limit(limit).all()


def count_jobs(
    db: Session,
    status_filter: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    q = _apply_status_filter(db.query(Job), status_filter)
    q = _apply_date_filter(q, date_from, date_to)
    return q.count()


def list_all_filtered_jobs(
    db: Session,
    status_filter: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[Job]:
    """All matching jobs (no limit), used to compute total cost across the entire filtered set."""
    q = _apply_status_filter(db.query(Job), status_filter)
    q = _apply_date_filter(q, date_from, date_to)
    return q.all()


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


# --- API Key / Settings CRUD ---


class NoApiKeyError(Exception):
    pass


def get_api_key(db: Session, provider: str) -> ApiKey | None:
    return db.query(ApiKey).filter(ApiKey.provider == provider).first()


def upsert_api_key(db: Session, provider: str, key: str, stage_a_model=None, stage_b_model=None) -> ApiKey:
    existing = get_api_key(db, provider)
    if existing:
        existing.key = key
        if stage_a_model:
            existing.stage_a_model = stage_a_model
        if stage_b_model:
            existing.stage_b_model = stage_b_model
        existing.updated_at = datetime.utcnow()
        api_key = existing
    else:
        api_key = ApiKey(provider=provider, key=key, stage_a_model=stage_a_model, stage_b_model=stage_b_model)
        db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key


def get_setting(db: Session, key: str, default=None) -> str | None:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else default


def set_setting(db: Session, key: str, value: str):
    existing = db.query(Setting).filter(Setting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = datetime.utcnow()
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


# --- Drive CRUD ---


def get_drive_token(db: Session) -> DriveToken | None:
    return db.query(DriveToken).first()


def save_drive_token(db: Session, email: str, token_json: str) -> DriveToken:
    existing = db.query(DriveToken).first()
    if existing:
        existing.email = email
        existing.token_json = token_json
        existing.updated_at = datetime.utcnow()
    else:
        existing = DriveToken(email=email, token_json=token_json)
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def delete_drive_token(db: Session) -> None:
    db.query(DriveToken).delete()
    db.commit()


def create_drive_upload(db: Session, job_id: int) -> DriveUpload:
    upload = DriveUpload(job_id=job_id)
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def get_drive_upload(db: Session, job_id: int) -> DriveUpload | None:
    return db.query(DriveUpload).filter(DriveUpload.job_id == job_id).order_by(DriveUpload.id.desc()).first()


def update_drive_upload(db: Session, upload_id: int, **kwargs) -> None:
    upload = db.query(DriveUpload).filter(DriveUpload.id == upload_id).first()
    if upload:
        for k, v in kwargs.items():
            setattr(upload, k, v)
        db.commit()


# --- Logo CRUD ---


def create_logo(db: Session, name: str, top_file_path: str, bottom_file_path: str) -> Logo:
    logo = Logo(name=name, top_file_path=top_file_path, bottom_file_path=bottom_file_path)
    db.add(logo)
    db.commit()
    db.refresh(logo)
    return logo


def list_logos(db: Session) -> list[Logo]:
    return db.query(Logo).order_by(Logo.created_at.desc()).all()


def get_logo(db: Session, logo_id: int) -> Logo | None:
    return db.query(Logo).filter(Logo.id == logo_id).first()


def delete_logo(db: Session, logo_id: int) -> bool:
    logo = db.query(Logo).filter(Logo.id == logo_id).first()
    if not logo:
        return False
    db.delete(logo)
    db.commit()
    return True


def get_active_provider(db: Session) -> str:
    return get_setting(db, "active_provider", "anthropic")


def get_active_provider_config(db: Session) -> dict:
    provider = get_active_provider(db)
    api_key_row = get_api_key(db, provider)
    if not api_key_row or not api_key_row.key:
        raise NoApiKeyError(f"No API key configured for provider '{provider}'")

    from youtok.llm.providers import PROVIDER_DEFAULTS
    defaults = PROVIDER_DEFAULTS[provider]
    return {
        "provider": provider,
        "api_key": api_key_row.key,
        "stage_a_model": api_key_row.stage_a_model or defaults["stage_a"],
        "stage_b_model": api_key_row.stage_b_model or defaults["stage_b"],
        "supports_caching": defaults["supports_caching"],
    }
