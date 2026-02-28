import time
import uuid
import secrets
import hashlib
import os
from typing import Optional

from jose import jwt, JWTError

from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_HOURS, SHARE_TOKEN_EXPIRE_DAYS


def hash_password(password: str) -> str:
    """Hash password using SHA-256 + salt (no bcrypt dependency issues)."""
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return "{}:{}".format(salt, hashed)


def verify_password(plain: str, stored: str) -> bool:
    """Verify password against stored hash."""
    if ":" not in stored:
        return False
    salt, hashed = stored.split(":", 1)
    return hashlib.sha256((salt + plain).encode()).hexdigest() == hashed


def create_access_token(user_id: str, role: str) -> str:
    expire = time.time() + ACCESS_TOKEN_EXPIRE_HOURS * 3600
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_share_token(share_link_id: str, mode: str, expires_days: Optional[int] = None) -> str:
    days = expires_days or SHARE_TOKEN_EXPIRE_DAYS
    expire = time.time() + days * 86400
    payload = {
        "sub": share_link_id,
        "mode": mode,
        "exp": expire,
        "type": "share",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def generate_id() -> str:
    return uuid.uuid4().hex[:16]


def generate_share_token() -> str:
    return secrets.token_urlsafe(32)
