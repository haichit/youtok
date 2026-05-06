from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from youtok.db.base import get_db
from youtok.db.crud import (
    create_drive_upload,
    delete_drive_token,
    get_drive_token,
    get_drive_upload,
    save_drive_token,
    update_drive_upload,
)
from youtok.db.models import Job

router = APIRouter()


@router.get("/status")
async def drive_status(db: Session = Depends(get_db)):
    from youtok.core.google_drive import has_credentials_file, is_auth_running
    token = get_drive_token(db)
    return {
        "has_credentials": has_credentials_file(),
        "connected": token is not None,
        "email": token.email if token else None,
        "auth_running": is_auth_running(),
    }


@router.post("/connect")
async def drive_connect(db: Session = Depends(get_db)):
    from youtok.core.google_drive import has_credentials_file, start_auth_flow, is_auth_running
    if not has_credentials_file():
        raise HTTPException(400, "google_drive_credentials.json not found in assets/keys/")
    if is_auth_running():
        raise HTTPException(400, "Auth flow already in progress")
    start_auth_flow()
    return {"ok": True, "message": "Auth flow started — check your browser"}


@router.get("/auth-result")
async def drive_auth_result(db: Session = Depends(get_db)):
    import json
    from loguru import logger
    from youtok.core.google_drive import (
        get_auth_result, clear_auth_result, is_auth_running,
        build_credentials, get_user_email,
    )
    running = is_auth_running()
    if running:
        return {"status": "running"}

    result, error = get_auth_result()
    logger.debug(f"auth-result poll: result={'set' if result else 'None'}, error={error}")
    if error:
        clear_auth_result()
        return {"status": "failed", "error": error}
    if result is None:
        return {"status": "idle"}

    try:
        token_json = json.dumps(result)
        creds = build_credentials(token_json)
        email = get_user_email(creds)
        save_drive_token(db, email=email, token_json=token_json)
        clear_auth_result()
        logger.info(f"Drive token saved for {email}")
        return {"status": "done", "email": email}
    except Exception as e:
        logger.exception("Failed to save drive token")
        clear_auth_result()
        return {"status": "failed", "error": str(e)}


@router.post("/disconnect")
async def drive_disconnect(db: Session = Depends(get_db)):
    delete_drive_token(db)
    return {"ok": True}


@router.post("/upload/{job_id}")
async def start_upload(job_id: int, db: Session = Depends(get_db)):
    token = get_drive_token(db)
    if not token:
        raise HTTPException(400, "Google Drive not connected")

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "done":
        raise HTTPException(400, f"Job not done (status={job.status})")

    existing = get_drive_upload(db, job_id)
    if existing and existing.status == "uploading":
        raise HTTPException(400, "Upload already in progress")

    upload = create_drive_upload(db, job_id)

    from youtok.queue.tasks import upload_to_drive
    upload_to_drive(upload.id)

    return {"ok": True, "upload_id": upload.id}


@router.get("/upload/{job_id}/status")
async def upload_status(job_id: int, db: Session = Depends(get_db)):
    upload = get_drive_upload(db, job_id)
    if not upload:
        return {"exists": False}
    import json
    files = {}
    if upload.files_json:
        try:
            files = json.loads(upload.files_json)
        except Exception:
            pass
    return {
        "exists": True,
        "status": upload.status,
        "progress_pct": upload.progress_pct,
        "current_file": upload.current_file,
        "drive_folder_url": upload.drive_folder_url,
        "files": files,
        "error_message": upload.error_message,
    }
