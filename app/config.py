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
# Maps every known model to a tier (0 = cheapest, 2 = most capable).
# Unknown models default to tier 0 (safe: never exceeds ceiling).
MODEL_HIERARCHY = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

MODEL_TIER = {
    # Tier 0 — economy / flash
    "qwen3.5-flash":           0,
    "kimi-for-coding":         0,
    "claude-haiku-4-5-20251001": 0,
    # Tier 1 — mid
    "qwen3.5-plus":            1,
    "qwen3-max":               1,
    "qwen3-coder-plus":        1,
    "deepseek-chat":           1,
    "kimi-code-2.5":           1,
    "claude-sonnet-4-6":       1,
    # Tier 2 — top
    "claude-opus-4-6":         2,
}

# Ceiling per plan expressed as max tier
PLAN_CEILING_TIER = {
    "free":        0,
    "starter":     1,
    "plus":        1,
    "geek":        2,
    "pro_starter": 2,
    "pro":         2,
    "pro_plus":    2,
    "business":    2,
    "particulier": 1,
}

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
        "tokens_per_day":  50_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 15,
        "speed":           "priority",
        "line":            "pro",
        "price_eur":       189,
    },
    "pro_plus": {
        "tokens_per_day":  100_000_000,
        "model_ceiling":   "claude-opus-4-6",
        "agents_included": 30,
        "speed":           "priority",
        "line":            "pro",
        "price_eur":       300,
    },
    "business": {
        "tokens_per_day":  200_000_000,
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
    "anthropic":  os.getenv("ANTHROPIC_API_KEY", ""),
    "moonshot":   os.getenv("MOONSHOT_API_KEY", ""),
    "deepseek":   os.getenv("DEEPSEEK_API_KEY", ""),
    "openai":     os.getenv("OPENAI_API_KEY", ""),
    "dashscope":  os.getenv("DASHSCOPE_API_KEY", ""),
}

PROVIDER_URLS = {
    "anthropic":  "https://api.anthropic.com/v1",
    "moonshot":   "https://api.kimi.com/coding/v1",
    "deepseek":   "https://api.deepseek.com/v1",
    "openai":     "https://api.openai.com/v1",
    "dashscope":  "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}

# Ordered list per plan — first provider with a key available wins.
# RULE: moonshot/dashscope ALWAYS first, anthropic ALWAYS last (fallback only).
PLAN_ROUTING = {
    "free": [
        {"provider": "dashscope",  "model": "qwen3.5-flash"},
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "anthropic",  "model": "claude-haiku-4-5-20251001"},
    ],
    "starter": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "dashscope",  "model": "qwen3.5-plus"},
        {"provider": "anthropic",  "model": "claude-sonnet-4-6"},
    ],
    "plus": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "dashscope",  "model": "qwen3-max"},
        {"provider": "anthropic",  "model": "claude-sonnet-4-6"},
    ],
    "geek": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "dashscope",  "model": "qwen3-coder-plus"},
        {"provider": "anthropic",  "model": "claude-opus-4-6"},
    ],
    "pro_starter": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "dashscope",  "model": "qwen3-max"},
        {"provider": "anthropic",  "model": "claude-opus-4-6"},
    ],
    "pro": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "deepseek",   "model": "deepseek-chat"},
        {"provider": "anthropic",  "model": "claude-opus-4-6"},
    ],
    "pro_plus": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "deepseek",   "model": "deepseek-chat"},
        {"provider": "anthropic",  "model": "claude-opus-4-6"},
    ],
    "business": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "deepseek",   "model": "deepseek-chat"},
        {"provider": "anthropic",  "model": "claude-opus-4-6"},
    ],
    # Legacy
    "particulier": [
        {"provider": "moonshot",   "model": "kimi-for-coding"},
        {"provider": "dashscope",  "model": "qwen3.5-plus"},
        {"provider": "anthropic",  "model": "claude-sonnet-4-6"},
    ],
}

# ── Throttle settings ────────────────────────────────────────────────────────
# When user exceeds daily quota: degrade speed, never cut service (telecom model).
THROTTLE_DELAY_SECONDS = 20
THROTTLE_MODEL = {"provider": "moonshot", "model": "kimi-for-coding"}
THROTTLE_FALLBACK = {"provider": "dashscope", "model": "qwen3.5-flash"}

# Upstream LLM provider (legacy — kept for llm_proxy backward compat)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"

# ── Voice pipeline ────────────────────────────────────────────────────────────
WHISPER_API_KEY = os.getenv("WHISPER_API_KEY", os.getenv("OPENAI_API_KEY", ""))
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DEFAULT_STT_LANGUAGE = os.getenv("DEFAULT_STT_LANGUAGE", "fr")
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE", "fr-FR-DeniseNeural")
TTS_MAX_CHARS = 1500

# Activation token TTL
ACTIVATION_TOKEN_TTL_MINUTES = 30
