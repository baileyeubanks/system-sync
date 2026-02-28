import time
from typing import Optional

from fastapi import Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import get_db
from app.services.auth_service import decode_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """Require a valid JWT. Returns user dict."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db = await get_db()
    try:
        row = await db.execute(
            "SELECT id, email, name, role, avatar_url, created_at FROM users WHERE id = ?",
            (payload["sub"],)
        )
        user = await row.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return dict(user)
    finally:
        await db.close()


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """Return user if authenticated, None otherwise."""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


async def require_admin(user: dict = Depends(get_current_user)):
    """Require admin role."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
