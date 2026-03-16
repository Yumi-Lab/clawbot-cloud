"""
OpenJarvis Cloud — Subscription plans and rate limits

Two product lines:
  Perso : free → starter → plus → geek
  Pro   : pro_starter → pro → pro_plus → business

Throttle model: never cut service — degrade speed beyond quota (telecom style).
Legacy DB values: "particulier" → starter
"""
import os

# Model hierarchy (ascending capability/cost) — used for ceiling enforcement
MODEL_HIERARCHY = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

# ── Plan definitions ──────────────────────────────────────────────────────────
# tokens_per_day  : soft quota — beyond this, throttle kicks in (never hard cut)
# model_ceiling   : max model allowed for this plan
# agents_included : number of agent slots included
# speed           : "slow" | "normal" | "fast" | "priority" (informational)
# line            : "perso" | "pro"

PLAN_LIMITS = {
    # ── Perso line ────────────────────────────────────────────────────────────
    "free": {
        "tokens_per_day":  10_000,
        "model_ceiling":   "claude-haiku-4-5-20251001",
        "agents_included": 1,
        "speed":           "slow",
        "line":            "perso",
        "price_eur":       0,
    },
    "starter": {
        "tokens_per_day":  200_000,
        "model_ceiling":   "claude-sonnet-4-6",
        "agents_included": 3,
        "speed":           "normal",
        "line":            "perso",
        "price_eur":       7.99,
    },
    "plus": {
        "tokens_per_day":  600_000,
        "model_ceiling":   "claude-sonnet-4-6",
        "agents_included": 10,
        "speed":           "fast",
        "line":            "perso",
        "price_eur":       29.99,
    },
    "geek": {
        "tokens_per_day":  2_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 15,
        "speed":           "fast",
        "line":            "perso",
        "price_eur":       80,
    },
    # ── Pro line ──────────────────────────────────────────────────────────────
    "pro_starter": {
        "tokens_per_day":  400_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 5,
        "speed":           "fast",
        "line":            "pro",
        "price_eur":       39,
    },
    "pro": {
        "tokens_per_day":  1_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 15,
        "speed":           "priority",
        "line":            "pro",
        "price_eur":       189,
    },
    "pro_plus": {
        "tokens_per_day":  6_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 30,
        "speed":           "priority",
        "line":            "pro",
        "price_eur":       300,
    },
    "business": {
        "tokens_per_day":  20_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 9999,
        "speed":           "priority",
        "line":            "pro",
        "price_eur":       500,
    },
    # ── Legacy aliases (existing DB rows) ─────────────────────────────────────
    "particulier": {
        "tokens_per_day":  200_000,
        "model_ceiling":   "claude-sonnet-4-6",
        "agents_included": 3,
        "speed":           "normal",
        "line":            "perso",
        "price_eur":       7.99,
    },
}

# ── Multi-provider routing ────────────────────────────────────────────────────
# Pi is always blind to provider — it just uses its sub_key → cloud picks provider.

PROVIDER_KEYS = {
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "moonshot":  os.getenv("MOONSHOT_API_KEY", ""),
    "deepseek":  os.getenv("DEEPSEEK_API_KEY", ""),
    "openai":    os.getenv("OPENAI_API_KEY", ""),
}

PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com/v1",
    "moonshot":  "https://api.kimi.com/coding/v1",
    "deepseek":  "https://api.deepseek.com/v1",
    "openai":    "https://api.openai.com/v1",
}

# Ordered list per plan — first provider with a key available wins.
PLAN_ROUTING = {
    "free": [
        {"provider": "moonshot",  "model": "kimi-for-coding"},
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    ],
    "starter":     [{"provider": "anthropic", "model": "claude-sonnet-4-6"}],
    "plus":        [{"provider": "anthropic", "model": "claude-sonnet-4-6"}],
    "geek":        [{"provider": "anthropic", "model": "claude-opus-4-6"}],
    "pro_starter": [{"provider": "anthropic", "model": "claude-opus-4-6"}],
    "pro":         [{"provider": "anthropic", "model": "claude-opus-4-6"}],
    "pro_plus":    [{"provider": "anthropic", "model": "claude-opus-4-6"}],
    "business":    [{"provider": "anthropic", "model": "claude-opus-4-6"}],
    # Legacy
    "particulier": [{"provider": "anthropic", "model": "claude-sonnet-4-6"}],
}

# Upstream LLM provider (legacy — kept for llm_proxy backward compat)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"

# Activation token TTL
ACTIVATION_TOKEN_TTL_MINUTES = 30
