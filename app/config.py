"""
ClawbotCloud — Subscription plans and rate limits
"""

PLAN_LIMITS = {
    "free": {
        "tokens_per_day": 10_000,
        "model": "claude-haiku-4-5-20251001",
        "upstream_model": "claude-haiku-4-5-20251001",
    },
    "particulier": {
        "tokens_per_day": 200_000,
        "model": "clawbot-smart",
        "upstream_model": "claude-sonnet-4-6",
    },
    "pro": {
        "tokens_per_day": 2_000_000,   # effectively unlimited for normal use
        "model": "clawbot-pro",
        "upstream_model": "claude-opus-4-6",
    },
}

# Upstream LLM provider
ANTHROPIC_API_KEY = None  # loaded from env at runtime
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"

# Activation token TTL
ACTIVATION_TOKEN_TTL_MINUTES = 30
