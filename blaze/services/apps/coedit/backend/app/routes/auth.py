import time
from fastapi import APIRouter, HTTPException, Depends

from app.database import get_db
from app.models.auth import UserCreate, UserLogin, UserResponse, TokenResponse
from app.services.auth_service import (
    hash_password, verify_password, create_access_token, generate_id
)
from app.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin):
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT id, email, name, password_hash, role, avatar_url, created_at FROM users WHERE email = ?",
            (data.email,)
        )
        user = await row.fetchone()
        if not user or not verify_password(data.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = create_access_token(user["id"], user["role"])
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user["id"],
                email=user["email"],
                name=user["name"],
                role=user["role"],
                avatar_url=user["avatar_url"],
                created_at=user["created_at"],
            )
        )
    finally:
        await db.close()


@router.post("/register", response_model=UserResponse)
async def register(data: UserCreate, admin: dict = Depends(require_admin)):
    db = await get_db()
    try:
        existing = await db.execute("SELECT id FROM users WHERE email = ?", (data.email,))
        if await existing.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        now = time.time()
        user_id = generate_id()
        await db.execute(
            "INSERT INTO users (id, email, name, password_hash, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, data.email, data.name, hash_password(data.password), data.role, now, now)
        )
        await db.commit()
        return UserResponse(
            id=user_id, email=data.email, name=data.name, role=data.role, created_at=now
        )
    finally:
        await db.close()


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)):
    return UserResponse(**user)


@router.post("/setup", response_model=TokenResponse)
async def setup(data: UserCreate):
    """First-time setup: create admin user if no users exist."""
    db = await get_db()
    try:
        row = await db.execute("SELECT COUNT(*) as cnt FROM users")
        count = (await row.fetchone())["cnt"]
        if count > 0:
            raise HTTPException(status_code=403, detail="Setup already completed")

        now = time.time()
        user_id = generate_id()
        await db.execute(
            "INSERT INTO users (id, email, name, password_hash, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, data.email, data.name, hash_password(data.password), "admin", now, now)
        )
        await db.commit()

        token = create_access_token(user_id, "admin")
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user_id, email=data.email, name=data.name, role="admin", created_at=now
            )
        )
    finally:
        await db.close()
