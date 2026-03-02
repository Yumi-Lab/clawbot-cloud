"""
POST /v1/auth/register
POST /v1/auth/login
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token, generate_subscription_key,
    hash_password, verify_password
)
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    plan: str = "particulier"  # "particulier" | "pro"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    plan: str
    sub_key: str | None


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if req.plan not in ("particulier", "pro"):
        raise HTTPException(400, "plan must be 'particulier' or 'pro'")

    if db.query(User).filter_by(email=req.email).first():
        raise HTTPException(409, "Email already registered")

    sub_key = generate_subscription_key(req.plan)
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        plan=req.plan,
        sub_key=sub_key,
        sub_active=True,  # activate immediately (no payment gateway yet)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, plan=user.plan, sub_key=sub_key)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, plan=user.plan, sub_key=user.sub_key)
