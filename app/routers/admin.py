"""
Admin routes — protected by ADMIN_SECRET env var header X-Admin-Secret

GET  /v1/admin/stats    — global platform stats
GET  /v1/admin/users    — list all users
GET  /v1/admin/devices  — list all devices
"""
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import PLAN_LIMITS
from app.database import get_db
from app.models import Device, User

router = APIRouter(prefix="/v1/admin", tags=["admin"])

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin-change-me")


def require_admin(x_admin_secret: str = Header(...)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(401, "Invalid admin secret")


@router.get("/stats")
def stats(db: Session = Depends(get_db), _=Depends(require_admin)):
    total_users = db.query(User).count()
    active_subs = db.query(User).filter_by(sub_active=True).count()
    total_devices = db.query(Device).count()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    online_devices = db.query(Device).filter(Device.last_seen_at >= cutoff).count()
    tokens_today = db.query(func.sum(User.tokens_used_today)).scalar() or 0
    return {
        "total_users": total_users,
        "active_subs": active_subs,
        "total_devices": total_devices,
        "online_devices": online_devices,
        "tokens_today": tokens_today,
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        plan_cfg = PLAN_LIMITS.get(u.plan, PLAN_LIMITS["particulier"])
        result.append({
            "id": str(u.id),
            "email": u.email,
            "plan": u.plan,
            "sub_active": u.sub_active,
            "sub_key": u.sub_key,
            "tokens_used_today": u.tokens_used_today or 0,
            "tokens_per_day": plan_cfg["tokens_per_day"],
            "device_count": len(u.devices),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return {"users": result}


@router.get("/devices")
def list_devices(db: Session = Depends(get_db), _=Depends(require_admin)):
    devices = db.query(Device).order_by(Device.last_seen_at.desc()).all()
    result = []
    for d in devices:
        owner_email = d.owner.email if d.owner else None
        result.append({
            "device_id": d.device_id,
            "owner": owner_email,
            "board": d.board,
            "firmware": d.firmware,
            "last_ip": d.last_ip,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "provisioned": d.provisioned,
            "picoclaw_status": d.picoclaw_status,
            "core_status": d.core_status,
        })
    return {"devices": result}
