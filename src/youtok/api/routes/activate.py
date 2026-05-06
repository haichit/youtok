from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from youtok.db.base import get_db
from youtok.license import manager
from youtok.license.manager import InvalidLicense, is_activated
from youtok.web import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def activate_page(request: Request):
    if is_activated():
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "activate.html", context={"error": None})


@router.post("/", response_class=HTMLResponse)
async def activate_submit(
    request: Request,
    license_key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        manager.activate(license_key.strip(), db)
    except (InvalidLicense, Exception) as e:
        return templates.TemplateResponse(request, "activate.html", context={"error": str(e)})
    return RedirectResponse("/dashboard", status_code=303)
