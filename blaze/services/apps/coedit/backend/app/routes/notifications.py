import time
from typing import List
from fastapi import APIRouter, Depends

from app.database import get_db
from app.dependencies import get_current_user
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: str
    type: str
    message: str
    asset_id: Optional[str] = None
    reference_id: Optional[str] = None
    is_read: bool
    created_at: float


@router.get("/notifications", response_model=List[NotificationResponse])
async def list_notifications(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await db.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],)
        )
        notifs = await rows.fetchall()
        return [
            NotificationResponse(
                id=n["id"], type=n["type"], message=n["message"],
                asset_id=n["asset_id"], reference_id=n["reference_id"],
                is_read=bool(n["is_read"]), created_at=n["created_at"]
            )
            for n in notifs
        ]
    finally:
        await db.close()


@router.get("/notifications/unread-count")
async def unread_count(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],)
        )
        result = await row.fetchone()
        return {"count": result["cnt"]}
    finally:
        await db.close()


@router.post("/notifications/mark-read")
async def mark_all_read(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (user["id"],)
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.post("/notifications/{notif_id}/read")
async def mark_read(notif_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notif_id, user["id"])
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()
