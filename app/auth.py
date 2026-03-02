"""
ClawbotCloud — Auth helpers (password hashing, JWT, subscription key generation)
"""
import os
import secrets
import string
from datetime import datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None


def generate_subscription_key(plan: str) -> str:
    """Generate a unique subscription key. Format: clb-<plan_prefix>-<random>"""
    prefix = {"particulier": "p", "pro": "b"}.get(plan, "x")
    alphabet = string.ascii_lowercase + string.digits
    rand = "".join(secrets.choice(alphabet) for _ in range(32))
    return f"clb-{prefix}-{rand}"


def generate_activation_token() -> str:
    return secrets.token_urlsafe(32)
