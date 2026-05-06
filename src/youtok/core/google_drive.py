import json
import threading
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger

from youtok.config import settings

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDENTIALS_FILE = settings.assets_dir / "keys" / "google_drive_credentials.json"

_auth_result: dict | None = None
_auth_error: str | None = None
_auth_running = False


def get_credentials_path() -> Path:
    return CREDENTIALS_FILE


def has_credentials_file() -> bool:
    return CREDENTIALS_FILE.exists()


def is_auth_running() -> bool:
    return _auth_running


def get_auth_result() -> tuple[dict | None, str | None]:
    return _auth_result, _auth_error


def clear_auth_result():
    global _auth_result, _auth_error
    _auth_result = None
    _auth_error = None


def start_auth_flow():
    global _auth_result, _auth_error, _auth_running
    _auth_result = None
    _auth_error = None
    _auth_running = True

    def _run():
        global _auth_result, _auth_error, _auth_running
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), scopes=SCOPES,
            )
            logger.info("Starting Google auth local server...")
            creds = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message="")
            logger.info(f"Auth completed, token={creds.token[:20] if creds.token else 'None'}...")
            _auth_result = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or SCOPES),
            }
            logger.info(f"_auth_result set, _auth_running will be set to False")
        except Exception as e:
            logger.exception("Google Drive auth failed")
            _auth_error = str(e)
        finally:
            _auth_running = False
            logger.info(f"Auth flow ended: result={'set' if _auth_result else 'None'}, error={_auth_error}")

    threading.Thread(target=_run, daemon=True).start()


def build_credentials(token_json: str) -> Credentials:
    info = json.loads(token_json)
    creds = Credentials(
        token=info["token"],
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=info.get("scopes", SCOPES),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_user_email(creds: Credentials) -> str:
    try:
        service = build("drive", "v3", credentials=creds)
        about = service.about().get(fields="user").execute()
        return about.get("user", {}).get("emailAddress", "unknown")
    except Exception as e:
        logger.warning(f"Could not get user email: {e}")
        return "unknown"


def build_drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds)


def create_folder(service, name: str, parent_id: str | None = None) -> str:
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file(service, local_path: Path, folder_id: str) -> str:
    meta = {
        "name": local_path.name,
        "parents": [folder_id],
    }
    mime = "video/mp4" if local_path.suffix == ".mp4" else "application/octet-stream"
    if local_path.suffix == ".json":
        mime = "application/json"

    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    file = service.files().create(body=meta, media_body=media, fields="id").execute()
    return file["id"]


def upload_job_clips(
    token_json: str,
    job_id: int,
    clips_dir: Path,
    video_title: str,
    progress_callback=None,
) -> dict:
    creds = build_credentials(token_json)
    service = build_drive_service(creds)

    folder_name = clips_dir.parent.name
    root_folder_id = create_folder(service, folder_name)
    logger.info(f"Created Drive folder: {folder_name} ({root_folder_id})")

    files_to_upload = sorted(clips_dir.glob("*.mp4"))
    manifest = clips_dir.parent / "manifest.json"
    if manifest.exists():
        files_to_upload.append(manifest)

    total = len(files_to_upload)
    file_map: dict[str, str] = {}
    for i, f in enumerate(files_to_upload, 1):
        logger.info(f"Uploading {f.name} ({i}/{total})")
        if progress_callback:
            progress_callback(i, total, f.name)
        file_id = upload_file(service, f, root_folder_id)
        file_map[f.name] = f"https://drive.google.com/file/d/{file_id}/view"

    folder_url = f"https://drive.google.com/drive/folders/{root_folder_id}"

    updated_token = json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    })

    return {
        "folder_id": root_folder_id,
        "folder_url": folder_url,
        "files_uploaded": total,
        "file_map": file_map,
        "updated_token": updated_token,
    }
