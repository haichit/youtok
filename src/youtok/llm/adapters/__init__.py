"""Provider-specific LLM adapters.

Each adapter knows the optimal:
- Message format (system role placement, cache_control, etc)
- Tool/function schema variant
- Sampling params (temperature, reasoning_effort, etc)
- Response parsing

Usage:
    from youtok.llm.adapters import get_adapter
    adapter = get_adapter('anthropic')
    kwargs = adapter.build_kwargs(model, system_prompt, user_message, tool_schema, max_tokens, api_key)
    resp = litellm.completion(**kwargs)
    args = adapter.parse_tool_call(resp)
"""
from youtok.llm.adapters.base import LLMAdapter
from youtok.llm.adapters.anthropic_adapter import AnthropicAdapter
from youtok.llm.adapters.openai_adapter import OpenAIAdapter
from youtok.llm.adapters.gemini_adapter import GeminiAdapter

_ADAPTERS: dict[str, LLMAdapter] = {
    "anthropic": AnthropicAdapter(),
    "openai": OpenAIAdapter(),
    "google": GeminiAdapter(),
}


def get_adapter(provider: str) -> LLMAdapter:
    if provider not in _ADAPTERS:
        raise ValueError(f"Unknown provider: {provider}")
    return _ADAPTERS[provider]
