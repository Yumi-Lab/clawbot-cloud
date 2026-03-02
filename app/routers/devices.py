"""
POST /v1/heartbeat           — device telemetry ping
GET  /v1/provision           — fetch config for a device
POST /v1/activate            — link device to user account (called from Yumi-Lab app)
GET  /v1/devices             — list user's devices (requires JWT)
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import decode_token, generate_activation_token
from app.config import ACTIVATION_TOKEN_TTL_MINUTES, PLAN_LIMITS
from app.database import get_db
from app.models import ActivationToken, Device, User

router = APIRouter(tags=["devices"])


# ── Dependency: JWT user ──────────────────────────────────────────────────────

def current_user(authorization: str = Header(...), db: Session = Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    uid = decode_token(token)
    if not uid:
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter_by(id=uid).first()
    if not user:
        raise HTTPException(401, "User not found")
    return user


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    device_id: str
    board: str | None = None
    firmware: str | None = None
    ip: str | None = None
    services: dict | None = None


class HeartbeatResponse(BaseModel):
    ok: bool = True
    provision: bool = False


@router.post("/v1/heartbeat", response_model=HeartbeatResponse)
def heartbeat(req: HeartbeatRequest, db: Session = Depends(get_db)):
    device = db.query(Device).filter_by(device_id=req.device_id).first()
    if not device:
        # Auto-register unknown device
        device = Device(device_id=req.device_id)
        db.add(device)

    device.board = req.board or device.board
    device.firmware = req.firmware or device.firmware
    device.last_ip = req.ip or device.last_ip
    device.last_seen_at = datetime.utcnow()
    if req.services:
        device.picoclaw_status = req.services.get("picoclaw")
        device.core_status = req.services.get("clawbot_core")

    db.commit()

    # Tell device to provision if it has an owner but isn't provisioned yet
    should_provision = bool(device.user_id and not device.provisioned)
    return HeartbeatResponse(provision=should_provision)


# ── Provision ─────────────────────────────────────────────────────────────────

class ProvisionResponse(BaseModel):
    provisioned: bool
    config: dict | None = None
    modules: list | None = None


@router.get("/v1/provision", response_model=ProvisionResponse)
def provision(device_id: str, db: Session = Depends(get_db)):
    device = db.query(Device).filter_by(device_id=device_id).first()
    if not device or not device.user_id:
        return ProvisionResponse(provisioned=False)

    user = db.query(User).filter_by(id=device.user_id).first()
    if not user or not user.sub_active:
        return ProvisionResponse(provisioned=False)

    plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])

    # Mark device as provisioned
    device.provisioned = True
    db.commit()

    return ProvisionResponse(
        provisioned=True,
        config={
            "subscription_key": user.sub_key,
            "model": plan_cfg["model"],
            "base_url": "https://api.clawbot.io/v1",
        },
        modules=[],  # future: push modules from user's plan
    )


# ── Activate (called from Yumi-Lab mobile app after QR scan) ─────────────────

class ActivateRequest(BaseModel):
    device_id: str


class ActivateResponse(BaseModel):
    ok: bool
    message: str


@router.post("/v1/activate", response_model=ActivateResponse)
def activate(req: ActivateRequest, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    device = db.query(Device).filter_by(device_id=req.device_id).first()
    if not device:
        device = Device(device_id=req.device_id)
        db.add(device)

    if device.user_id and str(device.user_id) != str(user.id):
        raise HTTPException(409, "Device already linked to another account")

    device.user_id = user.id
    device.provisioned = False  # heartbeat will re-provision
    db.commit()

    return ActivateResponse(ok=True, message=f"Device {req.device_id} linked to {user.email}")


# ── List devices ──────────────────────────────────────────────────────────────

@router.get("/v1/devices")
def list_devices(db: Session = Depends(get_db), user: User = Depends(current_user)):
    devices = db.query(Device).filter_by(user_id=user.id).all()
    return {
        "devices": [
            {
                "device_id": d.device_id,
                "board": d.board,
                "firmware": d.firmware,
                "last_ip": d.last_ip,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "provisioned": d.provisioned,
                "picoclaw_status": d.picoclaw_status,
                "core_status": d.core_status,
            }
            for d in devices
        ]
    }
