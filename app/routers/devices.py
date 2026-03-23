"""
POST /v1/heartbeat           — device telemetry ping
GET  /v1/provision           — fetch config for a device
POST /v1/activate            — link device to user account (called from Yumi-Lab app)
GET  /v1/devices             — list user's devices (requires JWT)
"""
import asyncio
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import decode_token, generate_activation_token
from app.config import ACTIVATION_TOKEN_TTL_MINUTES, PLAN_LIMITS
from app.database import get_db
from app.models import ActivationToken, Device, User
from app.routers.ws import manager as ws_manager, _build_config_payload

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
    mac: str | None = None
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

    # Store MAC if provided
    if req.mac and not device.mac:
        device.mac = req.mac.upper().strip()

    device.board = req.board or device.board
    device.firmware = req.firmware or device.firmware
    device.last_ip = req.ip or device.last_ip
    device.last_seen_at = datetime.utcnow()
    if req.services:
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
            "model": plan_cfg.get("model_ceiling", "kimi-for-coding"),
            "base_url": "https://clawbot-api.yumi-lab.com/v1",
        },
        modules=[],  # future: push modules from user's plan
    )


# ── Activate (called from Yumi-Lab mobile app after QR scan) ─────────────────

class ActivateRequest(BaseModel):
    device_id: str | None = None
    mac: str | None = None


class ActivateResponse(BaseModel):
    ok: bool
    message: str


@router.post("/v1/activate", response_model=ActivateResponse)
async def activate(req: ActivateRequest, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    if not req.device_id and not req.mac:
        raise HTTPException(400, "device_id or mac required")

    # Normalize MAC to uppercase no-colon format (matches WebSocket storage)
    device = None
    mac = None
    if req.mac:
        mac = req.mac.upper().strip().replace(":", "").replace("-", "")

    if mac:
        device = db.query(Device).filter_by(mac=mac).first()
    if not device and req.device_id:
        device = db.query(Device).filter_by(device_id=req.device_id).first()
    if not device:
        device = Device(
            device_id=req.device_id or f"mac-{mac}",
            mac=mac,
        )
        db.add(device)

    # Store MAC if we have it and device doesn't
    if mac and not device.mac:
        device.mac = mac

    if device.user_id and str(device.user_id) != str(user.id):
        raise HTTPException(409, "Device already linked to another account")

    device.user_id = user.id
    device.provisioned = False
    db.commit()

    # Push config via WebSocket if device is connected
    identifier = device.mac or ""
    if identifier and user.sub_active:
        config_msg = _build_config_payload(user)
        pushed = await ws_manager.send_to(identifier, config_msg)
        if pushed:
            device.provisioned = True
            db.commit()

    label = device.mac or device.device_id
    return ActivateResponse(ok=True, message=f"Device {label} linked to {user.email}")


# ── List devices ──────────────────────────────────────────────────────────────

@router.get("/v1/devices")
def list_devices(db: Session = Depends(get_db), user: User = Depends(current_user)):
    devices = db.query(Device).filter_by(user_id=user.id).all()
    return {
        "devices": [
            {
                "device_id": d.device_id,
                "mac": d.mac,
                "board": d.board,
                "firmware": d.firmware,
                "last_ip": d.last_ip,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "provisioned": d.provisioned,
                "core_status": d.core_status,
            }
            for d in devices
        ]
    }
