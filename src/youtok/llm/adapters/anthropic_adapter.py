"""Anthropic Claude adapter — leverage native cache_control + tool_use schema.

Optimizations:
- system_prompt as content block with cache_control: ephemeral (5min cache, free re-reads)
- Temperature 0.0 for deterministic analysis (Claude is consistent at 0.0, less so at >0.5)
- Native tool format passed via LiteLLM (it handles conversion)
- max_tokens up to 8192 standard
"""
from youtok.llm.adapters.base import (
    normalize_tool_schema,
    parse_openai_style_tool_call,
    parse_openai_style_usage,
)


class AnthropicAdapter:
    name = "anthropic"

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
        # Anthropic native cache_control on system prompt — re-uses cached prefix
        # across all calls in 5-min window (huge win for batch Stage B parallel calls).
        if use_cache:
            system_content = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system_prompt

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message},
        ]

        name, description, params = normalize_tool_schema(tool_schema)
        tools = [{
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": params,
            }
        }]

        return {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": name}},
            "api_key": api_key,
            "max_tokens": max_tokens,
            # Claude is highly deterministic — temp 0.0 gives best consistency on analysis
            "temperature": 0.0,
        }

    def parse_tool_call(self, resp) -> dict:
        return parse_openai_style_tool_call(resp)

    def usage_dict(self, resp) -> dict:
        return parse_openai_style_usage(resp)
