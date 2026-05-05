import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from youtok.config import settings

PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
}

_lock = Lock()
_log_path = settings.data_dir / "logs" / "llm-cost.jsonl"
_log_path.parent.mkdir(parents=True, exist_ok=True)


def get_total_cost_for_job(job_id: int | None) -> float:
    if job_id is None or not _log_path.exists():
        return 0.0
    total = 0.0
    for line in _log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("job_id") == job_id:
            total += rec.get("cost_usd", 0.0)
    return total


def log_call(
    stage: str,
    model: str,
    usage,
    duration_ms: int,
    prompt_preview: str = "",
    response_preview: str = "",
    job_id: int | None = None,
) -> dict:
    p = PRICING.get(model, {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75})
    in_tokens = usage.input_tokens
    out_tokens = usage.output_tokens
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    cost_in = (in_tokens / 1_000_000) * p["input"]
    cost_out = (out_tokens / 1_000_000) * p["output"]
    cost_cache_read = (cache_read / 1_000_000) * p.get("cache_read", 0)
    cost_cache_write = (cache_write / 1_000_000) * p.get("cache_write", 0)

    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "job_id": job_id,
        "stage": stage,
        "model": model,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_write,
        "cost_usd": round(cost_in + cost_out + cost_cache_read + cost_cache_write, 6),
        "duration_ms": duration_ms,
        "prompt_first_200": prompt_preview[:200],
        "response_first_200": response_preview[:200],
    }
    with _lock, _log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
