"""Youtok — YouTube → 9:16 short-form clip cutter."""
from loguru import logger

from youtok.config import settings

_log_dir = settings.data_dir / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logger.add(
    _log_dir / "youtok-{time}.log",
    rotation="10 MB",
    retention="7 days",
    level=settings.log_level,
    encoding="utf-8",
)
