# Clawbot Cloud

Cloud API for [ClawbotOS](https://github.com/Yumi-Lab/ClawBot-OS) — multi-provider LLM proxy, device provisioning, subscription management, and WebSocket tunnel.

## Features

- **Multi-provider LLM proxy** — routes requests to Kimi, Qwen, Claude, DeepSeek, or OpenAI based on user plan
- **Device tunnel** — bidirectional WebSocket between Pi devices and cloud for remote access
- **Subscription system** — plan-based token quotas with progressive throttle (never hard cutoff)
- **Device provisioning** — QR code activation flow, heartbeat monitoring
- **Voice pipeline** — STT (Whisper/Deepgram) + TTS (Edge-TTS/ElevenLabs)
- **JWT auth** — user registration, login, subscription key management
- **Admin panel** — user/device management, metrics

## Architecture

```
Pi Device                          Cloud (this repo)
┌──────────┐     WebSocket      ┌──────────────────────┐
│ClawbotCore├────────────────────┤  FastAPI :8000        │
│  :8090   │   /ws/{mac}        │                      │
└──────────┘                    │  ├── LLM Proxy       │
                                │  │   Kimi → Qwen →   │
Browser ──── HTTPS ─────────────┤  │   Claude fallback  │
                                │  ├── Device Manager   │
                                │  ├── Voice Pipeline   │
                                │  ├── Auth (JWT)       │
                                │  └── Admin            │
                                └──────────┬───────────┘
                                           │
                                    LLM Providers
                                    Kimi, Qwen, Claude
                                    DeepSeek, OpenAI
```

## API Endpoints

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/auth/register` | User registration |
| POST | `/v1/auth/login` | Login → JWT token |

### Chat (OpenAI-compatible)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/chat/completions` | LLM proxy — routes to best provider |
| POST | `/v1/chat/agents` | Agent mode via device tunnel |
| GET | `/v1/models` | List available models |

### Devices

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/heartbeat` | Device telemetry + auto-registration |
| POST | `/v1/provision` | Push config to device |
| POST | `/v1/activate` | Link device to user account |
| GET | `/ws/{mac}` | WebSocket tunnel |

### Voice

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/stt` | Speech-to-text (Whisper → Deepgram) |
| POST | `/v1/tts` | Text-to-speech (Edge-TTS → ElevenLabs) |
| POST | `/v1/voice` | Unified voice pipeline (STT → LLM → TTS) |

## Provider Routing

Requests are routed based on the user's subscription plan. First provider with an available API key wins.

| Plan | Priority 1 | Priority 2 | Priority 3 |
|------|-----------|-----------|-----------|
| Free | Qwen Flash | Kimi | Claude Haiku |
| Starter | Kimi | Qwen Plus | Claude Sonnet |
| Plus | Kimi | Qwen Max | Claude Sonnet |
| Geek | Kimi | Qwen Coder+ | Claude Opus |
| Pro | Kimi | DeepSeek | Claude Opus |

### Throttle

When daily token quota is exceeded, requests are **slowed down** (20s delay) and routed to the cheapest model (`kimi-for-coding`). Service is never cut off.

### Model Ceiling

Each plan has a maximum model tier. Users can select any model up to their ceiling:
- **Tier 0:** qwen3.5-flash, kimi-for-coding, claude-haiku
- **Tier 1:** qwen3.5-plus, qwen3-max, deepseek-chat, claude-sonnet
- **Tier 2:** claude-opus

## Tech Stack

- **Framework:** FastAPI + Uvicorn
- **Database:** SQLAlchemy (SQLite or PostgreSQL)
- **Auth:** JWT (python-jose), bcrypt
- **WebSocket:** native FastAPI WebSocket with heartbeat
- **Voice:** edge-tts, Whisper API, Deepgram, ElevenLabs

## Installation

### One-liner (Debian server, as root)

```bash
curl -fsSL https://raw.githubusercontent.com/Yumi-Lab/clawbot-cloud/main/install.sh | bash
```

### What install.sh does

1. Installs system packages: `python3`, `python3-pip`, `python3-venv`, `git`, `nginx`, `curl`
2. Clones the repo to `/opt/clawbot-cloud`
3. Creates Python venv + installs pip dependencies
4. Creates data dir at `/var/lib/clawbot-cloud/`
5. Generates `.env` with a random `SECRET_KEY`
6. Sets up systemd service: `clawbot-cloud`
7. Configures nginx as reverse proxy

### Dependencies

| Type | Packages |
|------|----------|
| **System** | python3, python3-pip, python3-venv, git, nginx, curl |
| **Python (pip)** | fastapi, uvicorn, sqlalchemy, python-jose, bcrypt, pydantic, python-multipart, aiofiles, httpx, edge-tts |

### Docker (alternative)

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

Stack: FastAPI + PostgreSQL 16 + Nginx + Certbot (Let's Encrypt).

### Manual

```bash
git clone https://github.com/Yumi-Lab/clawbot-cloud.git
cd clawbot-cloud
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your keys
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | SQLAlchemy connection string |
| `SECRET_KEY` | Yes | JWT signing key |
| `MOONSHOT_API_KEY` | Yes | Kimi API key |
| `DASHSCOPE_API_KEY` | Recommended | Qwen API key |
| `ANTHROPIC_API_KEY` | Optional | Claude API key |
| `DEEPSEEK_API_KEY` | Optional | DeepSeek API key |
| `OPENAI_API_KEY` | Optional | OpenAI API key |
| `ADMIN_SECRET` | Yes | Admin panel auth header |

## Related Repositories

| Repo | Description |
|------|-------------|
| [Yumi-Lab/ClawBot-OS](https://github.com/Yumi-Lab/ClawBot-OS) | ClawbotOS — full OS image build |
| [Yumi-Lab/clawbot-core](https://github.com/Yumi-Lab/clawbot-core) | ClawbotCore — on-device AI orchestrator |
| [Yumi-Lab/ClawbotCore-WebUI](https://github.com/Yumi-Lab/ClawbotCore-WebUI) | Web dashboard |

## License

BUSL-1.1 — See [LICENSE](LICENSE)
Change date 2036-03-02 → Apache 2.0
