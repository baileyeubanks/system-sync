from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db

router = APIRouter(prefix="/api/billing")


@router.get("/snapshot")
def billing_snapshot(
    business_unit: str = Query("CC"),
    db=Depends(get_db),
):
    bu = (business_unit or "CC").upper()
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")
    return db.get_billing_snapshot(bu)
