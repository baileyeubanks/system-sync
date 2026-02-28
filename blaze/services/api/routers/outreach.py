from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db
from api.models import OutreachDraftRequest

router = APIRouter(prefix="/api/outreach")


@router.get("/drafts")
def list_drafts(
    business_unit: str = Query("CC"),
    status: str = Query(""),
    limit: int = Query(50),
    db=Depends(get_db),
):
    bu = (business_unit or "CC").upper()
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")
    status_val = status.strip() or None
    clamped_limit = max(1, min(limit, 200))
    return {
        "business_unit": bu,
        "results": db.list_outreach_drafts(
            business_unit=bu, status=status_val, limit=clamped_limit
        ),
    }


@router.post("/drafts/propose")
def propose_draft(body: OutreachDraftRequest, db=Depends(get_db)):
    bu = (body.business_unit or "CC").upper()
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")
    channel = (body.channel or "").strip()
    recipient = (body.recipient or "").strip()
    body_text = (body.body_text or "").strip()
    if not channel or not recipient or not body_text:
        raise HTTPException(status_code=400, detail="channel, recipient, and body_text are required")
    source_ids = body.source_insight_ids or []
    if not isinstance(source_ids, list):
        raise HTTPException(status_code=400, detail="source_insight_ids must be a list")
    normalized: list[int] = []
    for raw_id in source_ids:
        try:
            normalized.append(int(raw_id))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="source_insight_ids must contain integers")
    result = db.create_outreach_draft(
        business_unit=bu,
        channel=channel,
        recipient=recipient,
        body_text=body_text,
        subject=(body.subject or "").strip() or None,
        rationale=(body.rationale or "").strip() or None,
        contact_id=int(body.contact_id) if body.contact_id else None,
        source_insight_ids=normalized,
    )
    return {"ok": True, "business_unit": bu, **result}
