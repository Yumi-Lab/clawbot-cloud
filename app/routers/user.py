"""
GET /v1/user/me — current authenticated user info
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import PLAN_LIMITS
from app.database import get_db
from app.models import User
from app.routers.devices import current_user

router = APIRouter(prefix="/v1/user", tags=["user"])


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    plan_cfg = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["particulier"])
    return {
        "id": str(user.id),
        "email": user.email,
        "plan": user.plan,
        "sub_key": user.sub_key,
        "sub_active": user.sub_active,
        "tokens_used_today": user.tokens_used_today or 0,
        "tokens_per_day": plan_cfg["tokens_per_day"],
        "model": plan_cfg["model"],
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
