import json as _json
import time

import anthropic
from loguru import logger

from youtok.config import settings
from youtok.llm.cost_tracker import log_call

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-7",
}


def call_with_tool(
    prompt: tuple[list[dict], list[dict]] | list[dict],
    tool_schema: dict,
    model: str | None = None,
    tier: str = "sonnet",
    max_retries: int = 5,
    max_tokens: int = 4000,
    stage: str = "unknown",
    job_id: int | None = None,
) -> dict:
    if model is None:
        model = MODEL_MAP[tier]

    if isinstance(prompt, tuple):
        system_blocks, messages = prompt
    else:
        system_blocks = None
        messages = prompt

    prompt_preview = ""
    if messages and isinstance(messages[0].get("content"), str):
        prompt_preview = messages[0]["content"]

    for attempt in range(max_retries + 1):
        try:
            t0 = time.monotonic()
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "tools": [tool_schema],
                "tool_choice": {"type": "tool", "name": tool_schema["name"]},
                "messages": messages,
            }
            if system_blocks:
                kwargs["system"] = system_blocks
            resp = client.messages.create(**kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            tool_use = next(b for b in resp.content if b.type == "tool_use")

            response_preview = ""
            try:
                response_preview = _json.dumps(tool_use.input, ensure_ascii=False)
            except Exception:
                pass

            stage_name = stage if attempt == 0 else f"{stage}_retry{attempt}"
            rec = log_call(
                stage=stage_name,
                model=model,
                usage=resp.usage,
                duration_ms=duration_ms,
                prompt_preview=prompt_preview,
                response_preview=response_preview,
                job_id=job_id,
            )
            logger.debug(
                f"LLM ok: {stage_name} {tool_schema['name']} model={model} "
                f"in={rec['input_tokens']} out={rec['output_tokens']} "
                f"cache_r={rec['cache_read_tokens']} cache_w={rec['cache_creation_tokens']} "
                f"${rec['cost_usd']:.4f} {duration_ms}ms"
            )
            return tool_use.input
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
            if attempt == max_retries:
                raise
            wait = min(60, 5 * (2 ** attempt))
            logger.warning(f"LLM retry {attempt + 1}/{max_retries}: rate limit / connection, wait {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")
