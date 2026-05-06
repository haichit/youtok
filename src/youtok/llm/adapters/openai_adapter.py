"""OpenAI adapter — strict mode + reasoning model branch.

Optimizations:
- Standard models (gpt-4o, 4o-mini, gpt-4-turbo, gpt-5*):
  * `strict: true` on function definition (guarantee JSON schema compliance)
  * temperature=0.2 + seed=42 (deterministic, reproducible)
- Reasoning models (o1, o1-mini, o3, o3-mini, o4*):
  * No temperature/top_p/seed (silently ignored or rejected)
  * `reasoning_effort` param: low/medium/high (medium = balanced)
  * `strict: true` STILL works on function calls
- Auto-cache for prompts ≥1024 tokens (no manual config needed)
"""
import copy

from youtok.llm.adapters.base import (
    normalize_tool_schema,
    parse_openai_style_tool_call,
    parse_openai_style_usage,
)


def _make_strict_schema(schema: dict) -> dict:
    """Transform a JSON schema to comply with OpenAI strict mode requirements:
    - additionalProperties: false on every object
    - type: ["object", "null"] → anyOf with additionalProperties on the object branch
    - Remove unsupported keywords: pattern, minimum, maximum
    - All object properties must be listed in required
    - Flatten recursive children (OpenAI strict doesn't support recursive refs)
    """
    schema = copy.deepcopy(schema)
    return _fix_node(schema, depth=0)


def _fix_node(node: dict, depth: int = 0) -> dict:
    if not isinstance(node, dict):
        return node

    node.pop("pattern", None)
    node.pop("minimum", None)
    node.pop("maximum", None)

    typ = node.get("type")

    if isinstance(typ, list):
        if "object" in typ and "null" in typ:
            obj_branch = {k: v for k, v in node.items() if k != "type"}
            obj_branch["type"] = "object"
            obj_branch = _fix_node(obj_branch, depth)
            node.clear()
            node["anyOf"] = [obj_branch, {"type": "null"}]
            return node
        if "string" in typ and "null" in typ:
            node.clear()
            node["anyOf"] = [{"type": "string"}, {"type": "null"}]
            return node

    if typ == "object":
        props = node.get("properties", {})
        if not props:
            node.pop("required", None)
            node.pop("properties", None)
            node.pop("additionalProperties", None)
            return node
        # Strip recursive children at depth >= 2 to avoid infinite nesting
        if depth >= 2 and "children" in props:
            del props["children"]
        for key in props:
            props[key] = _fix_node(props[key], depth)
        node["additionalProperties"] = False
        node["required"] = list(props.keys())

    if typ == "array":
        items = node.get("items")
        if isinstance(items, dict):
            node["items"] = _fix_node(items, depth + 1)

    if "anyOf" in node:
        node["anyOf"] = [_fix_node(branch, depth) for branch in node["anyOf"]]

    return node


def _is_reasoning_model(model: str) -> bool:
    """o1, o1-mini, o1-preview, o3, o3-mini, o4* — reasoning models with thinking budget."""
    m = model.lower()
    return (
        m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m == "o1-mini" or m == "o1-preview"
    )


class OpenAIAdapter:
    name = "openai"

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
        # OpenAI: messages with system role first. Auto-cache for ≥1024 tokens.
        # Reasoning models technically use "developer" role but LiteLLM normalizes from "system".
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        name, description, params = normalize_tool_schema(tool_schema)
        strict_params = _make_strict_schema(params)
        function_def = {
            "name": name,
            "description": description,
            "parameters": strict_params,
            "strict": True,  # OpenAI structured output strict mode — schema-enforced
        }
        tools = [{"type": "function", "function": function_def}]

        kwargs = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": name}},
            "api_key": api_key,
            "max_tokens": max_tokens,
        }

        if _is_reasoning_model(model):
            # Reasoning models: thinking budget, no temperature
            kwargs["reasoning_effort"] = "low"
        else:
            # Standard chat models: low temp + seed for deterministic analysis
            kwargs["temperature"] = 0.2
            kwargs["seed"] = 42

        return kwargs

    def parse_tool_call(self, resp) -> dict:
        return parse_openai_style_tool_call(resp)

    def usage_dict(self, resp) -> dict:
        return parse_openai_style_usage(resp)
