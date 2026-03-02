"""
POST /v1/chat/completions — OpenAI-compatible LLM proxy

Validates subscription key → checks rate limit → forwards to Anthropic → returns response.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, date

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import PLAN_LIMITS
from app.database import SessionLocal
from app.models import User

router = APIRouter(tags=["llm"])

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")


def _get_user_by_sub_key(sub_key: str, db: Session) -> User | None:
    return db.query(User).filter_by(sub_key=sub_key, sub_active=True).first()


def _reset_daily_tokens_if_needed(user: User, db: Session):
    today = date.today()
    if not user.tokens_reset_at or user.tokens_reset_at.date() < today:
        user.tokens_used_today = 0
        user.tokens_reset_at = datetime.utcnow()
        db.commit()


def _check_and_record_tokens(user: User, tokens_used: int, db: Session):
    limit = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])["tokens_per_day"]
    if user.tokens_used_today + tokens_used > limit:
        raise HTTPException(429, f"Daily token limit reached ({limit} tokens/day for plan '{user.plan}')")
    user.tokens_used_today += tokens_used
    db.commit()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(...)):
    # --- Auth ---
    sub_key = authorization.removeprefix("Bearer ").strip()
    db = SessionLocal()
    try:
        user = _get_user_by_sub_key(sub_key, db)
        if not user:
            raise HTTPException(401, "Invalid or inactive subscription key")

        _reset_daily_tokens_if_needed(user, db)

        plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])

        # --- Build upstream request ---
        body = await request.json()
        # Override model with plan-allowed model
        body["model"] = plan_cfg["upstream_model"]
        # Anthropic requires max_tokens
        body.setdefault("max_tokens", 4096)

        streaming = body.get("stream", False)

        upstream_headers = {
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{ANTHROPIC_BASE_URL}/messages",
            data=payload,
            headers=upstream_headers,
            method="POST",
        )

        if streaming:
            # Streaming passthrough
            def stream_gen():
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        for chunk in resp:
                            yield chunk
                except Exception as e:
                    yield f"data: [ERROR] {e}\n\n".encode()

            return StreamingResponse(stream_gen(), media_type="text/event-stream")

        # Non-streaming
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            raise HTTPException(e.code, err_body[:500])
        except Exception as e:
            raise HTTPException(502, f"Upstream error: {e}")

        # Record token usage
        usage = data.get("usage", {})
        tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        _check_and_record_tokens(user, tokens_used, db)

        # Normalize Anthropic response → OpenAI format
        openai_resp = _to_openai_format(data)
        return JSONResponse(openai_resp)

    finally:
        db.close()


def _to_openai_format(anthropic_resp: dict) -> dict:
    """Convert Anthropic /messages response to OpenAI chat.completions format."""
    content_blocks = anthropic_resp.get("content", [])
    text = " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
    tool_calls = [
        {
            "id": b.get("id", ""),
            "type": "function",
            "function": {
                "name": b.get("name", ""),
                "arguments": json.dumps(b.get("input", {})),
            },
        }
        for b in content_blocks if b.get("type") == "tool_use"
    ]

    stop_reason = anthropic_resp.get("stop_reason", "end_turn")
    finish_reason = "tool_calls" if tool_calls else \
                    "stop" if stop_reason == "end_turn" else stop_reason

    message: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = anthropic_resp.get("usage", {})
    return {
        "id": anthropic_resp.get("id", ""),
        "object": "chat.completion",
        "model": anthropic_resp.get("model", ""),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens":     usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens":      usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }
