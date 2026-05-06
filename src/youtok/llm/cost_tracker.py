import json
from datetime import datetime
from threading import Lock

from youtok.config import settings

# Pricing per 1M tokens (May 2026)
PRICING = {
    # Anthropic
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    # OpenAI (May 2026 published pricing per 1M tokens)
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25, "cache_write": 0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0, "cache_read": 5.0, "cache_write": 0},
    # Reasoning (o-series)
    "o1": {"input": 15.0, "output": 60.0, "cache_read": 7.5, "cache_write": 0},
    "o1-mini": {"input": 3.0, "output": 12.0, "cache_read": 1.5, "cache_write": 0},
    "o1-preview": {"input": 15.0, "output": 60.0, "cache_read": 7.5, "cache_write": 0},
    "o3": {"input": 15.0, "output": 60.0, "cache_read": 7.5, "cache_write": 0},
    "o3-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.55, "cache_write": 0},
    # Google Gemini (May 2026 — output includes thinking tokens)
    "gemini/gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.31, "cache_write": 0},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_read": 0.0375, "cache_write": 0},
    "gemini/gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cache_read": 0.025, "cache_write": 0},
}

_lock = Lock()
_log_path = settings.data_dir / "logs" / "llm-cost.jsonl"
_log_path.parent.mkdir(parents=True, exist_ok=True)


def get_history_summary(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Aggregate cost log into a structure suitable for the History tab.

    Returns dict:
      {
        "grand_total": {"cost": float, "calls": int, "in_tokens": int, "out_tokens": int},
        "providers": {
          "anthropic": {
            "cost": float, "calls": int, "in_tokens": int, "out_tokens": int,
            "by_model": {model_name: {"cost": float, "calls": int, "in_tokens": int, "out_tokens": int}},
            "by_day":   [{"date": "YYYY-MM-DD", "cost": float, "calls": int}],  # newest first
          },
          ...
        },
        "by_day_all": [{"date": "YYYY-MM-DD", "cost": float, "calls": int}],
      }
    """
    grand = {"cost": 0.0, "calls": 0, "in_tokens": 0, "out_tokens": 0}
    providers: dict = {}
    by_day_all: dict = {}  # date -> {cost, calls}

    if not _log_path.exists():
        return {"grand_total": grand, "providers": providers, "by_day_all": []}

    # Normalize bounds — inclusive [date_from, date_to]
    df = (date_from or "").strip() or None  # 'YYYY-MM-DD' string compare works because day is sliced as YYYY-MM-DD
    dt = (date_to or "").strip() or None

    for line in _log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        provider = rec.get("provider") or "anthropic"
        model = rec.get("model", "unknown")
        cost = rec.get("cost_usd", 0.0)
        in_tok = rec.get("input_tokens", 0) or 0
        out_tok = rec.get("output_tokens", 0) or 0
        ts = rec.get("ts", "")
        day = ts[:10] if ts else "unknown"

        # Apply date range filter
        if df and day < df:
            continue
        if dt and day > dt:
            continue

        # grand
        grand["cost"] += cost
        grand["calls"] += 1
        grand["in_tokens"] += in_tok
        grand["out_tokens"] += out_tok

        # provider
        p = providers.setdefault(provider, {
            "cost": 0.0, "calls": 0, "in_tokens": 0, "out_tokens": 0,
            "by_model": {}, "by_day": {},
        })
        p["cost"] += cost
        p["calls"] += 1
        p["in_tokens"] += in_tok
        p["out_tokens"] += out_tok

        # by model (within provider)
        m = p["by_model"].setdefault(model, {"cost": 0.0, "calls": 0, "in_tokens": 0, "out_tokens": 0})
        m["cost"] += cost
        m["calls"] += 1
        m["in_tokens"] += in_tok
        m["out_tokens"] += out_tok

        # by day (within provider)
        d = p["by_day"].setdefault(day, {"cost": 0.0, "calls": 0})
        d["cost"] += cost
        d["calls"] += 1

        # by day all
        da = by_day_all.setdefault(day, {"cost": 0.0, "calls": 0})
        da["cost"] += cost
        da["calls"] += 1

    # Round + finalise structures
    grand["cost"] = round(grand["cost"], 4)
    for p in providers.values():
        p["cost"] = round(p["cost"], 4)
        for m in p["by_model"].values():
            m["cost"] = round(m["cost"], 4)
        # convert by_day dict -> list sorted desc
        p["by_day"] = sorted(
            [{"date": d, **v, "cost": round(v["cost"], 4)} for d, v in p["by_day"].items()],
            key=lambda x: x["date"], reverse=True,
        )
    by_day_list = sorted(
        [{"date": d, **v, "cost": round(v["cost"], 4)} for d, v in by_day_all.items()],
        key=lambda x: x["date"], reverse=True,
    )

    return {"grand_total": grand, "providers": providers, "by_day_all": by_day_list}


def get_usage_per_provider() -> dict:
    """Aggregate cost log by provider. Returns {provider: {cost, calls, in_tokens, out_tokens}}."""
    out: dict = {}
    if not _log_path.exists():
        return out
    for line in _log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        p = rec.get("provider") or "anthropic"
        agg = out.setdefault(p, {"cost": 0.0, "calls": 0, "in_tokens": 0, "out_tokens": 0})
        agg["cost"] += rec.get("cost_usd", 0.0)
        agg["calls"] += 1
        agg["in_tokens"] += rec.get("input_tokens", 0) or 0
        agg["out_tokens"] += rec.get("output_tokens", 0) or 0
    for p in out:
        out[p]["cost"] = round(out[p]["cost"], 4)
    return out


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
    provider: str,
    usage_dict: dict,
    duration_ms: int,
    prompt_preview: str = "",
    response_preview: str = "",
    job_id: int | None = None,
) -> dict:
    p = PRICING.get(model, {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25})
    in_tokens = usage_dict["input_tokens"]
    out_tokens = usage_dict["output_tokens"]
    cache_r = usage_dict.get("cache_read_tokens", 0)
    cache_w = usage_dict.get("cache_creation_tokens", 0)

    regular_input = max(0, in_tokens - cache_r - cache_w)

    cost = (
        regular_input / 1_000_000 * p["input"]
        + out_tokens / 1_000_000 * p["output"]
        + cache_r / 1_000_000 * p["cache_read"]
        + cache_w / 1_000_000 * p["cache_write"]
    )

    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "job_id": job_id,
        "stage": stage,
        "model": model,
        "provider": provider,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "cache_read_tokens": cache_r,
        "cache_creation_tokens": cache_w,
        "cost_usd": round(cost, 6),
        "duration_ms": duration_ms,
        "prompt_first_200": prompt_preview[:200],
        "response_first_200": response_preview[:200],
    }
    with _lock, _log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
