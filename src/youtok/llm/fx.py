"""USD → VND exchange rate with daily cache + offline fallback."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import httpx
from loguru import logger

from youtok.config import settings

_FALLBACK_VND_PER_USD = 25500.0  # offline fallback (May 2026 ballpark)
_CACHE_TTL = timedelta(hours=24)
_API_URL = "https://open.er-api.com/v6/latest/USD"  # free, no key, ECB-derived

_cache_path = settings.data_dir / "fx-cache.json"
_lock = Lock()
_mem_cache: dict | None = None


def _load_disk_cache() -> dict | None:
    if not _cache_path.exists():
        return None
    try:
        return json.loads(_cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_disk_cache(rate: float):
    _cache_path.parent.mkdir(parents=True, exist_ok=True)
    _cache_path.write_text(
        json.dumps({"rate": rate, "fetched_at": datetime.utcnow().isoformat() + "Z"}),
        encoding="utf-8",
    )


def _fetch_remote() -> float | None:
    try:
        r = httpx.get(_API_URL, timeout=5)
        r.raise_for_status()
        data = r.json()
        rate = float(data["rates"]["VND"])
        if rate > 0:
            return rate
    except Exception as e:
        logger.warning(f"FX fetch failed: {e}")
    return None


def get_usd_vnd_rate() -> float:
    """Returns USD→VND multiplier. Cache daily, fallback offline."""
    global _mem_cache
    with _lock:
        # mem cache
        if _mem_cache:
            ts = datetime.fromisoformat(_mem_cache["fetched_at"].rstrip("Z"))
            if datetime.utcnow() - ts < _CACHE_TTL:
                return _mem_cache["rate"]

        # disk cache
        disk = _load_disk_cache()
        if disk:
            ts = datetime.fromisoformat(disk["fetched_at"].rstrip("Z"))
            if datetime.utcnow() - ts < _CACHE_TTL:
                _mem_cache = disk
                return disk["rate"]

        # remote
        rate = _fetch_remote()
        if rate:
            _save_disk_cache(rate)
            _mem_cache = {"rate": rate, "fetched_at": datetime.utcnow().isoformat() + "Z"}
            return rate

        # final fallback
        if disk:
            return disk["rate"]
        return _FALLBACK_VND_PER_USD


def usd_to_vnd(usd: float) -> int:
    return int(round(usd * get_usd_vnd_rate()))


def fmt_vnd(usd: float) -> str:
    """Format as Vietnamese currency e.g. '6.165 ₫' or '12.456 ₫'."""
    vnd = usd_to_vnd(usd)
    # Vietnamese thousand separator: dot
    return f"{vnd:,}".replace(",", ".") + " ₫"
