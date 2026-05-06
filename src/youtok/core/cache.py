"""Persistent disk cache for expensive pipeline artifacts (transcript + shot boundaries)."""
import hashlib
import json
from pathlib import Path

from loguru import logger

from youtok.config import settings

# Bump these when output schema changes — invalidates old caches automatically.
TRANSCRIPT_CACHE_VERSION = "v2"
SHOTS_CACHE_VERSION = "v2"

_cache_dir = settings.data_dir / "cache"
_cache_dir.mkdir(parents=True, exist_ok=True)


def _hash_file_head(path: Path, max_bytes: int = 1_048_576) -> str:
    """MD5 of first 1MB of file. Fast + effectively unique for video/audio files."""
    h = hashlib.md5()
    with path.open("rb") as f:
        h.update(f.read(max_bytes))
    return h.hexdigest()


def transcript_cache_path(audio_path: Path) -> Path:
    h = _hash_file_head(audio_path)
    return _cache_dir / f"transcript-{h}-{TRANSCRIPT_CACHE_VERSION}.json"


def load_transcript(audio_path: Path):
    """Returns Transcript or None."""
    from youtok.core.transcriber import Transcript
    p = transcript_cache_path(audio_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        logger.info(f"Cache HIT transcript: {p.name}")
        return Transcript.model_validate(data)
    except Exception as e:
        logger.warning(f"Transcript cache load failed: {e}")
        return None


def save_transcript(audio_path: Path, transcript) -> None:
    p = transcript_cache_path(audio_path)
    try:
        p.write_text(transcript.model_dump_json(), encoding="utf-8")
        logger.info(f"Cached transcript: {p.name}")
    except Exception as e:
        logger.warning(f"Transcript cache save failed: {e}")


def shots_cache_path(video_path: Path) -> Path:
    h = _hash_file_head(video_path)
    return _cache_dir / f"shots-{h}-{SHOTS_CACHE_VERSION}.json"


def load_shots(video_path: Path) -> list[float] | None:
    p = shots_cache_path(video_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        logger.info(f"Cache HIT shots: {p.name}")
        return data
    except Exception as e:
        logger.warning(f"Shots cache load failed: {e}")
        return None


def save_shots(video_path: Path, shots: list[float]) -> None:
    p = shots_cache_path(video_path)
    try:
        p.write_text(json.dumps(shots), encoding="utf-8")
        logger.info(f"Cached shots: {p.name} ({len(shots)} boundaries)")
    except Exception as e:
        logger.warning(f"Shots cache save failed: {e}")
