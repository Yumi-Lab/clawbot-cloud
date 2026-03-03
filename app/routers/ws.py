"""
WebSocket endpoint for device ↔ cloud bidirectional communication.

Devices connect with their MAC address and maintain a persistent tunnel.
The cloud can push config, commands, and activation status in real time.
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

from app.config import PLAN_LIMITS
from app.database import get_db
from app.models import Device, User

logger = logging.getLogger("clawbot.ws")
router = APIRouter(tags=["websocket"])


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """Tracks active WebSocket connections keyed by MAC address."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, mac: str, ws: WebSocket):
        await ws.accept()
        old = self._connections.pop(mac, None)
        if old:
            try:
                await old.close(1000, "replaced")
            except Exception:
                pass
        self._connections[mac] = ws
        logger.info("Device connected: %s (total: %d)", mac, len(self._connections))

    def disconnect(self, mac: str):
        self._connections.pop(mac, None)
        logger.info("Device disconnected: %s (total: %d)", mac, len(self._connections))

    def get(self, mac: str) -> WebSocket | None:
        return self._connections.get(mac)

    async def send_to(self, mac: str, message: dict) -> bool:
        ws = self._connections.get(mac)
        if not ws:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            self.disconnect(mac)
            return False


manager = ConnectionManager()


# ── Helper: build config payload for a provisioned device ─────────────────────

def _build_config_payload(user: User) -> dict:
    plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])
    return {
        "type": "config",
        "subscription_key": user.sub_key,
        "model": plan_cfg["model"],
        "base_url": "https://clawbot-api.yumi-lab.com/v1",
    }


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@router.websocket("/v1/ws")
async def device_ws(websocket: WebSocket, mac: str | None = None):
    if not mac:
        await websocket.close(1008, "mac query parameter required")
        return

    mac = mac.upper().strip()
    await manager.connect(mac, websocket)

    db: Session = next(get_db())
    try:
        # Upsert device by MAC
        device = db.query(Device).filter_by(mac=mac).first()
        if not device:
            # Also try legacy device_id lookup, otherwise create
            device = Device(device_id=f"mac-{mac}", mac=mac)
            db.add(device)
            db.commit()
            db.refresh(device)

        device.last_seen_at = datetime.utcnow()
        db.commit()

        # Determine activation status
        status = "pending"
        welcome = {"type": "welcome", "status": status}

        if device.user_id:
            user = db.query(User).filter_by(id=device.user_id).first()
            if user and user.sub_active:
                status = "activated"
                welcome = {"type": "welcome", "status": status}
                # Push config immediately
                config_msg = _build_config_payload(user)
                await websocket.send_json(welcome)
                await websocket.send_json(config_msg)
                # Mark provisioned
                device.provisioned = True
                db.commit()
            else:
                await websocket.send_json(welcome)
        else:
            await websocket.send_json(welcome)

        # ── Message loop ──────────────────────────────────────────────────
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "heartbeat":
                device.board = data.get("board") or device.board
                device.firmware = data.get("firmware") or device.firmware
                device.last_ip = data.get("ip") or device.last_ip
                device.last_seen_at = datetime.utcnow()
                services = data.get("services", {})
                if services:
                    device.picoclaw_status = services.get("picoclaw")
                    device.core_status = services.get("clawbot_core")
                db.commit()

                # Check if device was activated since last heartbeat
                if device.user_id and not device.provisioned:
                    db.refresh(device)
                    user = db.query(User).filter_by(id=device.user_id).first()
                    if user and user.sub_active:
                        await websocket.send_json(_build_config_payload(user))
                        device.provisioned = True
                        db.commit()

                await websocket.send_json({"type": "heartbeat_ack"})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error for %s: %s", mac, e)
    finally:
        manager.disconnect(mac)
        db.close()
