"""
ClawbotCloud — FastAPI entry point
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import engine
from app.models import Base
from app.routers import auth, devices, llm_proxy, user, admin, ws, voice

# Create tables (idempotent; use Alembic for migrations in production)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Clawbot Cloud API",
    description="Backend for ClawbotOS device management and LLM proxy",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(llm_proxy.router)
app.include_router(user.router)
app.include_router(admin.router)
app.include_router(ws.router)
app.include_router(voice.router)

# Static assets (CSS, JS, images if any)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── HTML page routes ──────────────────────────────────────────────────────────

@app.get("/")
def index_page():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(os.path.join(_static_dir, "dashboard.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(_static_dir, "admin.html"))



@app.get("/chat")
def chat_page():
    return FileResponse(os.path.join(_static_dir, "chat.html"))

@app.get("/activate")
def activate_page():
    return FileResponse(os.path.join(_static_dir, "activate.html"))


@app.get("/health")
def health():
    return {"ok": True, "service": "clawbot-cloud"}
