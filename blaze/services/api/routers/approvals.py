from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_db

router = APIRouter(prefix="/api/approvals")


@router.get("/{approval_id}")
def get_approval(approval_id: int, db=Depends(get_db)):
    approval = db.get_action_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@router.post("/{approval_id}/approve")
def approve(approval_id: int, db=Depends(get_db)):
    updated = db.set_action_approval_state(approval_id, "approved")
    if not updated:
        raise HTTPException(status_code=404, detail="approval not found")
    db.sync_outreach_draft_approval(approval_id=approval_id, approval_state="approved")
    return {"ok": True, "approval": updated}


@router.post("/{approval_id}/reject")
def reject(approval_id: int, db=Depends(get_db)):
    updated = db.set_action_approval_state(approval_id, "rejected")
    if not updated:
        raise HTTPException(status_code=404, detail="approval not found")
    db.sync_outreach_draft_approval(approval_id=approval_id, approval_state="rejected")
    return {"ok": True, "approval": updated}
