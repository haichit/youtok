# Session 08 — Multi-provider LLM support (Claude / OpenAI / Gemini)

> ⚠️ **Run BEFORE Session 07** dù số 08 cao hơn. Lý do: session 07 build distribution sẽ bundle code cuối cùng — phải có multi-provider trước, tránh phải rebuild + re-release sau.

## Goal

User vào `/settings`, chọn LLM provider (Claude / OpenAI / Gemini), paste API key, switch active provider runtime mà không sửa code. Pipeline runtime dùng provider active cho cả Stage A + Stage B.

## Read first

- `../SPEC.md` sections 7.3 (LLM segmentation), 9 (prompts)
- `../sessions/02-pipeline.md` (current Anthropic SDK direct usage)
- Code hiện tại: `src/youtok/llm/`

## Stack chốt

| Layer | Tool | Lý do |
|---|---|---|
| LLM abstraction | **LiteLLM** library | Wrap 100+ provider qua interface OpenAI format, auto tool-use translation |
| Active provider state | SQLite `Setting` table | Single source of truth |
| API key storage | SQLite `ApiKey` table, **plain text** | User chốt skip encrypt MVP (single-machine + license HWID lock đã đủ) |
| UI | Jinja `/settings` page | Form provider dropdown + key input + test button |
| Cost tracking | Extend `cost_tracker.PRICING` | Add pricing OpenAI + Gemini models |

**KHÔNG làm**: fallback chain (Claude fail → GPT → Gemini auto) — user chốt skip MVP, manual switch là đủ.

## Why LiteLLM (vs raw SDK 3 vendor)

LiteLLM auto convert:
- Tool-use format: Anthropic native ↔ OpenAI function calling ↔ Gemini function calling
- Response shape: tất cả normalize về OpenAI `choices[0].message.tool_calls[0].function.arguments`
- Usage tracking: `resp.usage.prompt_tokens / completion_tokens / cache_read_input_tokens`
- Streaming, retry, rate limit handling — built-in

→ Mày code 1 lần, đổi `model=...` string là switch provider. Không phải maintain 3 SDK adapter.

## Default model per provider

```python
PROVIDER_DEFAULTS = {
    "anthropic": {
        "stage_a": "claude-sonnet-4-6",
        "stage_b": "claude-haiku-4-5-20251001",
        "supports_caching": True,
    },
    "openai": {
        "stage_a": "gpt-4o",
        "stage_b": "gpt-4o-mini",
        "supports_caching": True,  # OpenAI auto cache 1024+ token prompts
    },
    "google": {
        "stage_a": "gemini/gemini-2.5-pro",
        "stage_b": "gemini/gemini-2.5-flash",
        "supports_caching": True,  # Gemini context cache
    },
}
```

Cost compare 1 video 6 phút:

| Provider | Stage A model | Stage B model | Est cost/video |
|---|---|---|---|
| **Anthropic** (đang) | Sonnet 4.6 | Haiku 4.5 | $0.05 |
| **OpenAI** | GPT-4o | GPT-4o-mini | $0.02-0.03 |
| **Google** | Gemini 2.5 Pro | Gemini 2.5 Flash | $0.01-0.015 |

→ Switch sang Gemini có thể giảm cost 5x.

## Deliverables

### 1. Add deps

`pyproject.toml`:

```toml
[project]
dependencies = [
    # ... existing
    "litellm>=1.52",
]
```

Run `uv pip install -e ".[cpu,dev]"` để cập nhật.

### 2. DB schema additions

File: `src/youtok/db/models.py` thêm 2 table:

```python
class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), unique=True)  # 'anthropic' | 'openai' | 'google'
    key: Mapped[str] = mapped_column(Text)  # plain text per user decision
    stage_a_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stage_b_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_validated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_validation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'active_provider'
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 3. Alembic migration

```bash
uv run alembic revision --autogenerate -m "add api_keys + settings tables"
uv run alembic upgrade head
```

Verify:

```bash
uv run python -c "from youtok.db.base import engine; from sqlalchemy import inspect; print(inspect(engine).get_table_names())"
# Expected: ['api_keys', 'clips', 'jobs', 'licenses', 'settings']
```

### 4. CRUD

File: `src/youtok/db/crud.py` thêm:

```python
def get_api_key(db: Session, provider: str) -> ApiKey | None:
    return db.execute(select(ApiKey).where(ApiKey.provider == provider)).scalar_one_or_none()

def upsert_api_key(db: Session, provider: str, key: str, stage_a_model=None, stage_b_model=None) -> ApiKey:
    existing = get_api_key(db, provider)
    if existing:
        existing.key = key
        if stage_a_model: existing.stage_a_model = stage_a_model
        if stage_b_model: existing.stage_b_model = stage_b_model
        existing.updated_at = datetime.utcnow()
        api_key = existing
    else:
        api_key = ApiKey(provider=provider, key=key, stage_a_model=stage_a_model, stage_b_model=stage_b_model)
        db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key

def get_setting(db: Session, key: str, default=None) -> str | None:
    s = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return s.value if s else default

def set_setting(db: Session, key: str, value: str):
    existing = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if existing:
        existing.value = value
        existing.updated_at = datetime.utcnow()
    else:
        db.add(Setting(key=key, value=value))
    db.commit()

def get_active_provider(db: Session) -> str:
    return get_setting(db, "active_provider", "anthropic")

def get_active_provider_config(db: Session) -> dict:
    """Returns dict with provider, api_key, stage_a_model, stage_b_model."""
    provider = get_active_provider(db)
    api_key_row = get_api_key(db, provider)
    if not api_key_row or not api_key_row.key:
        raise NoApiKeyError(f"No API key configured for provider '{provider}'")
    
    from youtok.llm.providers import PROVIDER_DEFAULTS
    defaults = PROVIDER_DEFAULTS[provider]
    return {
        "provider": provider,
        "api_key": api_key_row.key,
        "stage_a_model": api_key_row.stage_a_model or defaults["stage_a"],
        "stage_b_model": api_key_row.stage_b_model or defaults["stage_b"],
        "supports_caching": defaults["supports_caching"],
    }
```

### 5. Provider config

File: `src/youtok/llm/providers.py` (mới)

```python
PROVIDER_DEFAULTS = {
    "anthropic": {
        "stage_a": "claude-sonnet-4-6",
        "stage_b": "claude-haiku-4-5-20251001",
        "supports_caching": True,
        "key_prefix": "sk-ant-",
        "name": "Claude (Anthropic)",
        "test_message": "Say 'ok'",
    },
    "openai": {
        "stage_a": "gpt-4o",
        "stage_b": "gpt-4o-mini",
        "supports_caching": True,
        "key_prefix": "sk-",
        "name": "OpenAI (GPT-4o + 4o-mini)",
        "test_message": "Say 'ok'",
    },
    "google": {
        "stage_a": "gemini/gemini-2.5-pro",
        "stage_b": "gemini/gemini-2.5-flash",
        "supports_caching": True,
        "key_prefix": "",  # Google API key không có prefix chuẩn
        "name": "Google Gemini (2.5 Pro + Flash)",
        "test_message": "Say 'ok'",
    },
}


PROVIDER_CHOICES = [
    ("anthropic", "Claude (Anthropic) — recommend for quality"),
    ("openai", "OpenAI (GPT-4o + 4o-mini) — balanced cost/quality"),
    ("google", "Google Gemini (2.5 Pro + Flash) — cheapest"),
]
```

### 6. Rewrite LLM client với LiteLLM

File: `src/youtok/llm/client.py`

```python
import os
import time
from typing import Any
import litellm
from loguru import logger

from youtok.config import settings
from youtok.db.base import SessionLocal
from youtok.db.crud import get_active_provider_config
from youtok.llm.cost_tracker import log_call

# Disable litellm telemetry
os.environ["LITELLM_LOG"] = "WARNING"
litellm.drop_params = True  # Drop unsupported params per provider, không crash


class NoApiKeyError(Exception):
    pass


def call_with_tool(
    stage: str,
    system_prompt: str,
    user_message: str,
    tool_schema: dict,  # OpenAI function format: {name, description, parameters}
    tier: str = "stage_a",  # 'stage_a' | 'stage_b'
    job_id: int | None = None,
    use_cache: bool = True,
) -> dict:
    """
    Universal LLM call via LiteLLM. Returns parsed tool_calls[0].function.arguments as dict.
    """
    with SessionLocal() as db:
        cfg = get_active_provider_config(db)
    
    model = cfg["stage_a_model"] if tier == "stage_a" else cfg["stage_b_model"]
    
    # Build messages (OpenAI format)
    if cfg["supports_caching"] and use_cache and cfg["provider"] == "anthropic":
        # Anthropic native cache_control
        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": user_message},
        ]
    else:
        # OpenAI/Gemini auto-cache, không cần markup
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    
    # Tool format OpenAI universal
    tools = [{
        "type": "function",
        "function": {
            "name": tool_schema["name"],
            "description": tool_schema.get("description", ""),
            "parameters": tool_schema["parameters"],
        }
    }]
    
    start = time.time()
    try:
        resp = litellm.completion(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_schema["name"]}},
            api_key=cfg["api_key"],
            max_tokens=8000,
        )
    except Exception as e:
        logger.exception(f"LLM call failed at stage={stage} model={model}: {e}")
        raise
    
    duration_ms = int((time.time() - start) * 1000)
    
    # Parse response (OpenAI format universal)
    tool_call = resp.choices[0].message.tool_calls[0]
    import json
    args = json.loads(tool_call.function.arguments)
    
    # Log cost
    usage = resp.usage
    log_call(
        stage=stage,
        model=model,
        provider=cfg["provider"],
        usage_dict={
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        },
        duration_ms=duration_ms,
        job_id=job_id,
        prompt_preview=user_message[:200],
        response_preview=tool_call.function.arguments[:200],
    )
    
    return args


def test_provider_connection(provider: str, api_key: str) -> tuple[bool, str]:
    """Test API key valid. Returns (ok, message)."""
    from youtok.llm.providers import PROVIDER_DEFAULTS
    defaults = PROVIDER_DEFAULTS[provider]
    model = defaults["stage_b"]  # use cheap model for test
    
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
    except litellm.RateLimitError as e:
        return False, f"Rate limited (key may be valid): {e}"
    except Exception as e:
        return False, f"Error: {type(e).__name__}: {e}"
```

### 7. Update cost_tracker.PRICING

File: `src/youtok/llm/cost_tracker.py`

```python
# Pricing per 1M tokens (May 2026 — verify https://docs.litellm.ai/docs/providers)
PRICING = {
    # Anthropic (existing)
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25, "cache_write": 0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0},
    
    # Google Gemini (note: model name with "gemini/" prefix from LiteLLM)
    "gemini/gemini-2.5-pro": {"input": 1.25, "output": 5.0, "cache_read": 0.31, "cache_write": 0},
    "gemini/gemini-2.5-flash": {"input": 0.075, "output": 0.30, "cache_read": 0.019, "cache_write": 0},
    "gemini/gemini-2.5-flash-lite": {"input": 0.04, "output": 0.10, "cache_read": 0.01, "cache_write": 0},
}


def log_call(stage, model, provider, usage_dict, duration_ms, job_id=None, prompt_preview="", response_preview=""):
    p = PRICING.get(model, {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25})
    in_tokens = usage_dict["input_tokens"]
    out_tokens = usage_dict["output_tokens"]
    cache_r = usage_dict.get("cache_read_tokens", 0)
    cache_w = usage_dict.get("cache_creation_tokens", 0)
    
    # Subtract cached tokens from regular input
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
        "provider": provider,  # NEW field
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
        f.write(json.dumps(record) + "\n")
    return record
```

### 8. Update prompts.py for OpenAI tool format

File: `src/youtok/llm/prompts.py`

Hiện tại tool schema dùng Anthropic `input_schema`. Convert sang OpenAI format `parameters`:

```python
TOOL_SCHEMA_STAGE_A = {
    "name": "submit_topic_tree",
    "description": "Submit the analyzed topic tree.",
    "parameters": {  # was 'input_schema' for Anthropic
        "type": "object",
        "properties": {
            "main_topic": {"type": "string"},
            "intro_strip": {
                "type": "object",
                "nullable": True,
                "properties": {
                    "start": {"type": "string", "pattern": "^S\\d{3}$"},
                    "end": {"type": "string", "pattern": "^S\\d{3}$"},
                },
            },
            "outro_strip": {...},
            "sub_topics": {
                "type": "array",
                "items": {"$ref": "#/$defs/subTopic"},
            },
        },
        "$defs": {
            "subTopic": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "start_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                    "end_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                    "parent": {"type": "string", "nullable": True},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/subTopic"}},
                },
                "required": ["name", "start_sentence", "end_sentence"],
            }
        },
        "required": ["main_topic", "sub_topics"],
    },
}
```

LiteLLM convert ngược lại Anthropic format khi gọi Claude.

### 9. Settings UI

File: `src/youtok/api/routes/settings.py` (mới)

```python
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from youtok.api.deps import check_license_or_redirect
from youtok.db.base import get_db
from youtok.db.crud import get_api_key, upsert_api_key, get_active_provider, set_setting
from youtok.llm.providers import PROVIDER_DEFAULTS, PROVIDER_CHOICES
from youtok.llm.client import test_provider_connection

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    
    active = get_active_provider(db)
    keys = {p[0]: get_api_key(db, p[0]) for p in PROVIDER_CHOICES}
    
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="src/youtok/web/templates")
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active_provider": active,
        "keys": keys,  # dict {provider: ApiKey | None}
        "provider_choices": PROVIDER_CHOICES,
        "provider_defaults": PROVIDER_DEFAULTS,
    })


@router.post("/save")
async def save_settings(
    provider: str = Form(...),
    api_key: str = Form(...),
    stage_a_model: str = Form(""),
    stage_b_model: str = Form(""),
    set_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, "Invalid provider")
    
    upsert_api_key(
        db, provider,
        key=api_key,
        stage_a_model=stage_a_model or None,
        stage_b_model=stage_b_model or None,
    )
    
    if set_active:
        set_setting(db, "active_provider", provider)
    
    return {"ok": True, "provider": provider, "active": set_active}


@router.post("/test")
async def test_key(
    provider: str = Form(...),
    api_key: str = Form(...),
):
    ok, msg = test_provider_connection(provider, api_key)
    return {"ok": ok, "message": msg}


@router.post("/activate")
async def activate_provider(
    provider: str = Form(...),
    db: Session = Depends(get_db),
):
    """Switch active provider (key must exist already)."""
    if not get_api_key(db, provider):
        raise HTTPException(400, f"No API key configured for {provider}")
    set_setting(db, "active_provider", provider)
    return {"ok": True, "active": provider}
```

Wire vào `src/youtok/api/main.py`:

```python
from youtok.api.routes import settings as settings_routes
app.include_router(settings_routes.router, prefix="/settings")
```

### 10. Settings template

File: `src/youtok/web/templates/settings.html`

```html
{% extends "base.html" %}
{% set show_nav = true %}
{% block content %}
<div class="max-w-3xl mx-auto space-y-6">
  <h1 class="text-3xl font-bold">Settings</h1>
  
  <!-- Active provider -->
  <div class="glass rounded-2xl p-6">
    <h2 class="text-xl font-bold mb-4">LLM Provider</h2>
    <div class="text-sm text-white/60 mb-3">Currently active: <span class="text-accent-pink font-semibold">{{ provider_defaults[active_provider]["name"] }}</span></div>
    
    <div class="space-y-3">
      {% for provider_id, label in provider_choices %}
      {% set has_key = keys[provider_id] is not none %}
      <div class="glass rounded-xl p-4 {% if provider_id == active_provider %}border-accent-pink{% endif %}">
        <div class="flex items-center justify-between mb-2">
          <div>
            <div class="font-semibold">{{ label }}</div>
            <div class="text-xs text-white/40">{{ provider_defaults[provider_id]["stage_a"] }} + {{ provider_defaults[provider_id]["stage_b"] }}</div>
          </div>
          <div>
            {% if has_key %}<span class="text-emerald-400 text-sm">✓ Configured</span>{% endif %}
            {% if provider_id == active_provider %}<span class="ml-2 gradient-btn px-3 py-1 rounded text-xs">ACTIVE</span>{% endif %}
          </div>
        </div>
        
        <details class="mt-2">
          <summary class="cursor-pointer text-sm text-accent-pink">Configure</summary>
          <form class="mt-3 space-y-3" hx-post="/settings/save" hx-trigger="submit">
            <input type="hidden" name="provider" value="{{ provider_id }}">
            <div>
              <label class="text-xs text-white/60 block mb-1">API Key</label>
              <input type="password" name="api_key" placeholder="{{ provider_defaults[provider_id]['key_prefix'] }}..." 
                     class="w-full glass rounded p-2 text-sm font-mono"
                     {% if has_key %}value="••••••••••••••••"{% endif %} required>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-xs text-white/60 block mb-1">Stage A model (advanced)</label>
                <input name="stage_a_model" placeholder="{{ provider_defaults[provider_id]['stage_a'] }}" 
                       class="w-full glass rounded p-2 text-sm font-mono"
                       {% if keys[provider_id] and keys[provider_id].stage_a_model %}value="{{ keys[provider_id].stage_a_model }}"{% endif %}>
              </div>
              <div>
                <label class="text-xs text-white/60 block mb-1">Stage B model (advanced)</label>
                <input name="stage_b_model" placeholder="{{ provider_defaults[provider_id]['stage_b'] }}"
                       class="w-full glass rounded p-2 text-sm font-mono"
                       {% if keys[provider_id] and keys[provider_id].stage_b_model %}value="{{ keys[provider_id].stage_b_model }}"{% endif %}>
              </div>
            </div>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" name="set_active" value="true">
              <span>Set as active provider</span>
            </label>
            <div class="flex gap-2">
              <button type="button" onclick="testKey('{{ provider_id }}', this.parentElement.parentElement)" class="glass px-4 py-2 rounded text-sm">Test connection</button>
              <button type="submit" class="gradient-btn px-4 py-2 rounded text-sm">Save</button>
            </div>
          </form>
        </details>
      </div>
      {% endfor %}
    </div>
  </div>
</div>

<script>
async function testKey(provider, formEl) {
  const fd = new FormData(formEl);
  fd.set("provider", provider);
  const r = await fetch("/settings/test", { method: "POST", body: fd });
  const data = await r.json();
  alert(data.ok ? "✓ Connection OK" : "✗ " + data.message);
}
</script>
{% endblock %}
```

Add link to nav in `base.html`:

```html
<a href="/settings" class="hover:text-accent-pink">Settings</a>
```

### 11. Update segmenter to use new client

File: `src/youtok/core/segmenter.py`

```python
from youtok.llm.client import call_with_tool
from youtok.llm.prompts import (
    SYSTEM_STAGE_A, build_user_stage_a, TOOL_SCHEMA_STAGE_A,
    SYSTEM_STAGE_B, build_user_stage_b, TOOL_SCHEMA_STAGE_B,
)


def stage_a_outline(transcript, video_title, job_id) -> StageAOutput:
    user_msg = build_user_stage_a(transcript, video_title)
    args = call_with_tool(
        stage="stage_a",
        system_prompt=SYSTEM_STAGE_A,
        user_message=user_msg,
        tool_schema=TOOL_SCHEMA_STAGE_A,
        tier="stage_a",
        job_id=job_id,
    )
    return StageAOutput.model_validate(args)


def stage_b_validate(sub_topic, transcript, job_id) -> StageBOutput:
    cache_key = f"{sub_topic.start_sentence}:{sub_topic.end_sentence}:{sub_topic.parent or '_'}"
    with _cache_lock:
        if cache_key in _stage_b_cache:
            return _stage_b_cache[cache_key]
    
    user_msg = build_user_stage_b(sub_topic, transcript)
    args = call_with_tool(
        stage="stage_b",
        system_prompt=SYSTEM_STAGE_B,
        user_message=user_msg,
        tool_schema=TOOL_SCHEMA_STAGE_B,
        tier="stage_b",
        job_id=job_id,
    )
    result = StageBOutput.model_validate(args)
    
    with _cache_lock:
        _stage_b_cache[cache_key] = result
    return result
```

### 12. Migration data: existing ANTHROPIC_API_KEY env

Nếu user đã có `ANTHROPIC_API_KEY` trong `.env`, on first start:

```python
# src/youtok/api/main.py lifespan
async def migrate_env_to_db():
    """One-time: copy ANTHROPIC_API_KEY from .env to api_keys table if not exists."""
    if settings.anthropic_api_key:
        with SessionLocal() as db:
            if not get_api_key(db, "anthropic"):
                upsert_api_key(db, "anthropic", settings.anthropic_api_key)
                set_setting(db, "active_provider", "anthropic")
                logger.info("Migrated ANTHROPIC_API_KEY from .env to DB")
```

Run trong `lifespan` startup. Sau đó user có thể delete `ANTHROPIC_API_KEY` khỏi `.env` và quản key qua /settings.

## Acceptance test

### 1. Migration

```bash
uv run alembic upgrade head
# Verify: api_keys + settings tables exist
```

### 2. Save Anthropic key qua UI

```
1. Mở http://localhost:8000/settings
2. Expand "Claude (Anthropic)"
3. Paste sk-ant-... key
4. Click "Test connection" → alert "✓ Connection OK"
5. Check "Set as active provider" → Save
6. Reload page → "Currently active: Claude (Anthropic)" + ✓ Configured + ACTIVE badge
```

### 3. Run pipeline với Claude

```bash
# Submit 1 job qua UI
# Verify cost log có "provider": "anthropic", "model": "claude-sonnet-4-6" (Stage A) và "claude-haiku..." (Stage B)
```

### 4. Add OpenAI key + switch

```
1. /settings → Expand OpenAI → paste sk-... → Test → ✓
2. Check "Set as active" → Save
3. Submit new job
4. Cost log có "provider": "openai", "model": "gpt-4o" (Stage A) + "gpt-4o-mini" (Stage B)
5. Cost giảm rõ rệt vs Claude run
```

### 5. Add Gemini key + switch

```
1. /settings → Expand Google Gemini → paste API key
2. Test → ✓
3. Set active → Save → Submit job
4. Cost log: "provider": "google", "model": "gemini/gemini-2.5-pro" + "gemini/gemini-2.5-flash"
5. Cost rẻ nhất trong 3 provider
```

### 6. Wrong key handling

```
1. /settings → Expand any → paste invalid key → Test → ✗ "Authentication failed: ..."
2. Save invalid key + set active → Submit job
3. Job should fail fast với error message rõ "Authentication failed", không crash worker
```

### 7. No key configured

```
1. DELETE FROM api_keys; UPDATE settings SET value='openai' WHERE key='active_provider';
2. Submit job → fail với NoApiKeyError "No API key configured for provider 'openai'"
3. UI báo error, không crash
```

## Anti-patterns

- ❌ Encrypt API key với secret hardcoded trong code: secret leak qua reverse-engineer bundle. Nếu encrypt thì phải dùng machine_id derived key. Nhưng user chốt skip MVP.
- ❌ Log full API key trong cost_tracker: chỉ log `key[:8]` nếu cần debug. Tốt nhất: không log key.
- ❌ Pass API key qua URL query string: leak vào log access. Luôn POST body.
- ❌ Test connection không có timeout: hang nếu vendor chậm.
- ❌ Cache provider config 1 lần đầu pipeline: user switch provider giữa job → vẫn dùng old. Phải đọc DB mỗi LLM call.
- ❌ Hardcode model string trong segmenter.py: phải dùng `cfg.stage_a_model` từ DB.
- ❌ Skip prompts.py update: Anthropic `input_schema` không hoạt động trên OpenAI/Gemini, phải convert sang `parameters`.
- ❌ Drop existing cache_control khi switch sang OpenAI/Gemini: LiteLLM `drop_params=True` đã handle, nhưng verify code path không assume Anthropic format.

## Risks

| Risk | Mức | Mitigation |
|---|---|---|
| LiteLLM bug ở edge case (rare model) | Low-Med | Fallback to direct SDK nếu LiteLLM fail (advanced, skip MVP) |
| OpenAI/Gemini schema validation strict hơn Anthropic | Med | Test 3 provider, fix schema nếu fail |
| Pricing thay đổi | Low | Update `PRICING` dict khi vendor đổi |
| Tool-use format edge case | Med | Test cả 3 provider với 1 video, verify segment output đúng |
| Quality giảm khi switch sang Gemini Flash cho Stage A | Med | Default Gemini config dùng 2.5 Pro cho Stage A (chứ không Flash) |

## When done

1. /settings UI render đầy đủ 3 provider, có active badge.
2. Test 3 provider, mỗi provider chạy 1 video → cost log đúng provider + model.
3. Cost compare 3 provider in 1 video duy nhất → bảng so sánh cost thực tế (note vào `data/multi-provider-test.md`).
4. Migration env → DB chạy on first start.
5. Old `ANTHROPIC_API_KEY` trong `.env` không còn cần thiết — user có thể delete.

## Sau session 08 done

- Update wiki: thêm note "supports multi-provider (Claude / OpenAI / Gemini)" vào entity youtok.
- Tiếp tục Session 07 (build distribution) — code đã ready với multi-provider.
