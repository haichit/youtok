from huey import SqliteHuey
from huey.signals import SIGNAL_INTERRUPTED  # noqa: F401 — keep import for signal API
from loguru import logger

from youtok.config import settings

huey = SqliteHuey(
    name="youtok",
    filename=str(settings.queue_db_path),
    immediate=False,
    results=False,
    store_none=False,
)


# Pre-warm WhisperModel at worker startup. Runs once when the consumer process starts.
@huey.on_startup()
def _prewarm_whisper():
    try:
        from youtok.core.transcriber import detect_device, _get_whisper_model
        device, compute_type, model_name = detect_device()
        logger.info(f"Pre-warming WhisperModel at worker startup: {model_name} ({device}, {compute_type})")
        _get_whisper_model(model_name, device, compute_type)
        logger.info("WhisperModel pre-warm complete")
    except Exception as e:
        logger.warning(f"WhisperModel pre-warm failed (will lazy-load on first job): {e}")
