"""Universal LLM client — dispatches to provider-specific adapter for optimal config.

Adapter pattern: each provider (anthropic / openai / google) has its own adapter in
`llm/adapters/` that builds optimal completion kwargs for that vendor (cache_control,
strict mode, reasoning_effort, safety_settings, etc).

The client itself just:
1. Reads active provider config from DB
2. Picks the right adapter
3. Builds kwargs via adapter
4. Calls litellm.completion
5. Parses response via adapter
6. Logs cost
"""
import os
import time

import litellm
from loguru import logger

from youtok.db.base import SessionLocal
from youtok.db.crud import get_active_provider_config
from youtok.llm.adapters import get_adapter
from youtok.llm.adapters.base import ToolCallMissingError
from youtok.llm.cost_tracker import log_call

os.environ.setdefault("LITELLM_LOG", "WARNING")
litellm.drop_params = True  # Drop unsupported params per provider, don't crash


def call_with_tool(
    prompt: tuple[list[dict], list[dict]] | list[dict],
    tool_schema: dict,
    model: str | None = None,
    tier: str = "sonnet",
    max_retries: int = 3,
    max_tokens: int = 4000,
    stage: str = "unknown",
    job_id: int | None = None,
) -> dict:
    with SessionLocal() as db:
        cfg = get_active_provider_config(db)

    tier_key = "stage_a" if tier in ("sonnet", "stage_a") else "stage_b"
    resolved_model = model or (cfg["stage_a_model"] if tier_key == "stage_a" else cfg["stage_b_model"])

    # Extract system prompt + user message from legacy tuple format
    if isinstance(prompt, tuple):
        system_blocks, user_messages = prompt
        system_prompt = system_blocks[0]["text"] if isinstance(system_blocks, list) and system_blocks else ""
    else:
        system_prompt = ""
        user_messages = prompt

    user_message = user_messages[0]["content"] if user_messages else ""
    if not isinstance(user_message, str):
        # Tool flows always use plain string content
        user_message = str(user_message)

    # Dispatch to provider-specific adapter for optimal kwargs
    adapter = get_adapter(cfg["provider"])
    completion_kwargs = adapter.build_kwargs(
        model=resolved_model,
        system_prompt=system_prompt,
        user_message=user_message,
        tool_schema=tool_schema,
        max_tokens=max_tokens,
        api_key=cfg["api_key"],
        use_cache=cfg.get("supports_caching", True),
    )

    prompt_preview = user_message[:200]

    for attempt in range(max_retries + 1):
        try:
            t0 = time.monotonic()
            resp = litellm.completion(**completion_kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)

            args = adapter.parse_tool_call(resp)
            response_preview = ""
            try:
                import json as _json
                response_preview = _json.dumps(args, ensure_ascii=False)[:200]
            except Exception:
                pass

            usage_dict = adapter.usage_dict(resp)
            stage_name = stage if attempt == 0 else f"{stage}_retry{attempt}"
            rec = log_call(
                stage=stage_name,
                model=resolved_model,
                provider=cfg["provider"],
                usage_dict=usage_dict,
                duration_ms=duration_ms,
                job_id=job_id,
                prompt_preview=prompt_preview,
                response_preview=response_preview,
            )
            logger.debug(
                f"LLM ok: {stage_name} {tool_schema['name']} model={resolved_model} "
                f"provider={cfg['provider']} via {adapter.name}-adapter "
                f"in={rec['input_tokens']} out={rec['output_tokens']} "
                f"cache_r={rec['cache_read_tokens']} cache_w={rec['cache_creation_tokens']} "
                f"${rec['cost_usd']:.4f} {duration_ms}ms"
            )
            return args
        except litellm.RateLimitError as e:
            # Detect quota/credit exhaustion → don't retry, raise immediately with clear message
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("credits are depleted", "quota exceeded", "prepayment", "billing", "resource_exhausted")):
                provider_name = cfg["provider"]
                billing_urls = {
                    "anthropic": "https://console.anthropic.com/settings/billing",
                    "openai": "https://platform.openai.com/account/billing/overview",
                    "google": "https://ai.studio/projects",
                }
                billing = billing_urls.get(provider_name, "vendor billing dashboard")
                raise RuntimeError(
                    f"{provider_name.upper()} API quota/credit exhausted. "
                    f"Vào {billing} để nạp credit, hoặc đổi sang provider khác trong /settings/. "
                    f"Original error: {e}"
                ) from e
            # Otherwise: transient rate limit — retry with backoff
            if attempt == max_retries:
                raise
            wait = min(60, 5 * (2 ** attempt))
            logger.warning(f"LLM retry {attempt + 1}/{max_retries}: rate limit, wait {wait}s")
            time.sleep(wait)
        except litellm.APIConnectionError as e:
            if attempt == max_retries:
                raise
            wait = min(60, 5 * (2 ** attempt))
            logger.warning(f"LLM retry {attempt + 1}/{max_retries}: connection error, wait {wait}s")
            time.sleep(wait)
        except ToolCallMissingError as e:
            # Gemini occasionally returns plain text instead of tool_call.
            # Retry up to 2 times, then raise — usually transient.
            if attempt >= min(max_retries, 2):
                logger.error(f"Tool call missing after {attempt + 1} attempts: {e}")
                raise
            logger.warning(f"Tool call missing (attempt {attempt + 1}), retrying: {e}")
            time.sleep(1.0)

    raise RuntimeError("unreachable")


def test_provider_connection(provider: str, api_key: str) -> tuple[bool, str]:
    from youtok.llm.providers import PROVIDER_DEFAULTS
    defaults = PROVIDER_DEFAULTS[provider]
    model = defaults["stage_b"]

    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": defaults["test_message"]}],
            api_key=api_key,
            max_tokens=20,
            timeout=15,
        )
        if resp.choices[0].message.content:
            return True, "OK"
        return False, "Empty response"
    except litellm.AuthenticationError as e:
        return False, f"Authentication failed: {e}"
    except litellm.RateLimitError:
        return True, "Rate limited (key is valid)"
    except Exception as e:
        return False, f"Error: {type(e).__name__}: {e}"
