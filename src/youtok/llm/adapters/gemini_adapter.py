"""Google Gemini adapter — function calling + safety_settings.

Optimizations:
- Lower safety filter thresholds (default Gemini is overly cautious for analysis tasks)
- Temperature 0.2 + top_p=0.8 (Gemini works well at slightly higher top_p than OpenAI)
- Native function calling format (LiteLLM converts)
- Context cache only available via gemini-1.5+ explicit API — skip for now (overhead > gain for our short prompts)
"""
from youtok.llm.adapters.base import (
    normalize_tool_schema,
    parse_openai_style_tool_call,
    parse_openai_style_usage,
)


class GeminiAdapter:
    name = "google"

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
        messages = [
            {"role": "system", "content": system_prompt},
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

        is_lite = "flash-lite" in model

        kwargs = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "api_key": api_key,
            "max_tokens": max(max_tokens, 8192),
            "temperature": 0.2,
            "top_p": 0.8,
            "safety_settings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
            ],
        }

        if not is_lite:
            # Flash/Pro have thinking on by default — disable it.
            # Task is structured JSON extraction, thinking wastes tokens and money.
            kwargs["thinking"] = {"type": "disabled"}

        return kwargs

    def parse_tool_call(self, resp) -> dict:
        return parse_openai_style_tool_call(resp)

    def usage_dict(self, resp) -> dict:
        return parse_openai_style_usage(resp)
