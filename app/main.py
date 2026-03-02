"""
ClawbotCloud — FastAPI entry point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app.models import Base
from app.routers import auth, devices, llm_proxy

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


@app.get("/health")
def health():
    return {"ok": True, "service": "clawbot-cloud"}
