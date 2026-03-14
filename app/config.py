"""
ClawbotCloud — Subscription plans and rate limits
"""
import os

# Model hierarchy (ascending capability/cost) — used for ceiling enforcement
MODEL_HIERARCHY = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

PLAN_LIMITS = {
    "free": {
        "tokens_per_day": 10_000,
        "model_ceiling": "claude-haiku-4-5-20251001",
    },
    "particulier": {
        "tokens_per_day": 200_000,
        "model_ceiling": "claude-sonnet-4-6",
    },
    "pro": {
        "tokens_per_day": 2_000_000,
        "model_ceiling": "claude-opus-4-6",
    },
}

# ── Multi-provider routing ────────────────────────────────────────────────────
# Pi is always blind to provider — it just uses its sub_key → cloud picks provider.

PROVIDER_KEYS = {
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "moonshot":  os.getenv("MOONSHOT_API_KEY", ""),   # Kimi (api.moonshot.cn)
    "deepseek":  os.getenv("DEEPSEEK_API_KEY", ""),
    "openai":    os.getenv("OPENAI_API_KEY", ""),
}

PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com/v1",
    "moonshot":  "https://api.moonshot.cn/v1",
    "deepseek":  "https://api.deepseek.com/v1",
    "openai":    "https://api.openai.com/v1",
}

# Ordered list per plan — first provider with a key available wins.
# Fallback: next in list. If none available, raises error.
PLAN_ROUTING = {
    "free": [
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    ],
    "particulier": [
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    ],
    "pro": [
        {"provider": "anthropic", "model": "claude-opus-4-6"},
    ],
    # Future examples:
    # "free": [
    #     {"provider": "moonshot",  "model": "moonshot-v1-8k"},
    #     {"provider": "deepseek",  "model": "deepseek-chat"},
    #     {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    # ],
}

# Upstream LLM provider (legacy — kept for llm_proxy backward compat)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"

# Activation token TTL
ACTIVATION_TOKEN_TTL_MINUTES = 30
