from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db, get_imessage

router = APIRouter()


@router.get("/api/imessage/threads/recent")
def recent_threads(
    business_unit: str = Query(""),
    limit: int = Query(50),
    db=Depends(get_db),
):
    bu = business_unit.upper() if business_unit else None
    if bu and bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")
    clamped_limit = max(1, min(limit, 500))
    return {
        "results": db.list_recent_message_threads(
            business_unit=bu, limit=clamped_limit
        )
    }


@router.post("/api/imessage/send/propose")
def propose_send(body: dict, imessage=Depends(get_imessage)):
    result = imessage.propose_send(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/api/imessage/send")
def send_imessage(body: dict, imessage=Depends(get_imessage)):
    result = imessage.send_with_approval(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/api/imessage/notify")
def notify_imessage(body: dict, imessage=Depends(get_imessage)):
    """Direct iMessage send for trusted automation scripts (no approval flow)."""
    result = imessage.direct_send(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result
