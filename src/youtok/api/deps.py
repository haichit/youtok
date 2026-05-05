from fastapi.responses import RedirectResponse

from youtok.license.manager import is_activated


def check_license_or_redirect():
    if not is_activated():
        return RedirectResponse("/activate", status_code=302)
    return None
