from fastapi import APIRouter

from youtok.core.updater import (
    check_for_update,
    detect_just_updated,
    download_update,
    get_current_version,
    get_state,
    install_update,
    is_frozen,
)

router = APIRouter()


@router.get("/status")
async def update_status():
    return {
        "current_version": get_current_version(),
        "is_packaged": is_frozen(),
        **get_state(),
    }


@router.post("/check")
async def update_check():
    return check_for_update(auto_download=True)


@router.post("/download")
async def update_download():
    return download_update()


@router.post("/install")
async def update_install():
    return install_update()


@router.get("/just-updated")
async def just_updated():
    """Check if app was just updated (version changed since last launch).
    Frontend uses this to show post-update banner."""
    return detect_just_updated()
