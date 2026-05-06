PROVIDER_DEFAULTS = {
    "anthropic": {
        "stage_a": "claude-sonnet-4-6",
        "stage_b": "claude-haiku-4-5-20251001",
        "supports_caching": True,
        "key_prefix": "sk-ant-",
        "name": "Claude (Anthropic)",
        "test_message": "Say 'ok'",
        "billing_url": "https://console.anthropic.com/settings/billing",
        "stage_a_options": [
            ("claude-opus-4-7", "Opus 4.7 — best quality, expensive"),
            ("claude-sonnet-4-6", "Sonnet 4.6 — recommended"),
            ("claude-haiku-4-5-20251001", "Haiku 4.5 — fastest"),
        ],
        "stage_b_options": [
            ("claude-sonnet-4-6", "Sonnet 4.6 — higher quality"),
            ("claude-haiku-4-5-20251001", "Haiku 4.5 — recommended"),
        ],
    },
    "openai": {
        "stage_a": "gpt-4o",
        "stage_b": "gpt-4o-mini",
        "supports_caching": True,
        "key_prefix": "sk-",
        "name": "OpenAI (GPT-4o + 4o-mini)",
        "test_message": "Say 'ok'",
        "billing_url": "https://platform.openai.com/account/billing/overview",
        "stage_a_options": [
            ("o3", "o3 — top reasoning, slow + $$"),
            ("o1", "o1 — strong reasoning"),
            ("o3-mini", "o3-mini — balanced reasoning"),
            ("o1-mini", "o1-mini — cheap reasoning"),
            ("gpt-4o", "GPT-4o — recommended (chat)"),
            ("gpt-4-turbo", "GPT-4 Turbo — older but stable"),
            ("gpt-4o-mini", "GPT-4o-mini — cheap, may sacrifice quality"),
        ],
        "stage_b_options": [
            ("gpt-4o-mini", "GPT-4o-mini — recommended (best value)"),
            ("gpt-4o", "GPT-4o — higher quality, 7x cost"),
            ("o3-mini", "o3-mini — reasoning validation"),
            ("o1-mini", "o1-mini — cheap reasoning"),
            ("gpt-4-turbo", "GPT-4 Turbo — older stable"),
        ],
    },
    "google": {
        "stage_a": "gemini/gemini-2.5-pro",
        "stage_b": "gemini/gemini-2.5-flash",
        "supports_caching": True,
        "key_prefix": "",
        "name": "Google Gemini (2.5 Pro + Flash)",
        "test_message": "Say 'ok'",
        "billing_url": "https://aistudio.google.com/app/apikey",
        "stage_a_options": [
            ("gemini/gemini-2.5-pro", "Gemini 2.5 Pro — best quality"),
            ("gemini/gemini-2.5-flash", "Gemini 2.5 Flash — balanced"),
            ("gemini/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — cheapest, benchmark winner"),
        ],
        "stage_b_options": [
            ("gemini/gemini-2.5-pro", "Gemini 2.5 Pro — higher quality"),
            ("gemini/gemini-2.5-flash", "Gemini 2.5 Flash — recommended"),
            ("gemini/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — cheapest"),
        ],
    },
}


PROVIDER_CHOICES = [
    ("openai", "OpenAI (GPT-4o + 4o-mini) — recommended (best value)"),
    ("google", "Google Gemini (2.5 Pro + Flash) — cheapest"),
    ("anthropic", "Claude (Anthropic) — highest quality, expensive"),
]
