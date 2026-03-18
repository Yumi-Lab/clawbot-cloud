"""
WebSocket endpoint for device ↔ cloud bidirectional communication.

Devices connect with their MAC address and maintain a persistent tunnel.
The cloud can push config, commands, and activation status in real time.
"""
import asyncio
import hashlib
import hmac as _hmac
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
        # Pending chat requests awaiting device response: request_id → asyncio.Queue
        self._pending: dict[str, asyncio.Queue] = {}
        # MACs currently processing a tunnel request (prevents recursive routing)
        self._tunneling: set[str] = set()

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

    def register_request(self, request_id: str) -> asyncio.Queue:
        """Create a queue for an in-flight chat_request and return it."""
        q: asyncio.Queue = asyncio.Queue()
        self._pending[request_id] = q
        return q

    def resolve_request(self, request_id: str, item: dict):
        """Deliver a device response chunk/done/error to the waiting HTTP handler."""
        q = self._pending.get(request_id)
        if q:
            q.put_nowait(item)

    def cleanup_request(self, request_id: str):
        self._pending.pop(request_id, None)

    def get_online_mac_for_user(self, user_devices) -> str | None:
        """Return the MAC (no colons, uppercase) of the first online device.

        Anti-recursion is now handled by the _from_tunnel flag in the request
        body (set by the device-side clawbot-cloud script), so we no longer
        block devices that are already processing tunnel requests.  This allows
        multiple parallel sessions to be routed to the same device.
        """
        for device in user_devices:
            if not device.mac:
                continue
            mac_clean = device.mac.replace(":", "").upper()
            if mac_clean in self._connections:
                return mac_clean
        return None

    def start_tunnel(self, mac: str):
        """Mark device as busy handling a tunnel request."""
        self._tunneling.add(mac)

    def end_tunnel(self, mac: str):
        """Release device tunnel lock."""
        self._tunneling.discard(mac)


manager = ConnectionManager()


# ── Helper: compute device IDs from MAC ──────────────────────────────────────

def _compute_id_long(mac_clean: str) -> str:
    """HMAC-SHA256(key='YUMI', msg=mac_clean) → 64-char uppercase hex.
    - device_code = first 12 chars formatted as XXXX-XXXX-XXXX
    - id_long     = full 64-char string (used for secure device identity)
    Collision-safe to ~16M devices on device_code; id_long is practically unique.
    """
    return _hmac.new(b"YUMI", mac_clean.encode(), hashlib.sha256).hexdigest().upper()


# ── Helper: build config payload for a provisioned device ─────────────────────

def _build_config_payload(user: User) -> dict:
    plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])
    return {
        "type": "config",
        "subscription_key": user.sub_key,
        "model": plan_cfg.get("model_ceiling", "claude-haiku-4-5-20251001"),
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
        mac_clean = mac.replace(":", "")
        device = db.query(Device).filter_by(mac=mac).first()
        if not device:
            # Also try legacy device_id lookup, otherwise create
            device = Device(device_id=f"mac-{mac}", mac=mac)
            db.add(device)
            db.commit()
            db.refresh(device)

        # Assign id_long if not yet set (deterministic: same MAC → same id_long)
        if not device.id_long:
            device.id_long = _compute_id_long(mac_clean)

        device.last_seen_at = datetime.utcnow()
        db.commit()

        # Build device_code for welcome message (first 12 chars of id_long)
        h = device.id_long
        device_code = f"{h[:4]}-{h[4:8]}-{h[8:12]}"

        # Determine activation status
        status = "pending"
        welcome = {
            "type": "welcome",
            "status": status,
            "id_long": device.id_long,
            "device_code": device_code,
        }

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

            elif msg_type in ("chat_chunk", "chat_done", "chat_error",
                                "get_response", "get_error"):
                rid = data.get("request_id")
                if rid:
                    manager.resolve_request(rid, data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error for %s: %s", mac, e)
    finally:
        manager.disconnect(mac)
        db.close()
