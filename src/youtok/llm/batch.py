"""Anthropic Message Batches API — 50% cheaper, async (≤24h, usually <10min)."""
import time

from loguru import logger

from youtok.config import settings
from youtok.llm.client import client, MODEL_MAP
from youtok.llm.cost_tracker import log_call


class BatchTimeout(Exception):
    pass


def stage_b_batch(
    items: list[dict],
    tool_schema: dict,
    tier: str = "haiku",
    max_tokens: int = 2000,
    poll_interval: int = 15,
    timeout_sec: int | None = None,
    job_id: int | None = None,
) -> dict[str, dict]:
    """
    items: list of {"custom_id": str, "system_blocks": list, "messages": list}
    Returns: {custom_id: tool_use.input}
    """
    if timeout_sec is None:
        timeout_sec = settings.batch_timeout_sec
    model = MODEL_MAP[tier]

    requests = []
    for item in items:
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": [tool_schema],
            "tool_choice": {"type": "tool", "name": tool_schema["name"]},
            "messages": item["messages"],
        }
        if item.get("system_blocks"):
            params["system"] = item["system_blocks"]
        requests.append({"custom_id": item["custom_id"], "params": params})

    batch = client.messages.batches.create(requests=requests)
    logger.info(f"Stage B batch submitted: {batch.id}, {len(requests)} requests")

    poll_start = time.time()
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        if time.time() - poll_start > timeout_sec:
            logger.warning(f"Batch {batch.id} timeout after {timeout_sec}s")
            raise BatchTimeout(batch.id)
        time.sleep(poll_interval)

    results: dict[str, dict] = {}
    for line in client.messages.batches.results(batch.id):
        custom_id = line.custom_id
        if line.result.type != "succeeded":
            logger.warning(f"Batch item {custom_id} failed: {line.result.type}")
            continue
        msg = line.result.message
        try:
            tool_use = next(b for b in msg.content if b.type == "tool_use")
            results[custom_id] = tool_use.input
            log_call(
                stage="stage_b_batch",
                model=model,
                provider="anthropic",
                usage_dict={
                    "input_tokens": msg.usage.input_tokens,
                    "output_tokens": msg.usage.output_tokens,
                    "cache_read_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                    "cache_creation_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
                },
                duration_ms=0,
                prompt_preview="",
                response_preview="",
                job_id=job_id,
            )
        except StopIteration:
            logger.warning(f"Batch item {custom_id} no tool_use in response")

    logger.info(f"Stage B batch done: {len(results)}/{len(items)} succeeded")
    return results
