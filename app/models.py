"""
ClawbotCloud — SQLAlchemy ORM models
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, Enum
)
from sqlalchemy.types import TypeDecorator, String as SAString


class UUID(TypeDecorator):
    """SQLite-compatible UUID stored as string."""
    impl = SAString(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value) if value else None

    def process_result_value(self, value, dialect):
        return value
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    plan = Column(Enum("free", "particulier", "pro", name="plan_enum"),
                  nullable=False, default="free")
    # Subscription
    sub_key = Column(String(64), unique=True, nullable=True, index=True)
    sub_active = Column(Boolean, default=False)
    sub_expires_at = Column(DateTime, nullable=True)
    # Rate limiting counters (reset daily)
    tokens_used_today = Column(Integer, default=0)
    tokens_reset_at = Column(DateTime, nullable=True)
    last_throttled_at = Column(DateTime, nullable=True)  # last request while over quota
    created_at = Column(DateTime, default=datetime.utcnow)

    devices = relationship("Device", back_populates="owner")


class Device(Base):
    __tablename__ = "devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), unique=True, nullable=False, index=True)
    mac = Column(String(17), unique=True, nullable=True, index=True)
    # HMAC-SHA256("YUMI", mac_clean) hex uppercase — 64 chars
    # Deterministic device identity, safe to ~16M devices on first 12 chars
    id_long = Column(String(64), unique=True, nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    board = Column(String(64), nullable=True)
    firmware = Column(String(32), nullable=True)
    last_ip = Column(String(45), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    # Provisioning
    provisioned = Column(Boolean, default=False)
    # Service status (from heartbeat)
    core_status = Column(String(16), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="devices")


class ActivationToken(Base):
    """Short-lived token used to link a device to a user account via QR code."""
    __tablename__ = "activation_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
