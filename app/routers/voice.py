"""
Voice Pipeline — STT / TTS providers + unified voice endpoint.

POST /v1/stt    — Speech-to-Text (Whisper → Deepgram fallback)
POST /v1/tts    — Text-to-Speech (Edge-TTS → ElevenLabs fallback)
POST /v1/voice  — Unified: STT → LLM → TTS
"""

import asyncio
import json
import logging
import os
import re
import uuid as _uuid
from typing import Optional

import httpx
import edge_tts

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import User
from app.routers.devices import current_user

logger = logging.getLogger("clawbot.voice")
router = APIRouter(tags=["voice"])

# ── Config ────────────────────────────────────────────────────────────────────

WHISPER_API_KEY = os.getenv("WHISPER_API_KEY", os.getenv("OPENAI_API_KEY", ""))
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

DEFAULT_STT_LANGUAGE = os.getenv("DEFAULT_STT_LANGUAGE", "fr")
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE", "fr-FR-DeniseNeural")
TTS_MAX_CHARS = 1500

ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/wav", "audio/ogg", "audio/mp3", "audio/mp4", "audio/mpeg"}
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_AUDIO_BYTES = 1024              # 1 KB
PROVIDER_TIMEOUT = 30.0


# ── STT Providers ─────────────────────────────────────────────────────────────


async def _whisper_transcribe(audio_bytes: bytes, mime: str, language: str = "fr") -> str:
    """Transcribe via OpenAI Whisper API."""
    if not WHISPER_API_KEY:
        raise RuntimeError("WHISPER_API_KEY not configured")

    ext = _mime_to_ext(mime)
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {WHISPER_API_KEY}"},
            files={"file": (f"audio.{ext}", audio_bytes, mime)},
            data={"model": "whisper-1", "response_format": "text", "language": language},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:200]}")
        return resp.text.strip()


async def _deepgram_transcribe(audio_bytes: bytes, mime: str, language: str = "fr") -> str:
    """Transcribe via Deepgram API."""
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not configured")

    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": mime,
            },
            params={"model": "nova-3", "detect_language": "true", "language": language},
            content=audio_bytes,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        transcript = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
        return transcript.strip()


async def transcribe(audio_bytes: bytes, mime: str, language: str = "fr") -> str:
    """STT with fallback chain: Whisper → Deepgram."""
    errors = []
    for provider_fn, name in [(_whisper_transcribe, "Whisper"), (_deepgram_transcribe, "Deepgram")]:
        try:
            text = await provider_fn(audio_bytes, mime, language)
            if text:
                logger.info("STT OK via %s (%d chars)", name, len(text))
                return text
            errors.append(f"{name}: empty transcript")
        except Exception as e:
            logger.warning("STT %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise HTTPException(503, f"All STT providers failed: {'; '.join(errors)}")


# ── TTS Providers ─────────────────────────────────────────────────────────────


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting for natural TTS output."""
    text = re.sub(r"```[\s\S]*?```", "", text)   # code blocks
    text = re.sub(r"`[^`]+`", "", text)           # inline code
    text = re.sub(r"#{1,6}\s*", "", text)         # headers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)    # italic
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)  # blockquotes
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)  # list markers
    text = re.sub(r"\n{3,}", "\n\n", text)        # excess newlines
    return text.strip()


def _prepare_tts_text(text: str) -> str:
    """Strip markdown and truncate for TTS."""
    text = _strip_markdown(text)
    if len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS - 3] + "..."
    return text


async def _edge_tts_synthesize(text: str, voice: str, language: str = "fr") -> bytes:
    """Synthesize via Edge-TTS (free, Microsoft)."""
    communicate = edge_tts.Communicate(text, voice=voice)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        raise RuntimeError("Edge-TTS returned no audio")
    return b"".join(chunks)


async def _elevenlabs_synthesize(text: str, voice: str, language: str = "fr") -> bytes:
    """Synthesize via ElevenLabs API."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not configured")

    # ElevenLabs expects a voice_id; default to a multilingual voice
    voice_id = voice if len(voice) == 20 else "21m00Tcm4TlvDq8ikWAM"  # Rachel
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs {resp.status_code}: {resp.text[:200]}")
        return resp.content


async def synthesize(text: str, voice: str = "", language: str = "fr") -> bytes:
    """TTS with fallback chain: Edge-TTS → ElevenLabs."""
    text = _prepare_tts_text(text)
    if not text:
        raise HTTPException(400, "No text to synthesize")

    voice = voice or DEFAULT_TTS_VOICE
    errors = []
    for provider_fn, name in [(_edge_tts_synthesize, "Edge-TTS"), (_elevenlabs_synthesize, "ElevenLabs")]:
        try:
            audio = await provider_fn(text, voice, language)
            if audio:
                logger.info("TTS OK via %s (%d bytes)", name, len(audio))
                return audio
            errors.append(f"{name}: empty audio")
        except Exception as e:
            logger.warning("TTS %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise HTTPException(503, f"All TTS providers failed: {'; '.join(errors)}")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mime_to_ext(mime: str) -> str:
    """Convert MIME type to file extension."""
    mapping = {
        "audio/webm": "webm", "audio/wav": "wav", "audio/ogg": "ogg",
        "audio/mp3": "mp3", "audio/mp4": "mp4", "audio/mpeg": "mp3",
    }
    return mapping.get(mime, "webm")


def _validate_audio(audio_bytes: bytes, content_type: str):
    """Validate audio file size and MIME type."""
    mime = content_type.split(";")[0].strip().lower() if content_type else ""
    if mime not in ALLOWED_AUDIO_MIMES:
        raise HTTPException(400, f"Unsupported audio format: {mime}. Allowed: {', '.join(sorted(ALLOWED_AUDIO_MIMES))}")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(400, f"Audio too large: {len(audio_bytes)} bytes (max {MAX_AUDIO_BYTES})")
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        raise HTTPException(400, f"Audio too small: {len(audio_bytes)} bytes (min {MIN_AUDIO_BYTES})")
    return mime


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/v1/stt")
async def stt_endpoint(
    audio: UploadFile = File(...),
    language: str = Form(DEFAULT_STT_LANGUAGE),
    user: User = Depends(current_user),
):
    """Speech-to-Text endpoint."""
    audio_bytes = await audio.read()
    mime = _validate_audio(audio_bytes, audio.content_type)
    text = await transcribe(audio_bytes, mime, language)
    return {"text": text, "language": language}


@router.post("/v1/tts")
async def tts_endpoint(
    text: str = Form(...),
    voice: str = Form(""),
    language: str = Form("fr"),
    user: User = Depends(current_user),
):
    """Text-to-Speech endpoint."""
    audio = await synthesize(text, voice, language)
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/v1/voice")
async def voice_endpoint(
    audio: UploadFile = File(...),
    session_id: str = Form(""),
    language: str = Form(DEFAULT_STT_LANGUAGE),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Unified voice pipeline: STT → LLM (via device tunnel) → TTS.

    Returns MP3 audio with transcript/response in headers.
    """
    # 1. Validate & read audio
    audio_bytes = await audio.read()
    mime = _validate_audio(audio_bytes, audio.content_type)

    # 2. STT
    transcript = await transcribe(audio_bytes, mime, language)
    logger.info("Voice transcript: %s", transcript[:100])

    # 3. Forward to LLM via device tunnel (non-streaming)
    if not session_id:
        session_id = str(_uuid.uuid4())

    llm_body = {
        "messages": [{"role": "user", "content": transcript}],
        "channel": "voice",
        "session_id": session_id,
        "stream": False,
    }

    # Route: try device tunnel first, fallback to direct Anthropic
    from app.routers.ws import manager as _mgr
    device_mac = _mgr.get_online_mac_for_user(user.devices)

    if device_mac:
        from app.routers.llm_proxy import _route_via_device
        llm_response = await _route_via_device(device_mac, llm_body, streaming=False)
        # _route_via_device returns a JSONResponse — extract content
        resp_data = json.loads(llm_response.body.decode())
        response_text = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
    else:
        # Direct LLM call — import the internal helper
        response_text = await _direct_llm_call(user, llm_body, db)

    if not response_text:
        response_text = "Désolé, je n'ai pas pu générer de réponse."

    logger.info("Voice LLM response: %s", response_text[:100])

    # 4. TTS
    tts_audio = await synthesize(response_text, language=language)

    return Response(
        content=tts_audio,
        media_type="audio/mpeg",
        headers={
            "X-Transcript": transcript[:500],
            "X-Response-Text": response_text[:2000],
            "X-Session-Id": session_id,
        },
    )


async def _direct_llm_call(user: User, body: dict, db: Session) -> str:
    """Direct LLM call when no device is online (mirrors llm_proxy logic)."""
    from app.config import PLAN_LIMITS, PLAN_ROUTING, PROVIDER_KEYS, PROVIDER_URLS

    plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])
    routing = PLAN_ROUTING.get(user.plan, PLAN_ROUTING["free"])
    selected = next((r for r in routing if PROVIDER_KEYS.get(r["provider"])), routing[0])
    provider = selected["provider"]
    model = selected["model"]

    api_key = PROVIDER_KEYS.get(provider, "")
    base_url = PROVIDER_URLS.get(provider, "")
    if not api_key:
        raise HTTPException(503, f"No API key for provider {provider}")

    messages = body.get("messages", [])

    if provider == "anthropic":
        # Anthropic Messages API
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": messages,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                logger.error("Anthropic error %d: %s", resp.status_code, resp.text[:300])
                raise HTTPException(502, "LLM provider error")
            data = resp.json()
            # Extract text from content blocks
            content_blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
    else:
        # OpenAI-compatible API
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                logger.error("LLM error %d: %s", resp.status_code, resp.text[:300])
                raise HTTPException(502, "LLM provider error")
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
