"""Base interface for provider adapters."""
import json
import re
from typing import Protocol


class ToolCallMissingError(Exception):
    """LLM response did not contain a tool_call where one was required."""


def normalize_tool_schema(tool_schema: dict) -> tuple[str, str, dict]:
    """Extract (name, description, parameters) from a tool schema in either
    Anthropic 'input_schema' or OpenAI 'parameters' format."""
    name = tool_schema["name"]
    description = tool_schema.get("description", "")
    if "input_schema" in tool_schema and "parameters" not in tool_schema:
        params = tool_schema["input_schema"]
    else:
        params = tool_schema.get("parameters", tool_schema.get("input_schema", {}))
    return name, description, params


class LLMAdapter(Protocol):
    name: str

    def build_kwargs(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        tool_schema: dict,
        max_tokens: int,
        api_key: str,
        use_cache: bool = True,
    ) -> dict:
        """Return litellm.completion kwargs optimized for this provider."""
        ...

    def parse_tool_call(self, resp) -> dict:
        """Extract tool_call arguments JSON from completion response."""
        ...

    def usage_dict(self, resp) -> dict:
        """Extract usage stats. Returns dict with input_tokens, output_tokens, cache_*."""
        ...


def _try_extract_json(text: str) -> dict | None:
    """Attempt to extract a JSON object from a free-form text response.
    Handles: ```json blocks, plain JSON, JSON with leading/trailing prose."""
    if not text:
        return None
    # Strip common code fences
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    # Try to parse as-is
    try:
        return json.loads(text)
    except Exception:
        pass
    # Find the first '{' and try to parse from there
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        snippet = text[first:last + 1]
        try:
            return json.loads(snippet)
        except Exception:
            pass
    return None


def _diagnose_response(resp) -> str:
    """Return diagnostic info about why a response is empty (finish_reason, safety ratings)."""
    bits = []
    choices = getattr(resp, "choices", None) or []
    if not choices:
        # Try to read prompt feedback for safety blocks
        feedback = getattr(resp, "prompt_feedback", None)
        if feedback:
            block_reason = getattr(feedback, "block_reason", None)
            if block_reason:
                bits.append(f"block_reason={block_reason}")
            ratings = getattr(feedback, "safety_ratings", None) or []
            for r in ratings:
                cat = getattr(r, "category", "?")
                prob = getattr(r, "probability", "?")
                if str(prob) not in ("NEGLIGIBLE", "?"):
                    bits.append(f"safety: {cat}={prob}")
        return "; ".join(bits) or "no choices, no diagnostic info"

    ch = choices[0]
    finish = getattr(ch, "finish_reason", None)
    if finish:
        bits.append(f"finish_reason={finish}")
    ratings = getattr(ch, "safety_ratings", None) or []
    for r in ratings:
        cat = getattr(r, "category", "?")
        prob = getattr(r, "probability", "?")
        if str(prob) not in ("NEGLIGIBLE", "?"):
            bits.append(f"safety: {cat}={prob}")
    return "; ".join(bits) or "unknown"


def parse_openai_style_tool_call(resp) -> dict:
    """Default parser — works for OpenAI-format responses (used by all 3 via LiteLLM).

    Defensive against:
    - Empty `choices` list (safety block / quota exhausted / API error)
    - Missing `tool_calls` (Gemini occasionally returns plain text instead of tool call)
    - Falls back to JSON extraction from message.content if tool_calls missing.
    """
    if not getattr(resp, "choices", None):
        diag = _diagnose_response(resp)
        raise ToolCallMissingError(f"LLM response has no choices ({diag})")

    msg = resp.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []

    if tool_calls:
        try:
            return json.loads(tool_calls[0].function.arguments)
        except Exception as e:
            raise ToolCallMissingError(f"tool_call arguments not valid JSON: {e}")

    # Fallback: model returned content text instead of tool_call.
    # Try to extract JSON from the text — common with Gemini under strict tool_choice.
    content = getattr(msg, "content", None) or ""
    extracted = _try_extract_json(content)
    if extracted is not None:
        return extracted

    diag = _diagnose_response(resp)
    raise ToolCallMissingError(
        f"No tool_calls and no JSON content. {diag}. Content preview: {content[:200]!r}"
    )


def parse_openai_style_usage(resp) -> dict:
    """Default usage extractor — works for all providers via LiteLLM."""
    usage = resp.usage
    return {
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }
