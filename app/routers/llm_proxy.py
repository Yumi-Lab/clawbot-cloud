"""
POST /v1/chat/completions — OpenAI-compatible LLM proxy

Validates subscription key → checks rate limit → forwards to Anthropic → returns response.
"""
import asyncio
import json
import os
import time
import urllib.error
import urllib.request
import uuid as _uuid
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


# ── Device tunnel routing ──────────────────────────────────────────────────────

async def _route_via_device(mac: str, body: dict):
    """Forward a chat request through the WebSocket tunnel to the user's device.

    The device runs the request through its local ClawbotCore (with system tools),
    then sends back the response via chat_done. We stream it as SSE to the browser.
    """
    from app.routers.ws import manager  # lazy import — avoids circular at module load

    request_id = str(_uuid.uuid4())
    q = manager.register_request(request_id)

    ok = await manager.send_to(mac, {
        "type": "chat_request",
        "request_id": request_id,
        "payload": body,
    })
    if not ok:
        manager.cleanup_request(request_id)
        raise HTTPException(502, "Device is offline")

    # Mark device busy — prevents ClawbotCore's LLM callbacks from re-routing to device
    manager.start_tunnel(mac)

    async def event_gen():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    payload = json.dumps({"type": "done", "content": "⚠ Device timeout (300s)"})
                    yield f"event: done\ndata: {payload}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return

                mtype = msg.get("type")
                if mtype == "chat_done":
                    content = msg.get("content", "")
                    payload = json.dumps({"type": "done", "content": content})
                    yield f"event: done\ndata: {payload}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return
                elif mtype == "chat_error":
                    err = msg.get("error", "Unknown device error")
                    payload = json.dumps({"type": "done", "content": f"⚠ {err}"})
                    yield f"event: done\ndata: {payload}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return
                elif mtype == "chat_chunk":
                    # Forward raw SSE line (future streaming support)
                    data = msg.get("data", "")
                    if data:
                        yield (data + "\n").encode()
        finally:
            manager.cleanup_request(request_id)
            manager.end_tunnel(mac)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

        # Route through device if one is online for this user
        from app.routers.ws import manager as _mgr
        device_mac = _mgr.get_online_mac_for_user(user.devices)
        if device_mac:
            return await _route_via_device(device_mac, body)

        # No device online — fall through to direct Anthropic call
        # Cap model to plan ceiling
        from app.config import MODEL_HIERARCHY
        ceiling = plan_cfg.get("model_ceiling", "claude-haiku-4-5-20251001")
        requested = body.get("model", ceiling)
        ceiling_idx = MODEL_HIERARCHY.index(ceiling) if ceiling in MODEL_HIERARCHY else 0
        if requested in MODEL_HIERARCHY:
            requested_idx = MODEL_HIERARCHY.index(requested)
            body["model"] = requested if requested_idx <= ceiling_idx else ceiling
        else:
            body["model"] = ceiling  # unknown model → use plan ceiling

        streaming = body.get("stream", False)

        upstream_headers = {
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }

        anthropic_body = _openai_to_anthropic(body)
        payload = json.dumps(anthropic_body).encode()
        req = urllib.request.Request(
            f"{ANTHROPIC_BASE_URL}/messages",
            data=payload,
            headers=upstream_headers,
            method="POST",
        )

        if streaming:
            # Pre-check: refuse if already over limit (rough guard before stream starts)
            limit = plan_cfg["tokens_per_day"]
            if user.tokens_used_today >= limit:
                raise HTTPException(429, f"Daily token limit reached ({limit} tokens/day)")

            # Capture sub_key for post-stream token recording (db will be closed by then)
            _sub_key = sub_key
            _payload = payload
            _upstream_headers = upstream_headers

            def stream_gen():
                """Passthrough SSE stream; parse Anthropic usage events to count tokens."""
                input_tokens = 0
                output_tokens = 0
                # Retry once on 429 (TPM rate limit) with backoff
                for attempt in range(2):
                    try:
                        _req = urllib.request.Request(
                            f"{ANTHROPIC_BASE_URL}/messages",
                            data=_payload,
                            headers=_upstream_headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(_req, timeout=120) as resp:
                            for raw_line in resp:
                                yield raw_line
                                line = raw_line.decode("utf-8", errors="replace").strip()
                                if not line.startswith("data: ") or line == "data: [DONE]":
                                    continue
                                try:
                                    ev = json.loads(line[6:])
                                    t = ev.get("type", "")
                                    if t == "message_start":
                                        input_tokens = ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
                                    elif t == "message_delta":
                                        output_tokens = ev.get("usage", {}).get("output_tokens", 0)
                                except Exception:
                                    pass
                        break  # success
                    except urllib.error.HTTPError as e:
                        err_body = e.read().decode(errors="replace")
                        if e.code == 429 and attempt == 0:
                            time.sleep(60)  # wait 1 min then retry
                            continue
                        yield f"data: {{\"error\": {{\"code\": {e.code}, \"message\": \"{err_body[:200].replace(chr(34), chr(39))}\" }} }}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return
                    except Exception as e:
                        yield f"data: {{\"error\": {{\"message\": \"{e}\" }} }}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return

                # Record token usage after stream completes
                _db = SessionLocal()
                try:
                    _user = _get_user_by_sub_key(_sub_key, _db)
                    if _user and (input_tokens + output_tokens) > 0:
                        _reset_daily_tokens_if_needed(_user, _db)
                        _user.tokens_used_today = (_user.tokens_used_today or 0) + input_tokens + output_tokens
                        _db.commit()
                except Exception:
                    pass
                finally:
                    _db.close()

            return StreamingResponse(stream_gen(), media_type="text/event-stream")

        # Non-streaming — retry once on 429 (TPM rate limit)
        data = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                err_body = e.read().decode(errors="replace")
                if e.code == 429 and attempt == 0:
                    time.sleep(60)
                    continue
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


def _openai_to_anthropic(body: dict) -> dict:
    """Convert OpenAI chat.completions request body to Anthropic /messages format."""
    result: dict = {}
    result["model"] = body["model"]
    result["max_tokens"] = body.get("max_tokens", 4096)

    for field in ("temperature", "top_p", "stream"):
        if field in body:
            result[field] = body[field]
    if "stop" in body:
        stops = body["stop"]
        result["stop_sequences"] = stops if isinstance(stops, list) else [stops]

    # Extract system messages → top-level system field
    messages = body.get("messages", [])
    system_parts: list[str] = []
    filtered: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            c = msg.get("content", "")
            if isinstance(c, str):
                system_parts.append(c)
            elif isinstance(c, list):
                system_parts.extend(b.get("text", "") for b in c if b.get("type") == "text")
        else:
            filtered.append(msg)
    if system_parts:
        result["system"] = "\n\n".join(system_parts)

    # Convert messages
    anthropic_msgs: list[dict] = []
    i = 0
    while i < len(filtered):
        msg = filtered[i]
        role = msg.get("role")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role == "assistant":
            blocks: list[dict] = []
            if content:
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
                if text:
                    blocks.append({"type": "text", "text": text})
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    try:
                        inp = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        inp = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"toolu_{i}"),
                        "name": fn.get("name", ""),
                        "input": inp,
                    })
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            anthropic_msgs.append({"role": "assistant", "content": blocks})
            i += 1

        elif role == "tool":
            # Collect consecutive tool results → single user message
            tool_results: list[dict] = []
            while i < len(filtered) and filtered[i].get("role") == "tool":
                m = filtered[i]
                tc_content = m.get("content", "")
                content_blocks = (
                    [{"type": "text", "text": tc_content}]
                    if isinstance(tc_content, str)
                    else tc_content
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": content_blocks,
                })
                i += 1
            anthropic_msgs.append({"role": "user", "content": tool_results})

        else:  # user
            if isinstance(content, str):
                anthropic_msgs.append({"role": "user", "content": content})
            elif isinstance(content, list):
                blocks = [{"type": "text", "text": b.get("text", "")} for b in content if b.get("type") == "text"]
                anthropic_msgs.append({"role": "user", "content": blocks or content})
            else:
                anthropic_msgs.append({"role": "user", "content": str(content or "")})
            i += 1

    result["messages"] = anthropic_msgs

    # Convert tools: OpenAI function schema → Anthropic tool schema
    if body.get("tools"):
        result["tools"] = [
            {
                "name": t["function"].get("name", ""),
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in body["tools"] if t.get("type") == "function"
        ]

    # Convert tool_choice: OpenAI string/dict → Anthropic object
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, str):
            mapping = {"auto": {"type": "auto"}, "none": {"type": "none"}, "required": {"type": "any"}}
            result["tool_choice"] = mapping.get(tc, {"type": "auto"})
        elif isinstance(tc, dict) and tc.get("type") == "function":
            result["tool_choice"] = {"type": "tool", "name": tc.get("function", {}).get("name", "")}
        else:
            result["tool_choice"] = tc

    return result


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
